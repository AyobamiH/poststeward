import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const migration = readFileSync(
  "migrations/0013_workspace_capacity_observations.sql",
  "utf8",
);
const capacity = readFileSync("src/capacity-observation.ts", "utf8");
const edge = readFileSync("src/canary-edge.ts", "utf8");
const wrangler = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));

test("capacity observation table stores fingerprint and aggregates, never raw workspace id", () => {
  assert.match(migration, /workspace_fingerprint TEXT NOT NULL/);
  assert.doesNotMatch(migration, /\bworkspace\s+TEXT/i);
  for (const column of [
    "records",
    "bytes",
    "max_value_bytes",
    "daily_deliveries",
    "active_schedules",
    "source_profiles",
    "workspace_requests",
    "alarm_cycles",
  ])
    assert.ok(migration.includes(column));
});

test("capacity sweep hashes registry identity before writing observations", () => {
  assert.match(capacity, /sha256\(row\.workspace\)/);
  assert.match(capacity, /\.slice\(0, 24\)/);
  assert.match(capacity, /workspace_capacity_observations/);
  assert.doesNotMatch(
    capacity,
    /INSERT INTO workspace_capacity_observations\([^)]*\bworkspace\b(?!_fingerprint)/s,
  );
});

test("capacity observation uses hourly sampling plus one current-release bootstrap on the existing five-minute loop", () => {
  assert.deepEqual(wrangler.triggers.crons.sort(), ["*/5 * * * *", "17 * * * *"]);
  assert.match(edge, /controller\.cron === "17 \* \* \* \*"/);
  assert.match(edge, /controller\.cron === "\*\/5 \* \* \* \*"/);
  assert.match(edge, /capacityObservationDue\(env\)/);
  assert.match(edge, /sweepWorkspaceCapacityObservations\(env\)/);
  assert.match(capacity, /WHERE observation_date=\? AND release=\?/);
});

test("internal capacity snapshot is not exposed publicly and does not manufacture workspace identity", () => {
  assert.match(edge, /path === "\/capacity\/snapshot"/);
  assert.match(edge, /workspace && requested !== workspace/);
  assert.match(edge, /workspaceCapacitySnapshot/);
  assert.doesNotMatch(edge, /capacity\/snapshot[\\s\\S]{0,1600}put\(["']workspace["']/);
  const publicWorker = readFileSync("src/worker.ts", "utf8");
  assert.doesNotMatch(publicWorker, /\/api\/capacity\/snapshot/);
});

test("capacity sweep excludes completed deletion tombstones before Durable Object observation", () => {
  assert.match(capacity, /workspace_deletions deletion/);
  assert.match(capacity, /deletion\.state='completed'/);
  assert.match(capacity, /WHERE deletion\.workspace IS NULL/);
});


test("capacity sweep is fault-isolated from hourly identity maintenance", () => {
  assert.match(edge, /hourly_identity_maintenance_failed/);
  assert.match(edge, /workspace_capacity_observation_failed/);
  const maintenance = edge.indexOf("await edge.scheduled(controller, env)");
  const capacitySweep = edge.indexOf("sweepWorkspaceCapacityObservations(env)");
  assert.ok(maintenance >= 0 && capacitySweep > maintenance);
  assert.match(edge.slice(maintenance - 120, capacitySweep + 120), /try[\s\S]*catch[\s\S]*sweepWorkspaceCapacityObservations/);
});
