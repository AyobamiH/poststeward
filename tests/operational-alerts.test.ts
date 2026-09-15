import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { Miniflare, convertV4MiniflareOptions, Response as RuntimeResponse } from "miniflare";
import {
  enqueueOperationalAlert,
  flushOperationalAlerts,
  sweepOperationalConditions,
} from "../src/operational-alerts.ts";

async function fixture() {
  const mf = new Miniflare(
    convertV4MiniflareOptions({
      modules: true,
      script: "export default { fetch(){ return new Response('ok') } }",
      compatibilityDate: "2026-09-09",
      compatibilityFlags: ["nodejs_compat"],
      d1Databases: { IDENTITY: "alerts-test" },
    }),
  );
  const db = await mf.getD1Database("IDENTITY");
  for (const file of [
    "migrations/0005_external_effect_ledger.sql",
    "migrations/0006_workspace_recovery.sql",
    "migrations/0012_operational_alerts.sql",
  ])
    for (const statement of readFileSync(file, "utf8")
      .split(";")
      .map((value) => value.trim())
      .filter(Boolean))
      await db.prepare(statement).run();
  const env = {
    IDENTITY: db,
    RELEASE_SHA: "a".repeat(40),
  } as any;
  return { mf, db, env };
}

async function rows(db: D1Database) {
  const result = await db
    .prepare("SELECT * FROM operational_alerts ORDER BY created_at,id")
    .all<any>();
  return result.results;
}

test("same-severity alert deduplicates without storing raw subject", async () => {
  const { mf, db, env } = await fixture();
  try {
    const now = Date.UTC(2026, 8, 15, 12);
    const first = await enqueueOperationalAlert(
      env,
      {
        class: "recovery_state",
        severity: "warning",
        code: "RECOVERY_PREPARED_STALE",
        subject: "workspace-secret-id:plan-secret-id",
        dedupe: "workspace-secret-id:plan-secret-id:prepared",
      },
      now,
    );
    const second = await enqueueOperationalAlert(
      env,
      {
        class: "recovery_state",
        severity: "warning",
        code: "RECOVERY_PREPARED_STALE",
        subject: "workspace-secret-id:plan-secret-id",
        dedupe: "workspace-secret-id:plan-secret-id:prepared",
      },
      now + 1000,
    );
    const stored = await rows(db);
    assert.equal(stored.length, 1);
    assert.equal(stored[0].occurrences, 2);
    assert.equal(first.id, second.id);
    assert.equal(stored[0].subject_fingerprint.length, 24);
    assert.ok(!JSON.stringify(stored).includes("workspace-secret-id"));
    assert.ok(!JSON.stringify(stored).includes("plan-secret-id"));
  } finally {
    await mf.dispose();
  }
});

test("critical escalation becomes a distinct durable alert event", async () => {
  const { mf, db, env } = await fixture();
  try {
    const now = Date.UTC(2026, 8, 15, 12);
    const warning = await enqueueOperationalAlert(
      env,
      {
        class: "recovery_state",
        severity: "warning",
        code: "RECOVERY_PREPARED_STALE",
        subject: "workspace:plan",
        dedupe: "workspace:plan:prepared",
      },
      now,
    );
    const critical = await enqueueOperationalAlert(
      env,
      {
        class: "recovery_state",
        severity: "critical",
        code: "RECOVERY_PREPARED_STALE",
        subject: "workspace:plan",
        dedupe: "workspace:plan:prepared",
      },
      now + 1000,
    );
    assert.notEqual(warning.id, critical.id);
    const stored = await rows(db);
    assert.equal(stored.length, 2);
    assert.deepEqual(
      stored.map((row) => row.severity).sort(),
      ["critical", "warning"],
    );
  } finally {
    await mf.dispose();
  }
});

test("no configured delivery destination leaves durable alerts pending", async () => {
  const { mf, db, env } = await fixture();
  try {
    const now = Date.UTC(2026, 8, 15, 12);
    await enqueueOperationalAlert(
      env,
      {
        class: "stripe_reconciliation",
        severity: "critical",
        code: "STRIPE_EVENT_RECONCILIATION_STALE",
        subject: "workspace:event",
      },
      now,
    );
    const result = await flushOperationalAlerts(env, { now });
    assert.deepEqual(result, {
      configured: false,
      considered: 0,
      claimed: 0,
      sent: 0,
      retried: 0,
      dead: 0,
    });
    assert.equal((await rows(db))[0].status, "pending");
  } finally {
    await mf.dispose();
  }
});

test("successful delivery is idempotency-keyed and contains only bounded evidence", async () => {
  const { mf, db, env } = await fixture();
  try {
    const now = Date.UTC(2026, 8, 15, 12);
    env.OPERATIONAL_ALERT_WEBHOOK_URL = "https://alerts.example/hooks/private";
    env.OPERATIONAL_ALERT_WEBHOOK_TOKEN = "test-alert-token-value";
    const alert = await enqueueOperationalAlert(
      env,
      {
        class: "provider_or_oauth_failure",
        severity: "critical",
        code: "AMBIGUOUS_PROVIDER_EFFECT",
        subject: "workspace-secret-id:fingerprint-secret",
      },
      now,
    );
    const calls: any[] = [];
    const result = await flushOperationalAlerts(env, {
      now,
      send: async (url, init) => {
        calls.push({ url: String(url), init });
        return new RuntimeResponse(null, { status: 204 });
      },
    });
    assert.equal(result.sent, 1);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].init.headers["Idempotency-Key"], alert.id);
    assert.equal(
      calls[0].init.headers.Authorization,
      "Bearer test-alert-token-value",
    );
    const body = JSON.parse(calls[0].init.body);
    assert.equal(body.alertId, alert.id);
    assert.equal(body.code, "AMBIGUOUS_PROVIDER_EFFECT");
    assert.ok(!calls[0].init.body.includes("workspace-secret-id"));
    assert.ok(!calls[0].init.body.includes("fingerprint-secret"));
    const stored = await rows(db);
    assert.equal(stored[0].status, "sent");
    assert.equal(stored[0].attempts, 1);
    assert.equal(stored[0].last_http_status, 204);
  } finally {
    await mf.dispose();
  }
});

