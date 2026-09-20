import assert from "node:assert/strict";
import test from "node:test";
import {
  externalEvidenceGateIds,
  releaseGateDefinitions,
  releasePolicyViolations,
  runtimeCapabilitySnapshot,
} from "../src/release-gates.ts";
import { releaseReadiness } from "../src/readiness.ts";

function env(overrides: Record<string, string | undefined> = {}) {
  return {
    RELEASE_SHA: "a".repeat(40),
    DEPLOY_ENV: "staging",
    SIGNUP_MODE: "restricted",
    PUBLIC_WORKSPACE_LIMIT: "100",
    PUBLIC_SIGNUPS_PER_HOUR: "10",
    ADVANCED_ENABLED: "false",
    ADVANCED_ROLLOUT_MODE: "disabled",
    ADVANCED_CANARY_BPS: "0",
    ADVANCED_CANARY_SEED: "",
    MPP_ENABLED: "false",
    ENCRYPTION_ROOT_WRITE: "next",
    X_OAUTH_CLIENT_ID: "",
    X_OAUTH_CLIENT_SECRET: "",
    THREADS_OAUTH_CLIENT_ID: "threads-client",
    THREADS_OAUTH_CLIENT_SECRET: "threads-secret",
    LINKEDIN_OAUTH_CLIENT_ID: "",
    LINKEDIN_OAUTH_CLIENT_SECRET: "",
    LINKEDIN_MEMBER_READBACK: "false",
    GITHUB_APP_CLIENT_ID: "github-client",
    GITHUB_APP_CLIENT_SECRET: "github-secret",
    GITHUB_APP_SLUG: "poststeward-staging",
    STRIPE_SANDBOX_ENABLED: "true",
    STRIPE_SECRET_KEY: "sk_test_fixture",
    STRIPE_PRICE_ID: "price_fixture",
    STRIPE_WEBHOOK_SECRET: "whsec_fixture",
    OPERATIONAL_ALERT_WEBHOOK_URL: "",
    ...overrides,
  } as any;
}

