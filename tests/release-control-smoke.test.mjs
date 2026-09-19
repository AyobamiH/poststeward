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
      exact_recovery_checkpoints: { state: "live_verified" },
      github_main_ruleset: { state: "live_verified", blocking: false },
      public_signup: {
        state: "disabled_policy",
        scope: "public_launch",
        blocking: true,
      },
      threads_oauth_callback: { state: "external_setup_required" },
      advanced_rollout: { state: "disabled_policy" },
    },
    runtimeCapabilities: {
      policies: {
        advancedEnabled: false,
        advancedRolloutMode: "disabled",
        advancedCanaryBps: 0,
        signupMode: "restricted",
      },
    },
    evidenceStillExternal: [
      "threads_oauth_callback",
      "native_webmcp",
      "public_signup",
    ],
    ...overrides,
  };
}

function gates(overrides = {}) {
  const rows = [
    ["owner_google_signin", "live_verified", false, "restricted_staging"],
    ["exact_recovery_checkpoints", "live_verified", false, "restricted_staging"],
    ["approximate_timestamp_pitr", "blocked_external", false, "restricted_staging"],
    ["native_webmcp", "unavailable_capability", false, "restricted_staging"],
    ["github_main_ruleset", "live_verified", false, "production"],
    ["public_signup", "disabled_policy", true, "public_launch"],
  ].map(([id, state, blocking, scope]) => ({ id, state, blocking, scope }));
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
        ? { ...gate, state: "deployed" }
        : gate,
    );
    return Response.json(value, { headers: secureHeaders });
  });
  assert.equal(report.passed, false);
  assert.equal(report.checks[1].passed, false);
});

test("hosted release control rejects stale pre-acceptance GitHub governance state", async () => {
  const report = await verifyReleaseControl(origin, release, async (url) => {
    const path = new URL(url).pathname;
    if (path === "/readiness.json")
      return Response.json(readiness(), { headers: secureHeaders });
    const value = gates();
    value.gates = value.gates.map((gate) =>
      gate.id === "github_main_ruleset"
        ? { ...gate, state: "external_setup_required", blocking: true }
        : gate,
    );
    return Response.json(value, { headers: secureHeaders });
  });
  assert.equal(report.passed, false);
  assert.equal(report.checks[1].passed, false);
});

test("hosted release control rejects public admission incorrectly coupled to technical production", async () => {
  const report = await verifyReleaseControl(origin, release, async (url) => {
    const path = new URL(url).pathname;
    if (path === "/readiness.json")
      return Response.json(readiness(), { headers: secureHeaders });
    const value = gates();
    value.gates = value.gates.map((gate) =>
      gate.id === "public_signup"
        ? { ...gate, scope: "production" }
        : gate,
    );
    return Response.json(value, { headers: secureHeaders });
  });
  assert.equal(report.passed, false);
  assert.equal(report.checks[1].passed, false);
});

test("hosted release control fails when rollout policy fields drift", async () => {
  const report = await verifyReleaseControl(origin, release, async (url) => {
    const path = new URL(url).pathname;
    return Response.json(
      path === "/readiness.json"
        ? readiness({
            runtimeCapabilities: {
              policies: {
                advancedEnabled: false,
                advancedRolloutMode: "canary",
                advancedCanaryBps: 100,
                signupMode: "restricted",
              },
            },
          })
        : gates(),
      { headers: secureHeaders },
    );
  });
  assert.equal(report.passed, false);
  assert.equal(report.checks[0].passed, false);
});
