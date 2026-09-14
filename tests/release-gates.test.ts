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
    ADVANCED_ENABLED: "false",
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
  assert.ok(externalEvidenceGateIds().includes("threads_oauth_callback"));
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
  assert.equal(runtime.policies.advancedEnabled, false);
  assert.equal(releasePolicyViolations(env()).length, 0);
});

test("policy contradictions are visible instead of silently broadening claims", () => {
  const unsafe = env({
    SIGNUP_MODE: "public",
    ADVANCED_ENABLED: "true",
    MPP_ENABLED: "true",
    LINKEDIN_MEMBER_READBACK: "true",
  });
  assert.deepEqual(releasePolicyViolations(unsafe).sort(), [
    "advanced_globally_enabled_before_canary_gate",
    "linkedin_readback_enabled_without_live_permission_evidence",
    "mpp_enabled_before_settlement_gate",
    "public_signup_enabled_before_production_gate",
  ]);
  const readiness = releaseReadiness(unsafe);
  assert.equal(readiness.policy.healthy, false);
  assert.equal(readiness.gates.private_github_authority.state, "live_verified");
  assert.equal(readiness.gates.advanced_rollout.state, "disabled_policy");
});

test("readiness keeps legacy fields while exposing the typed control plane", () => {
  const readiness = releaseReadiness(env());
  assert.equal(readiness.schemaVersion, 2);
  assert.equal(readiness.policy.healthy, true);
  assert.equal(readiness.recovery.exactCheckpoints, true);
  assert.equal(readiness.gates.exact_recovery_checkpoints.state, "live_verified");
  assert.ok(readiness.evidenceStillExternal.includes("native_webmcp"));
  assert.ok(!readiness.evidenceStillExternal.includes("stripe_sandbox_lifecycle"));
});
