import assert from "node:assert/strict";
import test from "node:test";
import { verifyReleaseControl } from "../scripts/release-control-smoke.mjs";

const origin = "https://staging.example";
const release = "a".repeat(40);
const secureHeaders = {
  "Strict-Transport-Security": "max-age=31536000",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Cache-Control": "no-store",
};

function readiness(overrides = {}) {
  return {
    schemaVersion: 2,
    release,
    policy: { healthy: true, violations: [] },
    gates: {
      owner_google_signin: { state: "live_verified" },
      private_github_authority: { state: "live_verified" },
      stripe_sandbox_lifecycle: { state: "live_verified" },
      protected_root_cutover: { state: "live_verified" },
      exact_recovery_checkpoints: { state: "deployed" },
      threads_oauth_callback: { state: "blocked_external" },
      advanced_rollout: { state: "disabled_policy" },
    },
    runtimeCapabilities: {
      policies: { advancedEnabled: false, signupMode: "restricted" },
    },
    evidenceStillExternal: ["threads_oauth_callback", "native_webmcp"],
    ...overrides,
  };
}

function gates(overrides = {}) {
  const rows = [
    ["owner_google_signin", "live_verified", false],
    ["exact_recovery_checkpoints", "deployed", false],
    ["approximate_timestamp_pitr", "blocked_external", false],
    ["native_webmcp", "unavailable_capability", false],
    ["github_main_ruleset", "external_setup_required", true],
  ].map(([id, state, blocking]) => ({ id, state, blocking }));
  return { schemaVersion: 1, gates: rows, ...overrides };
}

test("hosted release control accepts matching healthy runtime and reviewed gate ledger", async () => {
  const report = await verifyReleaseControl(origin, release, async (url) => {
    const path = new URL(url).pathname;
    return Response.json(
      path === "/readiness.json" ? readiness() : gates(),
      { headers: secureHeaders },
    );
  });
  assert.equal(report.passed, true);
  assert.equal(report.checks.length, 2);
});

test("hosted release control fails closed on an unsafe runtime policy contradiction", async () => {
  const report = await verifyReleaseControl(origin, release, async (url) => {
    const path = new URL(url).pathname;
    return Response.json(
      path === "/readiness.json"
        ? readiness({ policy: { healthy: false, violations: ["advanced_globally_enabled_before_canary_gate"] } })
        : gates(),
      { headers: secureHeaders },
    );
  });
  assert.equal(report.passed, false);
  assert.equal(report.checks[0].passed, false);
});

test("hosted release control fails when the public ledger contradicts reviewed evidence", async () => {
  const report = await verifyReleaseControl(origin, release, async (url) => {
    const path = new URL(url).pathname;
    if (path === "/readiness.json")
      return Response.json(readiness(), { headers: secureHeaders });
    const value = gates();
    value.gates = value.gates.map((gate) =>
      gate.id === "exact_recovery_checkpoints"
        ? { ...gate, state: "live_verified" }
        : gate,
    );
    return Response.json(value, { headers: secureHeaders });
  });
  assert.equal(report.passed, false);
  assert.equal(report.checks[1].passed, false);
});
