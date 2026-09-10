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

test("hosted recovery boundary verifies only denied mutation surfaces and exact runtime", async () => {
  let authenticated = 0;
  const report = await inspectRecoveryBoundary(origin, release, async (url, init) => {
    const parsed = new URL(url);
    assert.equal(init.redirect, "manual");
    if (parsed.pathname === "/health")
      return Response.json({ release, advancedEnabled: false, providerOAuth: { x: false, threads: false, linkedin: false } }, { headers: secure });
    if (init.headers?.Authorization || init.headers?.Cookie) authenticated++;
    const status = init.headers?.Origin === "https://untrusted.example" ? 403 : 401;
    return Response.json({ error: { code: "DENIED" } }, { status, headers: secure });
  });
  assert.equal(report.checks.length, 13);
  assert.equal(report.passed, true);
  assert.equal(authenticated, 0);
  assert.equal(report.destructiveRecoveryAttempted, false);
  assert.equal(report.ownerSessionCreated, false);
  assert.equal(report.providerAuthorizationAttempted, false);
});

test("hosted recovery boundary fails closed on wrong release or unexpectedly open route", async () => {
  const wrong = await inspectRecoveryBoundary(origin, release, async (url) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/health")
      return Response.json({ release: "b".repeat(40), advancedEnabled: false, providerOAuth: { x: false, threads: false, linkedin: false } }, { headers: secure });
    return Response.json({}, { status: 401, headers: secure });
  });
  assert.equal(wrong.passed, false);
  const open = await inspectRecoveryBoundary(origin, release, async (url) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/health")
      return Response.json({ release, advancedEnabled: false, providerOAuth: { x: false, threads: false, linkedin: false } }, { headers: secure });
    return Response.json({}, { status: parsed.pathname === "/api/recovery/status" ? 200 : 401, headers: secure });
  });
  assert.equal(open.passed, false);
});
