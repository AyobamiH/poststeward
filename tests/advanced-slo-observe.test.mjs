import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  buildAdvancedSloObservation,
  observeAdvancedSlo,
  parseD1Rows,
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
  assert.ok(report.promotion.blockers.includes("duplicate_external_effect_evidence_unverified"));
  assert.ok(report.promotion.blockers.includes("recovery_evidence_missing"));
  assert.ok(report.promotion.blockers.includes("publication_provenance_unverified"));
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
    POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    DEPLOY_ENV: "staging",
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
    POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    DEPLOY_ENV: "staging",
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
    POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    DEPLOY_ENV: "staging",
  };
  const row = { release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 1000, stopped_at: null,
    id: "run-proof-1", seed_hash: "b".repeat(64) };
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
    POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    DEPLOY_ENV: "staging",
  };
  const row = { release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 1000, stopped_at: null,
    id: "run-proof-1", seed_hash: "b".repeat(64) };
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
    POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    DEPLOY_ENV: "staging",
  };
  const row = { release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 1000, stopped_at: null,
    id: "run-proof-1", seed_hash: "b".repeat(64) };
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
    CLOUDFLARE_API_TOKEN: "t".repeat(32), POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    DEPLOY_ENV: "staging",
  };
  const run = { release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 1000, stopped_at: null,
    id: "run-proof-1", seed_hash: "b".repeat(64) };
  const queries = [];
  await observeAdvancedSlo(env, async (_a, _d, _t, sql, params = []) => {
    queries.push({sql, params});
    if (sql.includes("FROM advanced_canary_runs")) return [run];
    if (sql.includes("FROM advanced_slo_events") || sql.includes("FROM stripe_events")) {
      assert.match(sql, /canary_bps=\?/);
      assert.ok(params.includes(100), "active 100-bps cohort must scope every event query");
    }
    if (sql.includes("count(DISTINCT workspace)")) return [{ count: 0 }];
    if (sql.includes("FROM stripe_events")) return [];
    return [];
  }, async () => ({
    environment: "staging", release: run.release,
    runtimeCapabilities: { policies: { advancedEnabled: true, advancedRolloutMode: "canary", advancedCanaryBps: 100 } },
  }));
  assert.ok(queries.filter(x => x.sql.includes("FROM advanced_slo_events")).length >= 2);
  assert.equal(queries.some(x => x.sql.includes("FROM stripe_events")), false,
    "No eligible workspace must mean no borrowed webhook evidence.");
});

test("SLO workflow isolates every observation file to one run attempt", () => {
  const workflow = readFileSync(new URL("../.github/workflows/staging-advanced-slo-observe.yml", import.meta.url), "utf8");
  assert.match(workflow, /APP_ORIGIN: https:\/\/poststeward-staging\.woeinvests\.workers\.dev/);
  assert.match(workflow, /POSTSTEWARD_ORIGIN: https:\/\/poststeward-staging\.woeinvests\.workers\.dev/);
  assert.doesNotMatch(workflow, /POSTSTEWARD_ORIGIN:.*vars\.APP_ORIGIN/);
  assert.match(workflow, /POSTSTEWARD_SLO_OUTPUT:.*\/tmp\/poststeward-advanced-slo-.*run_id.*run_attempt/);
  assert.match(workflow, /\[\[ ! -f "\$POSTSTEWARD_SLO_OUTPUT" \]\]/);
  assert.doesNotMatch(workflow, /POSTSTEWARD_SLO_OUTPUT: \/tmp\/poststeward-advanced-slo\.json/);
});

test("Cloudflare D1 result batches must explicitly succeed", () => {
  assert.throws(() => parseD1Rows({ success: true, result: [{ success: false, results: [] }] }), /batch reported failure/);
  assert.throws(() => parseD1Rows({ success: true, result: [{ results: [] }] }), /batch reported failure/);
  assert.throws(() => parseD1Rows({ success: false, result: [{ success: true, results: [] }] }), /did not confirm success/);
  assert.deepEqual(parseD1Rows({ success: true, result: [{ success: true, results: [] }] }), []);
});

test("a non-canonical APP_ORIGIN cannot manufacture staging evidence", async () => {
  const env = { CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32), DEPLOY_ENV: "staging",
    APP_ORIGIN: "https://unreviewed.example.test",
    POSTSTEWARD_ORIGIN: "https://unreviewed.example.test" };
  const run = { id: "run-proof-1", seed_hash: "b".repeat(64),
    release: "a".repeat(40), canary_bps: 100, started_at: Date.now() - 1000,
    stopped_at: null };
  await assert.rejects(observeAdvancedSlo(env, async () => [run],
    async () => { throw Error("readiness must not fetch unreviewed origin"); }),
    /APP_ORIGIN does not identify the canonical staging application/);
});

