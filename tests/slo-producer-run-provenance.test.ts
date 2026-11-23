import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { advancedCanaryBucket } from "../src/advanced-rollout.ts";
import { recordAdvancedSloEvent } from "../src/slo-telemetry.ts";

function fixture() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync("migrations/0005_external_effect_ledger.sql", "utf8"));
  db.exec(
    readFileSync("migrations/0014_advanced_slo_observations.sql", "utf8"),
  );
  db.exec(
    readFileSync("migrations/0021_advanced_slo_receipt_provenance.sql", "utf8"),
  );
  const seed = "staging-observation-seed";
  const release = "a".repeat(40);
  const bps = 1000;
  let workspace = "";
  for (let i = 0; i < 100000; i++) {
    const candidate = "workspace-" + i;
    if (advancedCanaryBucket(candidate, seed) < bps) {
      workspace = candidate;
      break;
    }
  }
  assert.ok(workspace);
  const env: any = {
    DEPLOY_ENV: "staging",
    RELEASE_SHA: release,
    ADVANCED_ENABLED: "true",
    ADVANCED_ROLLOUT_MODE: "canary",
    ADVANCED_CANARY_BPS: String(bps),
    ADVANCED_CANARY_SEED: seed,
    IDENTITY: {
      prepare(sql: string) {
        return {
          bind(...params: unknown[]) {
            return {
              async run() {
                const result = db.prepare(sql).run(...(params as any[]));
                return { meta: { changes: result.changes } };
              },
            };
          },
        };
      },
    },
  };
  const seedHash = createHash("sha256").update(seed).digest("hex");
  const addRun = (id: string, hash = seedHash, runBps = bps) => {
    db.prepare(
      `INSERT INTO advanced_canary_runs
      (id,release,canary_bps,seed_hash,started_at,stopped_at,created_at)
      VALUES (?,?,?,?,100,NULL,100)`,
    ).run(id, release, runBps, hash);
  };
  return { db, env, workspace, addRun };
}

test("no active matching release/seed/bps cannot create evidence", async () => {
  const f = fixture();
  const event = () =>
    recordAdvancedSloEvent(
      f.env,
      f.workspace,
      "advanced_operation",
      {
        dedupeKey: "operation-1",
        evidenceOrigin: "completed_operation",
        evidenceOutcome: "succeeded",
      },
      400,
    );
  assert.equal(await event(), false);
  f.addRun("wrong-seed", "0".repeat(64));
  assert.equal(await event(), false);
  f.db.prepare("UPDATE advanced_canary_runs SET stopped_at=500").run();
  f.addRun("wrong-bps", undefined, 500);
  assert.equal(await event(), false);
  assert.equal(
    f.db.prepare("SELECT count(*) AS n FROM advanced_slo_events").get()?.n,
    0,
  );
});

test("new complete receipts are tied to run and are idempotent on retry", async () => {
  const f = fixture();
  f.addRun("active-1");
  const event = (key: string) =>
    recordAdvancedSloEvent(
      f.env,
      f.workspace,
      "advanced_operation",
      {
        dedupeKey: key,
        evidenceOrigin: "completed_operation",
        evidenceOutcome: "succeeded",
      },
      400,
    );
  assert.equal(await event("operation-1"), true);
  assert.equal(await event("operation-1"), false);
  assert.equal(await event("operation-2"), true);
  const rows = f.db
    .prepare(
      "SELECT canary_run_id,evidence_origin,evidence_outcome,reference_hash FROM advanced_slo_events ORDER BY rowid",
    )
    .all();
  assert.equal(rows.length, 2);
  assert.equal(rows[0].canary_run_id, "active-1");
  assert.equal(rows[0].evidence_origin, "completed_operation");
  assert.equal(rows[0].evidence_outcome, "succeeded");
  assert.match(String(rows[0].reference_hash), /^[a-f0-9]{64}$/);
  f.db
    .prepare(
      "UPDATE advanced_canary_runs SET stopped_at=500 WHERE id='active-1'",
    )
    .run();
  f.addRun("active-2");
  assert.equal(await event("operation-1"), true);
  assert.equal(
    f.db.prepare("SELECT count(*) AS n FROM advanced_slo_events").get()?.n,
    3,
  );
});

test("derived campaign, allocation, metrics or unidentified requests never qualify as operations", async () => {
  const f = fixture();
  f.addRun("active-1");
  for (const [origin, key] of [
    ["observed_campaign", "campaign-1"],
    ["observed_allocation", "allocation-1"],
    ["observed_metrics", "metrics-1"],
    ["unidentified_operation", undefined],
  ] as const) {
    assert.equal(
      await recordAdvancedSloEvent(f.env, f.workspace, "advanced_operation", {
        dedupeKey: key,
        evidenceOrigin: origin,
        evidenceOutcome: "succeeded",
      }),
      false,
    );
  }
  assert.equal(
    f.db.prepare("SELECT count(*) AS n FROM advanced_slo_events").get()?.n,
    0,
  );
});

test("ambiguous active runs and pre-start observations are not promoted", async () => {
  const f = fixture();
  f.addRun("active-1");
  f.addRun("active-2");
  const event = (now: number) =>
    recordAdvancedSloEvent(
      f.env,
      f.workspace,
      "advanced_operation",
      {
        dedupeKey: "operation-3",
        evidenceOrigin: "completed_operation",
        evidenceOutcome: "succeeded",
      },
      now,
    );
  assert.equal(await event(400), false);
  f.db
    .prepare(
      "UPDATE advanced_canary_runs SET stopped_at=500 WHERE id='active-2'",
    )
    .run();
  assert.equal(await event(50), false);
  assert.equal(await event(400), true);
  assert.equal(
    f.db.prepare("SELECT count(*) AS n FROM advanced_slo_events").get()?.n,
    1,
  );
});

test("favourable scheduling and OAuth observations require their own trusted outcome", async () => {
  const f = fixture();
  f.addRun("active-1");
  for (const [eventType, origin, outcome] of [
    ["publication_eligible", "provider_write_completed", "succeeded"],
    ["schedule_observation", "due_delivery", "eligible"],
    ["provider_reconciliation", "observed_allocation", "observed"],
    ["oauth_refresh_success", "oauth_attempt", "attempted"],
  ] as const) {
    assert.equal(
      await recordAdvancedSloEvent(
        f.env,
        f.workspace,
        eventType,
        {
          dedupeKey: "false-positive",
          evidenceOrigin: origin,
          evidenceOutcome: outcome,
        },
        400,
      ),
      false,
    );
  }
  assert.equal(
    f.db.prepare("SELECT count(*) AS n FROM advanced_slo_events").get()?.n,
    0,
  );
});
