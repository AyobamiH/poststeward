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