test("a cross-workspace collage never satisfies a real 20-operation journey", async () => {
  const env = { CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32), DEPLOY_ENV: "staging",
    APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev" };
  const run = { id: "run-proof-1", seed_hash: "b".repeat(64),
    release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 86_400_000, stopped_at: null };
  const milestoneTypes = ["source_change", "inventory_snapshot", "spaced_allocation",
    "publication_eligible", "verified_readback", "scheduled_metrics_capture"];
  const grouped = [{ workspace: "ops-workspace", event_type: "advanced_operation", count: 20 },
    ...milestoneTypes.map((type, i) => ({ workspace: "other-" + i, event_type: type, count: 1 }))];
  const observed = await observeAdvancedSlo(env, async (_a, _d, _t, sql, params) => {
    if (sql.includes("FROM advanced_canary_runs")) return [run];
    if (sql.includes("GROUP BY workspace,event_type")) {
      assert.ok(params.includes(run.id));
      assert.match(sql, /canary_run_id=\\?/);
      assert.match(sql, /evidence_origin/);
      return grouped;
    }
    if (sql.includes("count(DISTINCT workspace)")) return [{ count: 7 }];
    return [];
  }, async () => ({ environment: "staging", release: run.release,
    runtimeCapabilities: { policies: { advancedEnabled: true,
      advancedRolloutMode: "canary", advancedCanaryBps: 100 } } }));
  assert.equal(observed.cohort.workspaceCount, 0);
  assert.equal(observed.cohort.advancedOperations, 0);
  assert.equal(evaluateSloObservation(observed).promotion.ready, false);
});

test("incomplete trusted duration denominators are rejected", async () => {
  const env = { CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32), DEPLOY_ENV: "staging",
    APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev" };
  const run = { id: "run-proof-1", seed_hash: "b".repeat(64),
    release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 86_400_000, stopped_at: null };
  const milestones = ["source_change", "inventory_snapshot", "spaced_allocation",
    "publication_eligible", "verified_readback", "scheduled_metrics_capture"];
  const grouped = [{ workspace: "the-workspace", event_type: "advanced_operation", count: 20 },
    ...milestones.map(event_type => ({ workspace: "the-workspace", event_type, count: 1 })),
    { workspace: "the-workspace", event_type: "provider_reconciliation", count: 1 }];
  await assert.rejects(observeAdvancedSlo(env, async (_a, _d, _t, sql) => {
    if (sql.includes("FROM advanced_canary_runs")) return [run];
    if (sql.includes("GROUP BY workspace,event_type")) return grouped;
    if (sql.includes("count(DISTINCT workspace)")) return [{ count: 1 }];
    return [];
  }, async () => ({ environment: "staging", release: run.release,
    runtimeCapabilities: { policies: { advancedEnabled: true,
      advancedRolloutMode: "canary", advancedCanaryBps: 100 } } })),
    /Incomplete provider_reconciliation latency denominator/);
});

