import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  buildAdvancedSloObservation,
  observeAdvancedSlo,
  percentile95,
} from "../scripts/advanced-slo-observe.mjs";
import { evaluateSloObservation } from "../scripts/slo-evaluate.mjs";

test("p95 uses nearest-rank evidence without smoothing away tail latency", () => {
  assert.equal(percentile95([]), 0);
  assert.equal(percentile95([10]), 10);
  assert.equal(percentile95([1, 2, 3, 4, 100]), 100);
  assert.equal(percentile95(Array.from({ length: 20 }, (_, i) => i + 1)), 19);
});

test("hosted sample counts cannot satisfy promotion without actual recovery evidence", () => {
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
    duplicateEvidenceMethod: "independent_provider_readback",
  });
  assert.equal(observation.evidenceClass, "hosted_observation");
  assert.equal(observation.publication.eligible, 20);
  assert.equal(observation.publication.verified, 20);
  assert.equal(observation.schedule.withinFiveMinutes, 20);
  assert.equal(observation.advanced.productPath.providerEffects, 20);
  assert.equal(evaluateSloObservation(observation).promotion.ready, false);
  assert.ok(evaluateSloObservation(observation).promotion.blockers.includes("recovery_evidence_missing"));
});

test("DB primary-key uniqueness cannot become provider exactly-once evidence", () => {
  const observation = buildAdvancedSloObservation({
    run: {release: "c".repeat(40), canary_bps: 100, started_at: 1000, stopped_at: 1000 + 86400000},
    observedAt: 1000 + 86400000,
    workspaceCount: 1,
    eventCounts: {advanced_operation: 20, source_change: 1, inventory_snapshot: 1,
      spaced_allocation: 20, publication_eligible: 20, verified_readback: 20,
      oauth_refresh_attempt: 1, oauth_refresh_success: 1, scheduled_metrics_capture: 1},
    scheduleDurations: Array(20).fill(1000),
    providerReconciliationDurations: [1000], webhookDurations: [1000],
    duplicateExternalEffects: null,
    duplicateEvidenceMethod: "workspace_fingerprint_primary_key",
  });
  assert.equal(observation.invariants.duplicateExternalEffects, null);
  const report = evaluateSloObservation(observation);
  assert.equal(report.promotion.ready, false);
  assert.deepEqual(report.promotion.blockers, ["duplicate_external_effect_evidence_unverified", "recovery_evidence_missing"]);
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
    duplicateEvidenceMethod: "independent_provider_readback",
  });
  const report = evaluateSloObservation(observation);
  assert.equal(report.promotion.ready, false);
  assert.ok(report.promotion.blockers.includes("advanced_product_path_incomplete"));
});


test("stopped historical cohorts cannot be selected for current SLO evidence", async () => {
  const seen = [];
  const env = {
    CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32),
    POSTSTEWARD_ORIGIN: "https://staging.example.test",
  };
  await assert.rejects(
    observeAdvancedSlo(env, async (_account, _database, _token, sql) => {
      seen.push(sql);
      return [];
    }, async () => { throw new Error("readiness must not run without active cohort"); }),
    /No active Advanced canary run boundary exists yet/,
  );
  assert.match(seen[0], /WHERE stopped_at IS NULL/);
});

test("a stopped canary row cannot masquerade as an active release", async () => {
  const env = {
    CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32),
    POSTSTEWARD_ORIGIN: "https://staging.example.test",
  };
  await assert.rejects(
    observeAdvancedSlo(env, async () => [{
      release: "a".repeat(40), canary_bps: 100,
      started_at: 100, stopped_at: 200,
    }], async () => { throw new Error("must not read stopped cohort"); }),
    /No active Advanced canary run boundary exists yet/,
  );
});


test("multiple unfinished canary boundaries fail closed", async () => {
  const env = {
    CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32),
    POSTSTEWARD_ORIGIN: "https://staging.example.test",
  };
  const row = { release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 1000, stopped_at: null };
  await assert.rejects(
    observeAdvancedSlo(env, async () => [row, row], async () => {
      throw new Error("must not fetch readiness for duplicate active runs");
    }),
    /Multiple active Advanced canary run boundaries exist/,
  );
});

