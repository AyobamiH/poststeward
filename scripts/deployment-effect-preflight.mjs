import { readdirSync } from "node:fs";
import { join } from "node:path";

// An ordinary deployment may retain an already-active next writer root, but
// cannot initiate a cutover, replace a root, or roll the writer back to legacy.
// Cutover requires a separately reviewed and explicitly authorised operation.
export function assertNoImplicitRootCutover(current, desired) {
  const mode = desired?.ENCRYPTION_ROOT_WRITE;
  if (mode === "legacy" && (current === null || current?.ENCRYPTION_ROOT_WRITE === "legacy")) return "legacy";
  if (mode === "next" && current?.ENCRYPTION_ROOT_WRITE === "next" &&
      ["ENCRYPTION_LEGACY_ROOT_ID", "ENCRYPTION_NEXT_ROOT_ID"].every(name =>
        /^[a-f0-9]{64}$/.test(current[name] || "") &&
        current[name] === desired[name])) {
    return "retained-next";
  }
  throw new Error("Release held: root-write cutover, rollback or replacement requires separate authority.");
}

export function expectedMigrationNames(db, cwd = process.cwd()) {
  if (db?.migrations_dir !== "migrations" ||
      (db.migrations_table && db.migrations_table !== "d1_migrations") ||
      db.migrations_pattern) {
    throw new Error("Release held: unreviewed D1 migration layout.");
  }
  const names = readdirSync(join(cwd, "migrations"), { withFileTypes: true })
    .filter(file => file.isFile() && file.name.endsWith(".sql"))
    .map(file => file.name)
    .sort();
  if (!names.length || new Set(names).size !== names.length ||
      names.some(name => !/^\d{4}_[a-z0-9_-]+\.sql$/.test(name))) {
    throw new Error("Release held: invalid or missing canonical D1 migrations.");
  }
  return names;
}

// This consumes only a read-only SELECT from the exact remote D1 database.
// A missing ledger, API failure, unknown migration or pending file blocks release.
export function assertCompleteRemoteMigrations(payload, db, cwd = process.cwd()) {
  const expected = expectedMigrationNames(db, cwd);
  if (payload?.success !== true || !Array.isArray(payload.result) ||
      payload.result.length !== 1 || payload.result[0]?.success !== true ||
      !Array.isArray(payload.result[0]?.results) ||
      payload.result[0]?.meta?.rows_written !== 0) {
    throw new Error("Release held: remote D1 migration evidence is unavailable or mutated.");
  }
  const actual = payload.result[0].results.map(row => row?.name);
  if (actual.length !== expected.length ||
      new Set(actual).size !== actual.length ||
      actual.some(name => typeof name !== "string" || !expected.includes(name))) {
    throw new Error("Release held: remote D1 migrations do not match exact release source.");
  }
  return { applied: actual.length };
}