test("release gate identifiers are unique and external evidence is derived from their states", () => {
  const ids = releaseGateDefinitions.map((gate) => gate.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.ok(ids.includes("private_github_authority"));
  assert.ok(ids.includes("exact_recovery_checkpoints"));
  assert.ok(ids.includes("native_webmcp"));
  assert.ok(ids.includes("github_main_ruleset"));
  assert.ok(!externalEvidenceGateIds().includes("private_github_authority"));
  assert.ok(!externalEvidenceGateIds().includes("threads_oauth_callback"));
  assert.ok(!externalEvidenceGateIds().includes("x_oauth"));
  assert.ok(!externalEvidenceGateIds().includes("x_publication_readback"));
  assert.ok(!externalEvidenceGateIds().includes("capacity_cost_calibration"));
  assert.equal(
    releaseGateDefinitions.find((gate) => gate.id === "x_token_refresh_rotation")?.state,
    "implemented",
  );
  assert.deepEqual(
    releaseGateDefinitions.find((gate) => gate.id === "capacity_cost_calibration"),
    {
      id: "capacity_cost_calibration",
      state: "live_verified",
      scope: "production",
      blocking: false,
      summary: "Exact-release staging and production observations project the reviewed first-100 workload with at least 30% product headroom and inside the reviewed Cloudflare/provider cost envelope.",
      evidence: [
        "docs/capacity-cost-live-evidence-2026-09-19.md",
        "actions/35442767724",
      ],
    },
  );
  assert.equal(
    releaseGateDefinitions.find((gate) => gate.id === "public_signup")?.scope,
    "public_launch",
  );
});

test("runtime capabilities remain distinct from reviewed live evidence", () => {
  const runtime = runtimeCapabilitySnapshot(env());
  assert.deepEqual(runtime.providerApplications, {
    x: false,
    threads: true,
    linkedin: false,
    linkedinMemberReadback: false,
  });
  assert.equal(runtime.privateGitHubConfigured, true);
  assert.equal(runtime.stripeSandboxConfigured, true);
  assert.equal(runtime.operationalAlertWebhookConfigured, false);
  assert.equal(runtime.policies.advancedEnabled, false);
  assert.equal(runtime.policies.advancedRolloutMode, "disabled");
  assert.equal(runtime.policies.advancedCanaryBps, 0);
  assert.equal(runtime.policies.advancedCanarySeedConfigured, false);
  assert.equal(releasePolicyViolations(env()).length, 0);
});

test("configured webhook remains runtime capability separate from reviewed alert acceptance", () => {
  const runtime = runtimeCapabilitySnapshot(
    env({ OPERATIONAL_ALERT_WEBHOOK_URL: "https://alerts.example/hook" }),
  );
  assert.equal(runtime.operationalAlertWebhookConfigured, true);
  assert.equal(
    releaseGateDefinitions.find((gate) => gate.id === "operational_alert_delivery")?.state,
    "live_verified",
  );
  assert.ok(!externalEvidenceGateIds().includes("operational_alert_delivery"));
});

test("bounded staging canary is a permitted evidence mode, not a global rollout", () => {
  const canary = env({
    ADVANCED_ENABLED: "true",
    ADVANCED_ROLLOUT_MODE: "canary",
    ADVANCED_CANARY_BPS: "500",
    ADVANCED_CANARY_SEED: "reviewed-canary-seed",
  });
  assert.deepEqual(releasePolicyViolations(canary), []);
  const readiness = releaseReadiness(canary);
  assert.equal(readiness.policy.healthy, true);
  assert.equal(readiness.runtimeCapabilities.policies.advancedEnabled, true);
  assert.equal(readiness.runtimeCapabilities.policies.advancedRolloutMode, "canary");
  assert.equal(readiness.runtimeCapabilities.policies.advancedCanaryBps, 500);
});

test("policy contradictions are visible instead of silently broadening claims", () => {
  const unsafe = env({
    SIGNUP_MODE: "public",
    ADVANCED_ENABLED: "true",
    ADVANCED_ROLLOUT_MODE: "global",
    ADVANCED_CANARY_BPS: "10000",
    ADVANCED_CANARY_SEED: "unsafe-global-seed",
    MPP_ENABLED: "true",
    LINKEDIN_MEMBER_READBACK: "true",
  });
  assert.deepEqual(releasePolicyViolations(unsafe).sort(), [
    "advanced_globally_enabled_before_canary_gate",
    "linkedin_readback_enabled_without_live_permission_evidence",
    "mpp_enabled_before_settlement_gate",
    "public_signup_enabled_before_public_launch_gate",
  ]);
  const readiness = releaseReadiness(unsafe);
  assert.equal(readiness.policy.healthy, false);
  assert.equal(readiness.gates.private_github_authority.state, "live_verified");
  assert.equal(readiness.gates.advanced_rollout.state, "disabled_policy");
});

test("public admission bounds fail closed when they drift from the reviewed first-100 policy", () => {
  assert.deepEqual(
    releasePolicyViolations(
      env({
        SIGNUP_MODE: "public",
        PUBLIC_WORKSPACE_LIMIT: "101",
        PUBLIC_SIGNUPS_PER_HOUR: "10",
      }),
    ).sort(),
    [
      "public_admission_bounds_drift",
      "public_signup_enabled_before_public_launch_gate",
    ],
  );
  const snapshot = runtimeCapabilitySnapshot(env());
  assert.equal(snapshot.policies.publicWorkspaceLimit, 100);
  assert.equal(snapshot.policies.publicSignupsPerHour, 10);
});

test("malformed or contradictory rollout state fails closed in readiness", () => {
  assert.deepEqual(
    releasePolicyViolations(
      env({ ADVANCED_ROLLOUT_MODE: "canary", ADVANCED_CANARY_BPS: "100" }),
    ),
    ["advanced_disabled_policy_drift"],
  );
  assert.deepEqual(
    releasePolicyViolations(
      env({
        ADVANCED_ENABLED: "true",
        ADVANCED_ROLLOUT_MODE: "canary",
        ADVANCED_CANARY_BPS: "2000",
        ADVANCED_CANARY_SEED: "reviewed-canary-seed",
      }),
    ),
    ["advanced_canary_policy_invalid"],
  );
});

test("readiness keeps legacy fields while exposing the typed control plane", () => {
  const readiness = releaseReadiness(env());
  assert.equal(readiness.schemaVersion, 2);
  assert.equal(readiness.policy.healthy, true);
  assert.equal(readiness.recovery.exactCheckpoints, true);
  assert.equal(readiness.gates.exact_recovery_checkpoints.state, "live_verified");
  assert.equal(readiness.runtimeCapabilities.operationalAlertWebhookConfigured, false);
  assert.equal(readiness.runtimeCapabilities.policies.advancedRolloutMode, "disabled");
  assert.equal(readiness.runtimeCapabilities.policies.advancedCanaryBps, 0);
  assert.ok(readiness.evidenceStillExternal.includes("native_webmcp"));
  assert.ok(!readiness.evidenceStillExternal.includes("stripe_sandbox_lifecycle"));
});