test("an active database cohort must match the actual staging release and policy", async () => {
  const env = {
    CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32),
    POSTSTEWARD_ORIGIN: "https://staging.example.test",
  };
  const row = { release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 1000, stopped_at: null };
  await assert.rejects(
    observeAdvancedSlo(env, async () => [row], async () => ({
      environment: "staging",
      release: "b".repeat(40),
      runtimeCapabilities: { policies: { advancedEnabled: true,
        advancedRolloutMode: "canary", advancedCanaryBps: 100 } },
    })),
    /Active canary release differs from hosted staging/,
  );
  await assert.rejects(
    observeAdvancedSlo(env, async () => [row], async () => ({
      environment: "staging",
      release: "a".repeat(40),
      runtimeCapabilities: { policies: { advancedEnabled: false,
        advancedRolloutMode: "disabled", advancedCanaryBps: 0 } },
    })),
    /Active canary boundary differs from hosted rollout policy/,
  );
});

test("matching release and canary policy from production cannot be labelled staging evidence", async () => {
  const env = {
    CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32),
    POSTSTEWARD_ORIGIN: "https://staging.example.test",
  };
  const row = { release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 1000, stopped_at: null };
  await assert.rejects(
    observeAdvancedSlo(env, async () => [row], async () => ({
      environment: "production",
      release: row.release,
      runtimeCapabilities: { policies: { advancedEnabled: true,
        advancedRolloutMode: "canary", advancedCanaryBps: 100 } },
    })),
    /Active canary readiness is not the staging environment/,
  );
  await assert.rejects(
    observeAdvancedSlo(env, async () => [row], async () => ({
      release: row.release,
      runtimeCapabilities: { policies: { advancedEnabled: true,
        advancedRolloutMode: "canary", advancedCanaryBps: 100 } },
    })),
    /Active canary readiness is not the staging environment/,
  );
});

test("unexpected evaluator exit statuses cannot be reported as a healthy observation", () => {
  const workflow = readFileSync(new URL("../.github/workflows/staging-advanced-slo-observe.yml", import.meta.url), "utf8");
  assert.match(workflow, /if \[\[ \$status -ne 0 \]\]; then\s+echo "Unexpected SLO evaluator exit status: \$status"/);
  assert.match(workflow, /if \[\[ \$status -eq 2 \]\]; then\s+echo "Advanced canary evidence is valid but not promotion-ready yet."\s+exit 0/);
  assert.match(workflow, /if \[\[ \$status -ne 0 \]\]; then[\s\S]*?exit 1/);
});

test("malformed OAuth success count is rejected before a 100% ratio can be published", () => {
  const input = {
    run: { release: "a".repeat(40), canary_bps: 100, started_at: 1000, stopped_at: null },
    observedAt: 90_000_000,
    eventCounts: { oauth_refresh_attempt: 1, oauth_refresh_success: 2 },
    workspaceCount: 1,
    scheduleDurations: [], providerReconciliationDurations: [], webhookDurations: [],
    duplicateExternalEffects: null,
  };
  assert.throws(() => buildAdvancedSloObservation(input), /OAuth success events exceed genuine refresh attempts/);
});

test("all Advanced event-derived cohort queries must constrain matching basis points", async () => {
  const env = {
    CLOUDFLARE_ACCOUNT_ID: "a".repeat(32), D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32), POSTSTEWARD_ORIGIN: "https://staging.example.test",
  };
  const run = { id: "run1", release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 1000, stopped_at: null };
  const queries = [];
  await observeAdvancedSlo(env, async (_a, _d, _t, sql, params = []) => {
    queries.push({sql, params});
    if (sql.includes("FROM advanced_canary_runs")) return [run];
    if (sql.includes("FROM advanced_slo_events") || sql.includes("FROM stripe_events")) {
      assert.match(sql, /canary_bps=\?/);
      assert.ok(params.includes(100), "active 100-bps cohort must scope every event query");
    }
    if (sql.includes("FROM stripe_events")) return [];
    return [];
  }, async () => ({
    environment: "staging", release: run.release,
    runtimeCapabilities: { policies: { advancedEnabled: true, advancedRolloutMode: "canary", advancedCanaryBps: 100 } },
  }));
  assert.ok(queries.filter(x => x.sql.includes("FROM advanced_slo_events")).length >= 3);
});

test("SLO workflow isolates every observation file to one run attempt", () => {
  const workflow = readFileSync(new URL("../.github/workflows/staging-advanced-slo-observe.yml", import.meta.url), "utf8");
  assert.match(workflow, /POSTSTEWARD_SLO_OUTPUT:.*\/tmp\/poststeward-advanced-slo-.*run_id.*run_attempt/);
  assert.match(workflow, /\[\[ ! -f "\$POSTSTEWARD_SLO_OUTPUT" \]\]/);
  assert.doesNotMatch(workflow, /POSTSTEWARD_SLO_OUTPUT: \/tmp\/poststeward-advanced-slo\.json/);
});
