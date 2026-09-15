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

test("capacity observation uses the established hourly schedule without adding a third trigger", () => {
  assert.deepEqual(wrangler.triggers.crons.sort(), ["*/5 * * * *", "17 * * * *"]);
  assert.match(edge, /controller\.cron === "17 \* \* \* \*"/);
  assert.match(edge, /sweepWorkspaceCapacityObservations\(env\)/);
});

test("internal capacity snapshot is not exposed as a public Worker route", () => {
  assert.match(edge, /path === "\/capacity\/snapshot"/);
  assert.match(edge, /input\.workspace !== workspace/);
  const publicWorker = readFileSync("src/worker.ts", "utf8");
  assert.doesNotMatch(publicWorker, /\/api\/capacity\/snapshot/);
});
