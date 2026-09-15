import type { Env } from "./types.ts";

const observationLimit = 500;
const retainedDays = 31;

function utcDay(now = Date.now()) {
  return new Date(now).toISOString().slice(0, 10);
}

async function sha256(value: string) {
  const bytes = new Uint8Array(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
  );
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function initialiseCapacityTelemetry(ctx: DurableObjectState) {
  ctx.storage.sql.exec(
    `CREATE TABLE IF NOT EXISTS capacity_telemetry_daily(
      day TEXT PRIMARY KEY,
      requests INTEGER NOT NULL DEFAULT 0,
      alarm_cycles INTEGER NOT NULL DEFAULT 0
    )`,
  );
}

function pruneLocalTelemetry(ctx: DurableObjectState, now = Date.now()) {
  const cutoff = new Date(now - 2 * 86400000).toISOString().slice(0, 10);
  ctx.storage.sql.exec("DELETE FROM capacity_telemetry_daily WHERE day < ?", cutoff);
}

export function recordWorkspaceRequest(ctx: DurableObjectState, now = Date.now()) {
  initialiseCapacityTelemetry(ctx);
  ctx.storage.sql.exec(
    `INSERT INTO capacity_telemetry_daily(day,requests,alarm_cycles)
     VALUES (?,1,0)
     ON CONFLICT(day) DO UPDATE SET requests=requests+1`,
    utcDay(now),
  );
  pruneLocalTelemetry(ctx, now);
}

export function recordAlarmCycle(ctx: DurableObjectState, now = Date.now()) {
  initialiseCapacityTelemetry(ctx);
  ctx.storage.sql.exec(
    `INSERT INTO capacity_telemetry_daily(day,requests,alarm_cycles)
     VALUES (?,0,1)
     ON CONFLICT(day) DO UPDATE SET alarm_cycles=alarm_cycles+1`,
    utcDay(now),
  );
  pruneLocalTelemetry(ctx, now);
}

function parseRows<T>(ctx: DurableObjectState, prefix: string) {
  return ctx.storage.sql
    .exec<{ value: string }>(
      "SELECT value FROM records WHERE key LIKE ? ORDER BY key",
      `${prefix}%`,
    )
    .toArray()
    .flatMap((row) => {
      try {
        return [JSON.parse(row.value) as T];
      } catch {
        return [];
      }
    });
}

export function workspaceCapacitySnapshot(
  ctx: DurableObjectState,
  release: string,
  now = Date.now(),
) {
  initialiseCapacityTelemetry(ctx);
  const usage = ctx.storage.sql
    .exec<{ records: number; bytes: number }>(
      "SELECT records,bytes FROM record_usage WHERE singleton=1",
    )
    .toArray()[0] || { records: 0, bytes: 0 };
  const maxValue = ctx.storage.sql
    .exec<{ max_value_bytes: number | null }>(
      "SELECT max(length(CAST(value AS BLOB))) AS max_value_bytes FROM records",
    )
    .toArray()[0]?.max_value_bytes || 0;
  const deliveries = parseRows<{
    createdAt?: number;
    status?: string;
  }>(ctx, "delivery:");
  const start = Date.parse(`${utcDay(now)}T00:00:00.000Z`);
  const active = new Set(["scheduled", "executing", "waiting_container"]);
  const daily = ctx.storage.sql
    .exec<{ requests: number; alarm_cycles: number }>(
      "SELECT requests,alarm_cycles FROM capacity_telemetry_daily WHERE day=?",
      utcDay(now),
    )
    .toArray()[0] || { requests: 0, alarm_cycles: 0 };
  const profiles = ctx.storage.sql
    .exec<{ count: number }>(
      "SELECT count(*) AS count FROM records WHERE key LIKE 'profile:%'",
    )
    .toArray()[0]?.count || 0;
  return {
    schemaVersion: 1,
    observedAt: now,
    release,
    records: Number(usage.records || 0),
    bytes: Number(usage.bytes || 0),
    maxValueBytes: Number(maxValue || 0),
    dailyDeliveries: deliveries.filter(
      (item) => Number.isFinite(item.createdAt) && Number(item.createdAt) >= start,
    ).length,
    activeSchedules: deliveries.filter((item) => active.has(item.status || "")).length,
    sourceProfiles: Number(profiles),
    workspaceRequests: Number(daily.requests || 0),
    alarmCycles: Number(daily.alarm_cycles || 0),
  };
}

async function snapshotWorkspace(env: Env, workspace: string) {
  const stub = env.WORKSPACES.get(env.WORKSPACES.idFromName(workspace));
  const response = await stub.fetch("https://workspace.internal/capacity/snapshot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace }),
  });
  if (!response.ok) {
    await response.body?.cancel().catch(() => {});
    throw new Error(`capacity snapshot failed with ${response.status}`);
  }
  return response.json<any>();
}

export async function sweepWorkspaceCapacityObservations(
  env: Env,
  now = Date.now(),
) {
  const count = await env.IDENTITY.prepare(
    "SELECT count(*) AS count FROM workspace_registry",
  ).first<{ count: number }>();
  const total = Number(count?.count || 0);
  const rows = await env.IDENTITY.prepare(
    "SELECT workspace FROM workspace_registry ORDER BY workspace LIMIT ?",
  )
    .bind(observationLimit)
    .all<{ workspace: string }>();
  let observed = 0;
  let failed = 0;
  const day = utcDay(now);

  for (const row of rows.results || []) {
    try {
      const snapshot = await snapshotWorkspace(env, row.workspace);
      const fingerprint = (await sha256(row.workspace)).slice(0, 24);
      await env.IDENTITY.prepare(
        `INSERT INTO workspace_capacity_observations(
          workspace_fingerprint,observation_date,observed_at,release,records,bytes,
          max_value_bytes,daily_deliveries,active_schedules,source_profiles,
          workspace_requests,alarm_cycles
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(workspace_fingerprint,observation_date) DO UPDATE SET
          observed_at=max(observed_at,excluded.observed_at),
          release=excluded.release,
          records=max(records,excluded.records),
          bytes=max(bytes,excluded.bytes),
          max_value_bytes=max(max_value_bytes,excluded.max_value_bytes),
          daily_deliveries=max(daily_deliveries,excluded.daily_deliveries),
          active_schedules=max(active_schedules,excluded.active_schedules),
          source_profiles=max(source_profiles,excluded.source_profiles),
          workspace_requests=max(workspace_requests,excluded.workspace_requests),
          alarm_cycles=max(alarm_cycles,excluded.alarm_cycles)`,
      )
        .bind(
          fingerprint,
          day,
          snapshot.observedAt,
          snapshot.release,
          snapshot.records,
          snapshot.bytes,
          snapshot.maxValueBytes,
          snapshot.dailyDeliveries,
          snapshot.activeSchedules,
          snapshot.sourceProfiles,
          snapshot.workspaceRequests,
          snapshot.alarmCycles,
        )
        .run();
      observed++;
    } catch {
      failed++;
    }
  }

  const cutoff = new Date(now - retainedDays * 86400000)
    .toISOString()
    .slice(0, 10);
  await env.IDENTITY.prepare(
    "DELETE FROM workspace_capacity_observations WHERE observation_date < ?",
  )
    .bind(cutoff)
    .run();

  return {
    totalRegistered: total,
    considered: rows.results?.length || 0,
    observed,
    failed,
    complete: total <= observationLimit && observed + failed === total,
  };
}
