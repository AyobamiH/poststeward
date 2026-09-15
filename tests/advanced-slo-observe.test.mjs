import assert from "node:assert/strict";
import test from "node:test";
import {
  buildAdvancedSloObservation,
  percentile95,
} from "../scripts/advanced-slo-observe.mjs";
import { evaluateSloObservation } from "../scripts/slo-evaluate.mjs";

test("p95 uses nearest-rank evidence without smoothing away tail latency", () => {
  assert.equal(percentile95([]), 0);
  assert.equal(percentile95([10]), 10);
  assert.equal(percentile95([1, 2, 3, 4, 100]), 100);
  assert.equal(percentile95(Array.from({ length: 20 }, (_, i) => i + 1)), 19);
});

test("hosted rows build the exact evaluator schema and can become promotion-ready", () => {
  const startedAt = 1_000_000;
  const observedAt = startedAt + 25 * 60 * 60 * 1000;
  const observation = buildAdvancedSloObservation({
    run: {
      release: "a".repeat(40),
      canary_bps: 500,
      started_at: startedAt,
      stopped_at: null,
    },
    observedAt,
    workspaceCount: 2,
    eventCounts: {
      advanced_operation: 24,
      source_change: 2,
      inventory_snapshot: 2,
      spaced_allocation: 20,
      publication_eligible: 20,
      verified_readback: 20,
      oauth_refresh_attempt: 1,
      oauth_refresh_success: 1,
      scheduled_metrics_capture: 2,
    },
    scheduleDurations: Array(20).fill(60_000),
    providerReconciliationDurations: [10_000, 20_000],
    webhookDurations: [5_000],
    duplicateExternalEffects: 0,
  });
  assert.equal(observation.evidenceClass, "hosted_observation");
  assert.equal(observation.publication.eligible, 20);
  assert.equal(observation.publication.verified, 20);
  assert.equal(observation.schedule.withinFiveMinutes, 20);
  assert.equal(observation.advanced.productPath.providerEffects, 20);
  assert.equal(evaluateSloObservation(observation).promotion.ready, true);
});

test("observation remains evidence, not a fabricated pass, when required paths are absent", () => {
  const observation = buildAdvancedSloObservation({
    run: {
      release: "b".repeat(40),
      canary_bps: 100,
      started_at: 1_000,
      stopped_at: 1_000 + 24 * 60 * 60 * 1000,
    },
    observedAt: 1_000 + 30 * 60 * 60 * 1000,
    workspaceCount: 1,
    eventCounts: {
      advanced_operation: 20,
      publication_eligible: 20,
      verified_readback: 20,
      oauth_refresh_attempt: 1,
      oauth_refresh_success: 1,
    },
    scheduleDurations: Array(20).fill(1_000),
    providerReconciliationDurations: [1_000],
    webhookDurations: [1_000],
    duplicateExternalEffects: 0,
  });
  const report = evaluateSloObservation(observation);
  assert.equal(report.promotion.ready, false);
  assert.ok(report.promotion.blockers.includes("advanced_product_path_incomplete"));
});
