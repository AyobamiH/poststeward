import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { evaluatePromotion, collectObservations } from "../scripts/release-promotion-controller.mjs";

const release = "a".repeat(40);
const now = Date.parse("2026-09-15T18:00:00Z");
const productionIds = ["capacity_cost_calibration"];
function fixture(target = "restricted_staging", acceptedProduction = false) {
  const ledger = JSON.parse(readFileSync(new URL("../public/release-gates.json", import.meta.url), "utf8"));
  if (acceptedProduction)
    for (const gate of ledger.gates)
      if (productionIds.includes(gate.id)) { gate.state = "live_verified"; gate.blocking = false; }
  const environment = ["production", "public_launch"].includes(target) ? "production" : "staging";
  return {
    target, ledger, expectedRelease: release, now,
    readiness: {
      schemaVersion: 2, release, environment,
      origin: `https://${environment}.example.com`,
      policy: { healthy: true, violations: [] },
      gates: Object.fromEntries(ledger.gates.map((gate) => [gate.id, structuredClone(gate)])),
      runtimeCapabilities: { policies: { advancedEnabled: false, advancedRolloutMode: "disabled", advancedCanaryBps: 0 } },
      providers: { x: { oauth: false }, threads: { oauth: true }, linkedin: { oauth: false } },
    },
  };
}
function observation(context, overrides = {}) {
  return { evidenceClass: "hosted_observation", release, environment: context.readiness.environment,
    origin: context.readiness.origin, observedAt: now, ready: true, ...overrides };
}
test("reviewed technical production readiness does not require public admission", () => {
  const report = evaluatePromotion(fixture("production", true));
  assert.equal(report.promotion.ready, true);
  assert.equal(report.promotion.authorized, false);
  assert.ok(!report.required.some((entry) => entry.gate === "public_signup"));
});
test("public launch still requires the separately reviewed admission gate", () => {
  const report = evaluatePromotion(fixture("public_launch", true));
  assert.equal(report.promotion.ready, false);
  assert.deepEqual(report.promotion.blockers, ["public_signup"]);
});
test("successful observations only make evidence review-ready, never auto-promote", () => {
  const input = fixture("production");
  input.observations = Object.fromEntries(productionIds.map((id) => [id, observation(input)]));
  const before = JSON.stringify(input.ledger);
  const report = evaluatePromotion(input);
  assert.equal(report.promotion.evidenceReady, true);
  assert.equal(report.promotion.ready, false);
  assert.deepEqual(report.promotion.reviewRequired, productionIds);
  assert.equal(JSON.stringify(input.ledger), before);
  assert.equal(report.nextAction.action, "review_and_advance_gate_from_live_evidence");
});
test("arbitrary ready flags cannot satisfy a gate", () => {
  const input = fixture("production");
  input.observations = Object.fromEntries(productionIds.map((id) => [id, { ready: true }]));
  const report = evaluatePromotion(input);
  assert.equal(report.promotion.ready, false);
  assert.equal(report.promotion.evidenceReady, false);
  assert.ok(report.required.every((entry) => entry.evidenceReady === false));
});
test("staging evidence never becomes production evidence", () => {
  const input = fixture("production", true);
  input.readiness.environment = "staging";
  const report = evaluatePromotion(input);
  assert.equal(report.promotion.ready, false);
  assert.ok(report.promotion.blockers.includes("target_environment_mismatch"));
});
for (const expectedRelease of [undefined, "", "b".repeat(40)])
  test(`missing or different expected revision fails closed: ${String(expectedRelease)}`, () => {
    const input = fixture();
    input.expectedRelease = expectedRelease;
    assert.equal(evaluatePromotion(input).promotion.ready, false);
  });
