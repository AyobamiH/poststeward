import assert from "node:assert/strict";
import test from "node:test";
import {
  SLO_POLICY,
  evaluateSloObservation,
} from "../scripts/slo-evaluate.mjs";

function healthy(overrides = {}) {
  const startedAt = 1_789_000_000_000;
  return {
    schemaVersion: 1,
    evidenceClass: "hosted_observation",
    environment: "staging",
    release: "a".repeat(40),
    cohort: {
      mode: "canary",
      runId: "reviewed-run-id-1",
      seedHash: "b".repeat(64),
      bps: 500,
      startedAt,
      observedAt: startedAt + SLO_POLICY.minimumCanaryWindowMs,
      workspaceCount: 2,
      advancedOperations: 50,
    },
    invariants: { duplicateExternalEffects: 0, evidenceMethod: "independent_provider_readback" },
    evidenceCoverage: {
      publication: true, schedule: true, oauth: true, providerReconciliation: true,
      webhook: true, cardinality: true, recovery: true,
    },
    publication: { eligible: 100, verified: 100 },
    schedule: { due: 100, withinFiveMinutes: 100 },
    oauthRefresh: { attempts: 5, succeeded: 5 },
    providerReconciliation: { samples: 5, p95Ms: 60_000 },
    webhookReconciliation: { samples: 5, p95Ms: 30_000 },
    advanced: {
      productPath: {
        sourceChanges: 2,
        inventorySnapshots: 2,
        spacedAllocations: 2,
        providerEffects: 2,
        verifiedReadbacks: 2,
        scheduledMetricsCaptures: 2,
      },
    },
    recovery: { samples: 1, p95RtoMs: 60_000, maxRpoMs: 60_000,
      evidenceMethod: "independent_monitored_recovery_receipt",
      release: "a".repeat(40), verified: true, receiptId: "reviewed-recovery-receipt-1" },
    ...overrides,
  };
}

test("healthy hosted canary evidence is promotion-ready but never self-authorises rollout", () => {
  const report = evaluateSloObservation(healthy());
  assert.equal(report.promotion.ready, true);
  assert.deepEqual(report.promotion.blockers, []);
  assert.match(report.promotion.note, /reviewed release-gate change/);
  assert.equal(report.invariant.severity, "none");
});

test("PK-unique ledger rows cannot prove external-provider exactly-once delivery", () => {
  const report = evaluateSloObservation(healthy({
    invariants: { duplicateExternalEffects: 0, evidenceMethod: "ledger_primary_key" },
  }));
  assert.equal(report.promotion.ready, false);
  assert.equal(report.invariant.passed, false);
  assert.ok(report.promotion.blockers.includes("duplicate_external_effect_evidence_unverified"));
});

test("legacy zero with no provider-readback provenance remains unverified", () => {
  const report = evaluateSloObservation(healthy({ invariants: { duplicateExternalEffects: 0 } }));
  assert.equal(report.promotion.ready, false);
  assert.equal(report.invariant.evidenceVerified, false);
  assert.ok(report.promotion.blockers.includes("duplicate_external_effect_evidence_unverified"));
});

test("one duplicate external effect is an immediate hard invariant failure", () => {
  const report = evaluateSloObservation(
    healthy({ invariants: { duplicateExternalEffects: 1 } }),
  );
  assert.equal(report.invariant.passed, false);
  assert.equal(report.invariant.severity, "page");
  assert.ok(report.promotion.blockers.includes("duplicate_external_effect_invariant"));
});

test("error budget exhaustion and insufficient evidence are separate blockers", () => {
  const observation = healthy();
  observation.publication = { eligible: 100, verified: 98 };
  observation.oauthRefresh = { attempts: 0, succeeded: 0 };
  const report = evaluateSloObservation(observation);
  assert.ok(
    report.promotion.blockers.includes(
      "verified_publication_error_budget_exhausted",
    ),
  );
  assert.ok(
    report.promotion.blockers.includes(
      "oauth_refresh_success_insufficient_samples",
    ),
  );
  const publication = report.ratios.find(
    (item) => item.name === "verified_publication",
  );
  assert.ok(publication.budgetConsumed > 1);
});