test("retryable webhook failure releases the lease with bounded backoff", async () => {
  const { mf, db, env } = await fixture();
  try {
    const now = Date.UTC(2026, 8, 15, 12);
    env.OPERATIONAL_ALERT_WEBHOOK_URL = "https://alerts.example/hooks/private";
    await enqueueOperationalAlert(
      env,
      {
        class: "recovery_state",
        severity: "warning",
        code: "RECOVERY_RECONCILED_STALE",
        subject: "workspace:plan",
      },
      now,
    );
    const result = await flushOperationalAlerts(env, {
      now,
      send: async () =>
        new RuntimeResponse(null, {
          status: 503,
          headers: { "Retry-After": "120" },
        }),
    });
    assert.equal(result.retried, 1);
    const stored = (await rows(db))[0];
    assert.equal(stored.status, "pending");
    assert.equal(stored.attempts, 1);
    assert.equal(stored.lease_until, null);
    assert.equal(stored.next_attempt_at, now + 120_000);
    assert.equal(stored.last_error_code, "WEBHOOK_RETRYABLE_HTTP");
  } finally {
    await mf.dispose();
  }
});

test("permanent webhook rejection is dead-lettered without retry", async () => {
  const { mf, db, env } = await fixture();
  try {
    const now = Date.UTC(2026, 8, 15, 12);
    env.OPERATIONAL_ALERT_WEBHOOK_URL = "https://alerts.example/hooks/private";
    await enqueueOperationalAlert(
      env,
      {
        class: "capacity_threshold",
        severity: "warning",
        code: "CAPACITY_THRESHOLD_EXCEEDED",
      },
      now,
    );
    const result = await flushOperationalAlerts(env, {
      now,
      send: async () => new RuntimeResponse(null, { status: 400 }),
    });
    assert.equal(result.dead, 1);
    const stored = (await rows(db))[0];
    assert.equal(stored.status, "dead");
    assert.equal(stored.last_http_status, 400);
    assert.equal(stored.last_error_code, "WEBHOOK_PERMANENT_HTTP");
  } finally {
    await mf.dispose();
  }
});

test("expired sending lease is safely reclaimed using the same alert id", async () => {
  const { mf, db, env } = await fixture();
  try {
    const now = Date.UTC(2026, 8, 15, 12);
    env.OPERATIONAL_ALERT_WEBHOOK_URL = "https://alerts.example/hooks/private";
    const alert = await enqueueOperationalAlert(
      env,
      {
        class: "provider_or_oauth_failure",
        severity: "critical",
        code: "OAUTH_REFRESH_FAILED",
        subject: "workspace:account",
      },
      now,
    );
    await db
      .prepare(
        "UPDATE operational_alerts SET status='sending',lease_until=?,attempts=1 WHERE id=?",
      )
      .bind(now - 1, alert.id)
      .run();
    let idempotencyKey = "";
    const result = await flushOperationalAlerts(env, {
      now,
      send: async (_url, init: any) => {
        idempotencyKey = init.headers["Idempotency-Key"];
        return new RuntimeResponse(null, { status: 204 });
      },
    });
    assert.equal(result.sent, 1);
    assert.equal(idempotencyKey, alert.id);
    const stored = (await rows(db))[0];
    assert.equal(stored.status, "sent");
    assert.equal(stored.attempts, 2);
  } finally {
    await mf.dispose();
  }
});

test("condition sweep converts stale recovery and ambiguous effects into deduplicated alerts", async () => {
  const { mf, db, env } = await fixture();
  try {
    const now = Date.UTC(2026, 8, 15, 12);
    const old = now - 20 * 60_000;
    await db
      .prepare(
        "INSERT INTO workspace_recovery_plans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
      )
      .bind(
        "plan-1",
        "workspace-1",
        "owner",
        old,
        "target-bookmark",
        "pre-bookmark",
        null,
        "test",
        "a".repeat(64),
        "armed",
        old,
        now + 60_000,
        old,
      )
      .run();
    await db
      .prepare(
        "INSERT INTO external_effects(workspace,fingerprint,delivery_id,provider,text_digest,status,created_at,updated_at) VALUES (?,?,?,?,?,'uncertain',?,?)",
      )
      .bind(
        "workspace-1",
        "effect-fingerprint",
        "delivery-1",
        "x",
        "b".repeat(64),
        old,
        old,
      )
      .run();
    const result = await sweepOperationalConditions(env, now);
    assert.equal(result.queued, 2);
    const stored = await rows(db);
    assert.deepEqual(
      stored.map((row) => row.code).sort(),
      ["AMBIGUOUS_PROVIDER_EFFECT", "RECOVERY_ARMED_STALE"],
    );
    assert.ok(stored.every((row) => row.subject_fingerprint?.length === 24));
  } finally {
    await mf.dispose();
  }
});
