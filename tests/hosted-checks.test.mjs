import assert from "node:assert/strict";
import test from "node:test";
import { waitForRevision, verifyHosted } from "../scripts/hosted-checks.mjs";
const origin = "https://publish.example",
  release = "a".repeat(40);
const c = {
  vars: {
    PUBLIC_ORIGIN: origin,
    RELEASE_SHA: release,
    OIDC_ISSUER: "https://accounts.google.com",
    OIDC_CLIENT_ID: "test-client",
  },
};
const headers = {
  "Strict-Transport-Security": "max-age=31536000",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Cache-Control": "no-store",
};
const healthy = () =>
  Response.json({ status: "ok", release, advancedEnabled: false }, { headers });
const sleep = async () => {};
function service(appStatus = 200) {
  return async (url, options) => {
    const path = new URL(url).pathname;
    assert.equal(options.redirect, "manual");
    if (path === "/health") return healthy();
    if (path === "/help.json")
      return Response.json(
        {
          release,
          operations: Array(26).fill({}),
          payment: { enabled: false },
        },
        { headers },
      );
    if (path === "/readiness.json")
      return Response.json(
        {
          release,
          access: { signupMode: "restricted", publicSignup: false },
          payments: { advancedEnabled: false, mppEnabled: false },
          recovery: { externalEffectLedger: true, ownerPitr: true },
        },
        { headers },
      );
    if (path === "/app" && appStatus !== 200)
      return new Response(null, {
        status: appStatus,
        headers: { Location: "/app" },
      });
    if (["/", "/app"].includes(path))
      return new Response("<html>PostSteward</html>", {
        headers: { ...headers, "Content-Type": "text/html" },
      });
    if (path === "/api/session")
      return Response.json(
        { error: { code: "UNAUTHENTICATED" } },
        { status: options.headers?.Origin ? 403 : 401, headers },
      );
    if (path === "/mcp") return new Response(null, { status: 401 });
    if (path === "/auth/callback")
      return Response.json(
        { error: { code: "LOGIN_STATE_INVALID" } },
        { status: 400 },
      );
    if (path === "/auth/login") {
      const q = new URLSearchParams({
        client_id: "test-client",
        redirect_uri: origin + "/auth/callback",
        response_type: "code",
        scope: "openid profile email",
        state: "PRIVATE_STATE",
        nonce: "PRIVATE_NONCE",
        code_challenge: "PRIVATE_CHALLENGE",
        code_challenge_method: "S256",
      });
      return new Response(null, {
        status: 302,
        headers: {
          ...headers,
          Location: "https://accounts.google.com/o/oauth2/v2/auth?" + q,
          "Set-Cookie":
            "__Host-login=PRIVATE_COOKIE; HttpOnly; Secure; SameSite=Lax; Path=/",
        },
      });
    }
    return new Response("resource", { headers });
  };
}
test("readiness tolerates bounded propagation and old revisions, never redeploys", async () => {
  let calls = 0,
    waits = 0;
  const report = await waitForRevision(origin, release, {
    send: async (_url, options) => {
      assert.equal(options.method, undefined);
      assert.equal(options.headers["Cache-Control"], "no-cache");
      calls++;
      if (calls === 1) throw new Error("private network details");
      if (calls === 2) return new Response(null, { status: 404 });
      if (calls === 3)
        return Response.json({ status: "ok", release: "b".repeat(40) });
      return healthy();
    },
    sleep: async () => {
      waits++;
    },
  });
  assert.deepEqual(report, { attempts: 4, status: 200 });
  assert.equal(waits, 3);
});
test("readiness fails after its bound without exposing remote error contents", async () => {
  let calls = 0;
  await assert.rejects(
    waitForRevision(origin, release, {
      attempts: 3,
      sleep,
      send: async () => {
        calls++;
        throw new Error("PRIVATE_NETWORK_SECRET");
      },
    }),
    (e) =>
      e.message.includes("bounded checks") &&
      !e.message.includes("PRIVATE_NETWORK_SECRET"),
  );
  assert.equal(calls, 3);
});
test("readiness never follows redirects or retries access rejection", async () => {
  for (const status of [302, 401, 403]) {
    let calls = 0;
    await assert.rejects(
      waitForRevision(origin, release, {
        sleep,
        send: async () => {
          calls++;
          return new Response(null, { status });
        },
      }),
      /not retrying/,
    );
    assert.equal(calls, 1);
  }
});
test("hosted report checks 19 surfaces and does not disclose login state or cookies", async () => {
  const report = await verifyHosted(c, { send: service(), sleep });
  assert.equal(report.passed, true);
  assert.equal(report.checks.length, 19);
  assert.ok(
    report.checks.some((item) => item.name === "public resource: /app-client.js"),
  );
  assert.doesNotMatch(JSON.stringify(report), /PRIVATE_|test-client/);
  assert.ok(report.notVerified.includes("completed owner sign-in"));
});
test("version-bound read-only surfaces converge independently during edge propagation", async () => {
  const stable = service();
  const calls = new Map();
  let waits = 0;
  const report = await verifyHosted(c, {
    sleep: async () => {
      waits++;
    },
    send: async (url, options) => {
      const path = new URL(url).pathname;
      const count = (calls.get(path) || 0) + 1;
      calls.set(path, count);
      if (path === "/health" && count === 2)
        return Response.json(
          { status: "ok", release: "b".repeat(40), advancedEnabled: false },
          { headers },
        );
      if (path === "/help.json" && count === 1)
        return Response.json(
          {
            release: "b".repeat(40),
            operations: Array(26).fill({}),
            payment: { enabled: false },
          },
          { headers },
        );
      if (path === "/readiness.json" && count === 1)
        return new Response(null, { status: 404, headers });
      return stable(url, options);
    },
  });
  assert.equal(report.passed, true);
  assert.equal(report.checks[0].attempts, 2);
  assert.equal(report.checks[1].attempts, 2);
  assert.equal(report.checks[2].attempts, 2);
  assert.equal(waits, 3);
});
test("safe revision retries never turn redirects or server failures into acceptance", async () => {
  for (const status of [307, 500]) {
    let helpCalls = 0;
    const stable = service();
    const report = await verifyHosted(c, {
      sleep,
      send: async (url, options) => {
        if (new URL(url).pathname === "/help.json") {
          helpCalls++;
          return new Response(null, {
            status,
            headers: { ...headers, Location: "/help.json" },
          });
        }
        return stable(url, options);
      },
    });
    assert.equal(report.passed, false);
    assert.equal(helpCalls, 1);
  }
});
test("a workspace self-redirect fails acceptance rather than being followed", async () => {
  const report = await verifyHosted(c, { send: service(307), sleep });
  assert.equal(report.passed, false);
  assert.deepEqual(
    report.checks.filter((x) => !x.passed).map((x) => [x.name, x.status]),
    [["HTML serves without a redirect: /app", 307]],
  );
});