test("webhook observations are scoped to the same completed workspace", async () => {
  const env = { CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    D1_ID: "11111111-1111-1111-1111-111111111111",
    CLOUDFLARE_API_TOKEN: "t".repeat(32), DEPLOY_ENV: "staging",
    APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev" };
  const run = { id: "run-proof-1", seed_hash: "b".repeat(64),
    release: "a".repeat(40), canary_bps: 100,
    started_at: Date.now() - 86_400_000, stopped_at: null };
  const milestoneTypes = ["source_change", "inventory_snapshot", "spaced_allocation",
    "publication_eligible", "verified_readback", "scheduled_metrics_capture"];
  const grouped = [
    { workspace: "eligible-workspace", event_type: "advanced_operation", count: 20 },
    ...milestoneTypes.map(event_type => ({
      workspace: "eligible-workspace", event_type, count: 1
    })),
    { workspace: "other-workspace", event_type: "webhook_reconciliation", count: 100 }
  ];
  let webhookQueried = false;
  const observed = await observeAdvancedSlo(env, async (_a, _d, _t, sql, params) => {
    if (sql.includes("FROM advanced_canary_runs")) return [run];
    if (sql.includes("GROUP BY workspace,event_type")) return grouped;
    if (sql.includes("count(DISTINCT workspace)")) return [{ count: 2 }];
    if (sql.includes("FROM stripe_events")) {
      webhookQueried = true;
      assert.ok(sql.includes("workspace IN (?)"));
      assert.deepEqual(params.slice(-1), ["eligible-workspace"]);
      return [{received_at: 100, completed_at: 200}];
    }
    return [];
  }, async () => ({ environment: "staging", release: run.release,
    runtimeCapabilities: { policies: { advancedEnabled: true,
      advancedRolloutMode: "canary", advancedCanaryBps: 100 } } }));
  assert.equal(webhookQueried, true);
  assert.equal(observed.cohort.workspaceCount, 1);
  assert.equal(observed.cohort.advancedOperations, 20);
  assert.equal(observed.webhookReconciliation.samples, 1);
  assert.equal(observed.evidenceCoverage.webhook, false);
  assert.equal(evaluateSloObservation(observed).promotion.ready, false);
});

test("real SQLite provenance joins exclude another run and untrusted receipts", async () => {
  const { DatabaseSync } = await import("node:sqlite");
  const db = new DatabaseSync(":memory:");
  try {
    db.exec(`CREATE TABLE advanced_canary_runs (
      id TEXT, release TEXT, canary_bps INTEGER, seed_hash TEXT,
      started_at INTEGER, stopped_at INTEGER
    );
    CREATE TABLE advanced_slo_events (
      workspace TEXT, event_type TEXT, observed_at INTEGER,
      duration_ms INTEGER, release TEXT, canary_bps INTEGER,
      canary_run_id TEXT, evidence_origin TEXT, evidence_outcome TEXT,
      reference_hash TEXT
    );
    CREATE TABLE stripe_events (
      workspace TEXT, received_at INTEGER, completed_at INTEGER
    );`);
    const now = Date.now();
    const release = "a".repeat(40);
    const seedHash = "b".repeat(64);
    const runId = "run-proof-sql-1";
    const startedAt = now - 86_400_000;
    db.prepare("INSERT INTO advanced_canary_runs VALUES (?,?,?,?,?,NULL)")
      .run(runId, release, 100, seedHash, startedAt);
    const insert = db.prepare(`INSERT INTO advanced_slo_events VALUES (?,?,?,?,?,?,?,?,?,?)`);
    let sequence = 0;
    const event = (workspace, type, ownerRun, origin, outcome, duration=null, reference=null) => {
      sequence += 1;
      insert.run(workspace, type, now - 2000, duration, release, 100,
        ownerRun, origin, outcome,
        reference === null ? sequence.toString(16).padStart(64, "0") : reference);
    };
    for (let i = 0; i < 20; i++) {
      event("real-workspace", "advanced_operation", runId, "completed_operation", "succeeded");
      event("real-workspace", "advanced_operation", "unrelated-previous-run",
        "completed_operation", "succeeded");
    }
    event("real-workspace", "advanced_operation", runId, "legacy_unverified", "unverified");
    event("real-workspace", "advanced_operation", runId, "completed_operation", "succeeded", null, "");
    const milestones = [
      ["source_change", "observed_campaign", "observed"],
      ["inventory_snapshot", "observed_campaign", "observed"],
      ["spaced_allocation", "observed_allocation", "observed"],
      ["publication_eligible", "due_delivery", "eligible"],
      ["verified_readback", "provider_verified", "verified"],
      ["scheduled_metrics_capture", "observed_metrics", "observed"],
      ["schedule_observation", "provider_write_completed", "succeeded"],
      ["provider_reconciliation", "provider_verified", "verified"],
    ];
    for (const [type, origin, outcome] of milestones)
      event("real-workspace", type, runId, origin, outcome,
        ["schedule_observation", "provider_reconciliation"].includes(type) ? 100 : null);
    db.prepare("INSERT INTO stripe_events VALUES (?,?,?)").run("real-workspace", now - 500, now - 400);
    db.prepare("INSERT INTO stripe_events VALUES (?,?,?)").run("another-workspace", now - 500, now - 300);
    const env = {
      CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
      D1_ID: "11111111-1111-1111-1111-111111111111",
      CLOUDFLARE_API_TOKEN: "t".repeat(32),
      DEPLOY_ENV: "staging",
      APP_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
      POSTSTEWARD_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
    };
    const query = async (_a, _d, _t, sql, params=[]) => db.prepare(sql).all(...params);
    const observed = await observeAdvancedSlo(env, query, async () => ({
      environment: "staging",
      release,
      runtimeCapabilities: { policies: {
        advancedEnabled: true, advancedRolloutMode: "canary", advancedCanaryBps: 100,
      } },
    }));
    assert.equal(observed.cohort.runId, runId);
    assert.equal(observed.cohort.seedHash, seedHash);
    assert.equal(observed.cohort.workspaceCount, 1);
    assert.equal(observed.cohort.advancedOperations, 20);
    assert.equal(observed.providerReconciliation.samples, 1);
    assert.equal(observed.schedule.due, 1);
    assert.equal(observed.webhookReconciliation.samples, 1);
    assert.equal(observed.evidenceCoverage.publication, false);
    assert.equal(evaluateSloObservation(observed).promotion.ready, false);
  } finally {
    db.close();
  }
});
