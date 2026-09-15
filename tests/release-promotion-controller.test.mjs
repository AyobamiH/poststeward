import assert from "node:assert/strict";
import test from "node:test";
import { evaluatePromotion } from "../scripts/release-promotion-controller.mjs";

function gate(id, scope, state = "external_setup_required", blocking = true) {
  return { id, scope, state, blocking, summary: id, evidence: [] };
}

function ledger() {
  return {
    schemaVersion: 1,
    gates: [
      gate("owner_google_signin", "restricted_staging", "live_verified", false),
      gate("production_edge", "production"),
      gate("operational_alert_delivery", "production"),
      gate("capacity_cost_calibration", "production"),
      gate("hosted_cross_tenant", "production"),
      gate("github_main_ruleset", "production", "live_verified", false),
      gate("public_signup", "public_launch", "disabled_policy", true),
      gate("advanced_rollout", "advanced", "disabled_policy", false),
      gate("x_oauth", "provider_optional", "external_setup_required", false),
      gate("linkedin_oauth", "provider_optional", "external_setup_required", false),
    ],
  };
}

function readiness(overrides = {}) {
  return {
    release: "a".repeat(40),
    environment: "staging",
    origin: "https://poststeward-staging.example.com",
    policy: { healthy: true, violations: [] },
    runtimeCapabilities: {
      policies: {
        advancedEnabled: false,
        advancedRolloutMode: "disabled",
        advancedCanaryBps: 0,
      },
    },
    providers: {
      x: { oauth: false, requiredScopes: ["tweet.read", "tweet.write"] },
      threads: { oauth: true, requiredScopes: ["threads_basic"] },
      linkedin: { oauth: false, requiredScopes: ["openid", "profile"] },
    },
    ...overrides,
  };
}

function readyProductionObservations() {
  return {
    production_edge: { ready: true },
    operational_alert_delivery: { ready: true },
    capacity_cost_calibration: { ready: true },
    hosted_cross_tenant: { ready: true },
  };
}

test("production readiness does not require public admission", () => {
  const report = evaluatePromotion({
    target: "production",
    ledger: ledger(),
    readiness: readiness(),
    observations: readyProductionObservations(),
  });
  assert.equal(report.promotion.ready, true);
  assert.ok(!report.required.some((entry) => entry.gate === "public_signup"));
});

test("public launch requires both production evidence and public signup approval", () => {
  const report = evaluatePromotion({
    target: "public_launch",
    ledger: ledger(),
    readiness: readiness(),
    observations: readyProductionObservations(),
  });
  assert.equal(report.promotion.ready, false);
  assert.deepEqual(report.promotion.blockers, ["public_signup"]);
});

test("runtime policy contradictions block promotion regardless of evidence", () => {
  const report = evaluatePromotion({
    target: "production",
    ledger: ledger(),
    readiness: readiness({
      policy: {
        healthy: false,
        violations: ["advanced_globally_enabled_before_canary_gate"],
      },
    }),
    observations: readyProductionObservations(),
  });
  assert.equal(report.promotion.ready, false);
  assert.ok(
    report.promotion.blockers.includes(
      "advanced_globally_enabled_before_canary_gate",
    ),
  );
});

test("provider callbacks and owner actions are reported exactly without granting authority", () => {
  const report = evaluatePromotion({
    target: "production",
    ledger: ledger(),
    readiness: readiness(),
    observations: {},
  });
  assert.equal(
    report.providerContracts.x.callback,
    "https://poststeward-staging.example.com/connections/oauth/x/callback",
  );
  const x = report.nextActions.find((entry) => entry.gate === "x_oauth");
  assert.equal(
    x.action,
    "register_x_application_and_store_protected_client_authority",
  );
  assert.equal(x.evidenceReady, false);
});

test("live evidence is surfaced for review but never auto-advances a gate", () => {
  const report = evaluatePromotion({
    target: "production",
    ledger: ledger(),
    readiness: readiness(),
    observations: { production_edge: { ready: true } },
  });
  const edge = report.nextActions.find(
    (entry) => entry.gate === "production_edge",
  );
  assert.equal(edge.action, "review_and_advance_gate_from_live_evidence");
  assert.equal(edge.evidenceReady, true);
  assert.equal(
    ledger().gates.find((entry) => entry.id === "production_edge").state,
    "external_setup_required",
  );
});