test("runtime ledger must match the complete reviewed ledger", () => {
  const input = fixture();
  input.readiness.gates.github_main_ruleset.state = "external_setup_required";
  const report = evaluatePromotion(input);
  assert.equal(report.promotion.ready, false);
  assert.ok(report.promotion.blockers.includes("hosted_gate_ledger_mismatch"));
});
test("empty or incomplete ledgers cannot erase required gates", () => {
  const empty = fixture();
  empty.ledger.gates = [];
  assert.throws(() => evaluatePromotion(empty), /invalid or empty/);
  const missing = fixture();
  missing.ledger.gates = missing.ledger.gates.filter((gate) => gate.id !== "production_edge");
  assert.throws(() => evaluatePromotion(missing), /missing or mis-scopes/);
});
test("a formerly accepted core gate cannot be skipped by setting blocking false", () => {
  const input = fixture();
  const gate = input.ledger.gates.find((entry) => entry.id === "exact_recovery_checkpoints");
  gate.state = "implemented";
  gate.blocking = false;
  input.readiness.gates[gate.id] = structuredClone(gate);
  const report = evaluatePromotion(input);
  assert.equal(report.promotion.ready, false);
  assert.ok(report.promotion.blockers.includes(gate.id));
});
test("contradictory policy flags and unexplained unhealthy policy both block", () => {
  for (const policy of [
    { healthy: true, violations: ["advanced_globally_enabled_before_canary_gate"] },
    { healthy: false, violations: [] },
    { healthy: true },
  ]) {
    const input = fixture();
    input.readiness.policy = policy;
    const report = evaluatePromotion(input);
    assert.equal(report.promotion.ready, false);
    assert.ok(report.promotion.blockers.length > 0);
  }
});
test("accepted historical effects are not reopened by observation freshness", () => {
  const input = fixture();
  input.observations = { exact_recovery_checkpoints: observation(input, { observedAt: 0 }) };
  const report = evaluatePromotion(input);
  assert.equal(report.promotion.ready, true);
  assert.ok(!report.nextActions.some((entry) => entry.gate === "exact_recovery_checkpoints"));
});
test("production next actions cannot be displaced by optional provider setup", () => {
  const report = evaluatePromotion(fixture("production"));
  assert.deepEqual(report.nextActions.map((entry) => entry.gate), productionIds);
  assert.equal(report.nextAction.gate, "capacity_cost_calibration");
  assert.equal(report.providerContracts.x.callback, "https://production.example.com/connections/oauth/x/callback");
  assert.equal(report.optionalActions.find((entry) => entry.gate === "x_oauth").action,
    "register_x_application_and_store_protected_client_authority");
});
test("SLO file collection uses the actual evaluator verdict and event-window timestamp", async () => {
  const directory = mkdtempSync(join(tmpdir(), "poststeward-promotion-"));
  try {
    const path = join(directory, "slo.json");
    // Synthetic unit-test input, never persisted as a live acceptance receipt.
    const raw = {
      schemaVersion: 1, evidenceClass: "hosted_observation", environment: "staging", release,
      cohort: { mode: "canary", bps: 100, startedAt: now - 86400000, observedAt: now, workspaceCount: 1, advancedOperations: 20 },
      invariants: { duplicateExternalEffects: 0 },
      publication: { verified: 20, eligible: 20 }, schedule: { withinFiveMinutes: 20, due: 20 },
      oauthRefresh: { succeeded: 1, attempts: 1 },
      providerReconciliation: { samples: 1, p95Ms: 100 }, webhookReconciliation: { samples: 1, p95Ms: 100 },
      advanced: { productPath: { sourceChanges: 1, inventorySnapshots: 1, spacedAllocations: 1,
        providerEffects: 20, verifiedReadbacks: 20, scheduledMetricsCaptures: 1 } },
    };
    writeFileSync(path, JSON.stringify(raw));
    const result = await collectObservations({ POSTSTEWARD_SLO_OBSERVATION: path });
    assert.equal(result.advanced_rollout.ready, true);
    assert.equal(result.advanced_rollout.observedAt, now);
    assert.equal(result.advanced_rollout.release, release);
    raw.invariants.duplicateExternalEffects = 1;
    raw.ready = true;
    writeFileSync(path, JSON.stringify(raw));
    assert.equal((await collectObservations({ POSTSTEWARD_SLO_OBSERVATION: path })).advanced_rollout.ready, false);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
test("empty or undefined optional file settings do not fabricate observations", async () => {
  assert.deepEqual(await collectObservations({}), {});
  assert.deepEqual(await collectObservations({ POSTSTEWARD_SLO_OBSERVATION: "", POSTSTEWARD_ALERT_EVIDENCE: "" }), {});
});
