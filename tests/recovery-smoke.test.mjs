import assert from "node:assert/strict";
import test from "node:test";
import { inspectRecoveryBoundary } from "../scripts/recovery-smoke.mjs";

const origin = "https://staging.example";
const release = "a".repeat(40);
const secure = {
  "X-Frame-Options": "DENY",
  "Strict-Transport-Security": "max-age=31536000",
  "Cache-Control": "no-store",
};

test("hosted recovery boundary verifies checkpoint assets plus denied mutation surfaces and exact runtime", async () => {
  let credentialBearing = 0;
  const report = await inspectRecoveryBoundary(origin, release, async (url, init) => {
    const parsed = new URL(url);
    assert.equal(init.redirect, "manual");
    if (parsed.pathname === "/health")
      return Response.json(
        {
          release,
          advancedEnabled: false,
          providerOAuth: { x: false, threads: false, linkedin: false },
        },
        { headers: secure },
      );
    if (parsed.pathname === "/recovery")
      return new Response("<h1>Exact recovery checkpoints</h1>", {
        status: 200,
        headers: secure,
      });
    if (parsed.pathname === "/recovery-checkpoints.js")
      return new Response('fetch("/api/recovery/checkpoints")', {
        status: 200,
        headers: secure,
      });
    if (init.headers?.Authorization || init.headers?.Cookie) credentialBearing++;
    const status =
      init.headers?.Origin === "https://untrusted.example" ? 403 : 401;
    return Response.json(
      { error: { code: "DENIED" } },
      { status, headers: secure },
    );
  });
  assert.equal(report.checks.length, 17);
  assert.equal(report.passed, true);
  assert.equal(credentialBearing, 0);
  assert.equal(report.destructiveRecoveryAttempted, false);
  assert.equal(report.checkpointCaptureAttempted, false);
  assert.equal(report.ownerSessionCreated, false);
  assert.equal(report.providerAuthorizationAttempted, false);
});

test("hosted recovery boundary fails closed on wrong release or an unexpectedly open route", async () => {
  const responder = (runtimeRelease, openStatus = false) => async (url) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/health")
      return Response.json(
        {
          release: runtimeRelease,
          advancedEnabled: false,
          providerOAuth: { x: false, threads: false, linkedin: false },
        },
        { headers: secure },
      );
    if (parsed.pathname === "/recovery")
      return new Response("<h1>Exact recovery checkpoints</h1>", {
        status: 200,
        headers: secure,
      });
    if (parsed.pathname === "/recovery-checkpoints.js")
      return new Response('fetch("/api/recovery/checkpoints")', {
        status: 200,
        headers: secure,
      });
    return Response.json(
      {},
      {
        status:
          openStatus && parsed.pathname === "/api/recovery/status" ? 200 : 401,
        headers: secure,
      },
    );
  };

  const wrong = await inspectRecoveryBoundary(
    origin,
    release,
    responder("b".repeat(40)),
  );
  assert.equal(wrong.passed, false);

  const open = await inspectRecoveryBoundary(
    origin,
    release,
    responder(release, true),
  );
  assert.equal(open.passed, false);
});