test("promotion requires the complete Advanced product path", () => {
  const observation = healthy();
  observation.advanced.productPath.scheduledMetricsCaptures = 0;
  const report = evaluateSloObservation(observation);
  assert.equal(report.advancedProductPath.passed, false);
  assert.ok(report.promotion.blockers.includes("advanced_product_path_incomplete"));
});

test("recovery must be observed and within RTO/RPO budgets before promotion", () => {
  const unobserved = evaluateSloObservation(healthy({ recovery: { samples: 0 } }));
  assert.equal(unobserved.recovery.observed, false);
  assert.equal(unobserved.promotion.ready, false);
  assert.ok(unobserved.promotion.blockers.includes("recovery_evidence_missing"));

  const observation = healthy();
  observation.recovery = {
    samples: 2,
    p95RtoMs: SLO_POLICY.monitoredRecovery.p95RtoMs + 1,
    maxRpoMs: SLO_POLICY.monitoredRecovery.maxRpoMs,
    evidenceMethod: "independent_monitored_recovery_receipt",
    release: observation.release, verified: true, receiptId: "reviewed-recovery-receipt-2",
  };
  const report = evaluateSloObservation(observation);
  assert.equal(report.recovery.observed, true);
  assert.equal(report.recovery.passed, false);
  assert.equal(report.promotion.ready, false);
  assert.ok(report.promotion.blockers.includes("recovery_rto_rpo_budget_exceeded"));
});

test("nominal recovery timing cannot pass without independently attested release-bound receipt", () => {
  const synthetic = healthy({
    recovery: {
      samples: 1, p95RtoMs: 1_000, maxRpoMs: 0,
      evidenceMethod: "unverified_synthetic_only",
    },
  });
  const report = evaluateSloObservation(synthetic);
  assert.equal(report.recovery.observed, true);
  assert.equal(report.recovery.evidenceVerified, false);
  assert.equal(report.recovery.passed, false);
  assert.equal(report.promotion.ready, false);
  assert.ok(report.promotion.blockers.includes("recovery_evidence_unverified"));

  const wrongRelease = healthy({
    recovery: { ...healthy().recovery, release: "b".repeat(40) },
  });
  assert.equal(evaluateSloObservation(wrongRelease).recovery.evidenceVerified, false);
  const missingReceipt = healthy({
    recovery: { ...healthy().recovery, receiptId: "" },
  });
  assert.equal(evaluateSloObservation(missingReceipt).recovery.evidenceVerified, false);
});

test("fixtures, short windows, broad cohorts and malformed counters fail closed", () => {
  assert.throws(
    () => evaluateSloObservation({ ...healthy(), evidenceClass: "synthetic" }),
    /hosted observation/,
  );
  const short = healthy();
  short.cohort.observedAt = short.cohort.startedAt + 60_000;
  assert.equal(evaluateSloObservation(short).promotion.ready, false);
  const broad = healthy();
  broad.cohort.bps = 1001;
  assert.throws(() => evaluateSloObservation(broad), /1 and 1000 basis points/);
  const invalid = healthy();
  invalid.publication = { eligible: 1, verified: 2 };
  assert.throws(() => evaluateSloObservation(invalid), /cannot exceed total/);
});

test("complete metrics without provenance cannot become promotion-ready", () => {
  const observation = healthy();
  delete observation.evidenceCoverage.publication;
  observation.evidenceCoverage.oauth = false;
  const report = evaluateSloObservation(observation);
  assert.equal(report.promotion.ready, false);
  assert.ok(report.promotion.blockers.includes("publication_provenance_unverified"));
  assert.ok(report.promotion.blockers.includes("oauth_provenance_unverified"));
  const missingRun = healthy();
  delete missingRun.cohort.runId;
  assert.equal(evaluateSloObservation(missingRun).promotion.ready, false);
  assert.ok(evaluateSloObservation(missingRun).promotion.blockers.includes("active_run_provenance_unverified"));
});
