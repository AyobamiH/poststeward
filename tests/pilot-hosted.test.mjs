import assert from "node:assert/strict";
import test from "node:test";
import { inspectPilot } from "../scripts/pilot-smoke.mjs";
const origin = "https://pilot.example";
function transport(fail = false) {
  return async (url, init) => {
    assert.equal(init.redirect, "manual"); assert.ok(init.signal);
    const u = new URL(url);
    const headers = { "X-Frame-Options": "DENY", "Strict-Transport-Security": "max-age=31536000", "Cache-Control": "no-store" };
    if (u.pathname === "/pilot") return new Response('<input type="password"><input id="approve"><button id="confirm">', { headers: { ...headers, "Content-Type": "text/html" } });
    if (u.pathname === "/auth/login") {
      if (u.searchParams.get("return") !== "/pilot") return new Response("{}", { status: 400, headers });
      const location = new URL("https://accounts.google.com/authorize");
      location.search = new URLSearchParams({ redirect_uri: origin + "/auth/callback", response_type: "code", code_challenge_method: "S256", nonce: "private-nonce", state: "private-state" }).toString();
      return new Response(null, { status: 302, headers: { ...headers, Location: location.href, "Set-Cookie": "__Host-login=private-cookie" } });
    }
    if (u.pathname.startsWith("/api/")) {
      if (fail) throw new Error("secret in upstream error");
      return new Response("{}", { status: init.headers?.Origin === "https://untrusted.example" ? 403 : 401, headers });
    }
    return new Response("asset", { headers });
  };
}
test("twelve hosted pilot surface checks never manufacture live owner/publication evidence or disclose OAuth state", async () => {
  const report = await inspectPilot(origin, transport());
  assert.equal(report.checks.length, 12); assert.equal(report.passed, true);
  assert.equal(report.ownerSignInCompleted, "not tested by deployment");
  assert.equal(report.publication, "not attempted by deployment");
  assert.doesNotMatch(JSON.stringify(report), /private-cookie|private-state|private-nonce/);
});
test("failed pilot admission is fail-closed with redacted diagnostics", async () => {
  const report = await inspectPilot(origin, transport(true));
  assert.equal(report.passed, false); assert.doesNotMatch(JSON.stringify(report), /secret in upstream/);
});

async function inspectWithProbe(path, respond) {
  const send = transport();
  const waits = [];
  let requests = 0;
  const report = await inspectPilot(origin, async (url, init) => {
    if (new URL(url).pathname === path)
      return respond(++requests, url, init, send);
    return send(url, init);
  }, async (ms) => { waits.push(ms); });
  return { report, requests, waits };
}

test("static pilot asset recovers from transient transport failures within the existing retry budget", async () => {
  const { report, requests, waits } = await inspectWithProbe("/pilot.css", async (attempt, url, init, send) => {
    if (attempt < 3) throw new TypeError("private transport detail");
    return send(url, init);
  });
  assert.equal(report.passed, true);
  assert.equal(requests, 3);
  assert.deepEqual(waits, [2000, 2000]);
  assert.deepEqual(report.checks.find((check) => check.name === "acceptance asset /pilot.css"), {
    name: "acceptance asset /pilot.css", status: 200, expected: 200, attempts: 3, passed: true,
  });
  assert.doesNotMatch(JSON.stringify(report), /private transport detail/);
});

test("persistent static asset transport failure exhausts five attempts and remains fail-closed", async () => {
  const { report, requests, waits } = await inspectWithProbe("/pilot.css", async () => {
    throw new TypeError("private transport detail");
  });
  assert.equal(report.passed, false);
  assert.equal(requests, 5);
  assert.deepEqual(waits, [2000, 2000, 2000, 2000]);
  assert.deepEqual(report.checks.filter((check) => !check.passed), [{
    name: "acceptance asset /pilot.css", status: 0, expected: 200, attempts: 5, passed: false,
  }]);
  assert.doesNotMatch(JSON.stringify(report), /private transport detail/);
});

test("fresh static asset 404 still uses the same bounded recovery path", async () => {
  const { report, requests, waits } = await inspectWithProbe("/pilot.css", async (attempt, url, init, send) => {
    return attempt === 1 ? new Response(null, { status: 404 }) : send(url, init);
  });
  assert.equal(report.passed, true);
  assert.equal(requests, 2);
  assert.deepEqual(waits, [2000]);
});

test("static asset authentication denials, redirects and other HTTP failures are never retried", async () => {
  for (const status of [401, 403, 302, 429, 500]) {
    const { report, requests, waits } = await inspectWithProbe("/pilot.css", async () => new Response(null, { status }));
    assert.equal(report.passed, false, String(status));
    assert.equal(requests, 1, String(status));
    assert.deepEqual(waits, [], String(status));
    assert.equal(report.checks.find((check) => check.name === "acceptance asset /pilot.css").status, status);
  }
});

test("failed content and security assertions are never retried, including inspection exceptions", async () => {
  const fixtures = [
    ["/pilot", async (url, init, send) => {
      const response = await send(url, init);
      return new Response("missing approval controls", { headers: response.headers });
    }],
    ["/pilot", async (url, init, send) => {
      const response = await send(url, init);
      response.text = async () => { throw new Error("private content inspection detail"); };
      return response;
    }],
    ...["x-frame-options", "strict-transport-security"].map((header) => [
      "/pilot.css", async (url, init, send) => {
        const response = await send(url, init);
        response.headers.delete(header);
        return response;
      },
    ]),
  ];
  for (const [path, respond] of fixtures) {
    const { report, requests, waits } = await inspectWithProbe(path, async (_attempt, ...args) => respond(...args));
    assert.equal(report.passed, false);
    assert.equal(requests, 1);
    assert.deepEqual(waits, []);
    assert.equal(report.checks.filter((check) => !check.passed).length, 1);
    assert.doesNotMatch(JSON.stringify(report), /private content inspection detail/);
  }
});

test("transport failures during authentication probes or POST requests are never retried", async () => {
  const send = transport();
  const waits = [];
  let requests = 0;
  const report = await inspectPilot(origin, async (url, init) => {
    requests++;
    if (new URL(url).pathname.startsWith("/api/") || new URL(url).pathname === "/auth/login")
      throw new TypeError("private authentication transport detail");
    return send(url, init);
  }, async (ms) => { waits.push(ms); });
  assert.equal(report.passed, false);
  assert.equal(requests, 12);
  assert.deepEqual(waits, []);
  assert.equal(report.checks.filter((check) => !check.passed).length, 8);
  for (const check of report.checks) assert.equal(check.attempts, 1);
  assert.doesNotMatch(JSON.stringify(report), /private authentication transport detail/);
});
