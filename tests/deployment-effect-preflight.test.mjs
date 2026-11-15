import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  assertNoImplicitRootCutover,
  assertCompleteRemoteMigrations,
  expectedMigrationNames,
} from "../scripts/deployment-effect-preflight.mjs";

function fixture() {
  const cwd = mkdtempSync(join(tmpdir(), "poststeward-migration-gate-"));
  mkdirSync(join(cwd, "migrations"));
  const names = ["0001_identity.sql", "0002_security.sql"];
  for (const name of names) writeFileSync(join(cwd, "migrations", name), "SELECT 1;");
  return { cwd, names, db: { migrations_dir: "migrations" } };
}
const result = (names, overrides = {}) => ({
  success: true,
  result: [{ success: true, results: names.map(name => ({ name })), meta: { rows_written: 0 }, ...overrides }],
});

test("ordinary deployment preserves an existing next root but cannot start or roll back a cutover", () => {
  const legacy = "a".repeat(64), next = "b".repeat(64);
  const retained = { ENCRYPTION_ROOT_WRITE: "next", ENCRYPTION_LEGACY_ROOT_ID: legacy, ENCRYPTION_NEXT_ROOT_ID: next };
  assert.equal(assertNoImplicitRootCutover(null, { ENCRYPTION_ROOT_WRITE: "legacy" }), "legacy");
  assert.equal(assertNoImplicitRootCutover({ ENCRYPTION_ROOT_WRITE: "legacy" }, { ENCRYPTION_ROOT_WRITE: "legacy" }), "legacy");
  assert.equal(assertNoImplicitRootCutover(retained, { ...retained }), "retained-next");
  for (const [current, desired] of [
    [null, retained], [null, { ENCRYPTION_ROOT_WRITE: "next" }],
    [{ ENCRYPTION_ROOT_WRITE: "legacy" }, retained],
    [retained, { ...retained, ENCRYPTION_LEGACY_ROOT_ID: "c".repeat(64) }],
    [retained, { ...retained, ENCRYPTION_NEXT_ROOT_ID: "d".repeat(64) }],
    [{ ...retained, ENCRYPTION_NEXT_ROOT_ID: "" }, retained],
    [retained, { ...retained, ENCRYPTION_ROOT_WRITE: "legacy" }],
    [retained, { ...retained, ENCRYPTION_ROOT_WRITE: "unknown" }],
    [{}, { ENCRYPTION_ROOT_WRITE: "legacy" }],
  ]) assert.throws(() => assertNoImplicitRootCutover(current, desired), /requires separate authority/);
});

test("only complete, exact-source D1 migration evidence allows zero-write release", () => {
  const f = fixture();
  try {
    assert.deepEqual(expectedMigrationNames(f.db, f.cwd), f.names);
    assert.deepEqual(assertCompleteRemoteMigrations(result(f.names), f.db, f.cwd), { applied: 2 });
    assert.deepEqual(assertCompleteRemoteMigrations(result(f.names.toReversed()), f.db, f.cwd), { applied: 2 });
    for (const bad of [
      result([]), result(["0001_identity.sql"]),
      result(["0001_identity.sql", "0999_unknown.sql"]),
      result(["0001_identity.sql", "0001_identity.sql"]),
      result(f.names, { success: false }),
      result(f.names, { meta: { rows_written: 1 } }),
      result(f.names, { meta: {} }),
      { success: false, result: [{ success: true, results: [] }] },
      { success: true, result: [] },
      { success: true, result: [{ success: true }] },
      { success: true, result: [{ success: true, results: f.names.map(name => ({ name })) }, { success: true, results: [] }] },
    ]) assert.throws(() => assertCompleteRemoteMigrations(bad, f.db, f.cwd), /Release held/);
  } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("unreviewed migration paths, missing ledger and source tampering fail closed", () => {
  const f = fixture();
  try {
    for (const db of [
      { migrations_dir: "../migrations" },
      { migrations_dir: "migrations", migrations_table: "other_ledger" },
      { migrations_dir: "migrations", migrations_pattern: "migrations/**/*.sql" },
    ]) assert.throws(() => expectedMigrationNames(db, f.cwd), /unreviewed/);
    writeFileSync(join(f.cwd, "migrations", "not-sequential.sql"), "SELECT 1;");
    assert.throws(() => expectedMigrationNames(f.db, f.cwd), /invalid/);
  } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("reviewed deployment path never invokes implicit remote D1 mutation", () => {
  const script = readFileSync("scripts/deploy-environment.mjs", "utf8");
  assert.match(script, /assertNoImplicitRootCutover\(currentRootBindings, c\.vars\)/);
  assert.match(script, /SELECT name FROM d1_migrations ORDER BY name/);
  assert.match(script, /assertCompleteRemoteMigrations\(/);
  assert.doesNotMatch(script, /wrangler\(\["d1",\s*"migrations",\s*"apply"/);
  assert.doesNotMatch(script, /runRootCutover|ROOT_ROTATION_TOKEN/);
  assert.ok(script.indexOf("assertNoImplicitRootCutover(currentRootBindings, c.vars)") < script.indexOf("const migrationResponse"));
  assert.ok(script.indexOf("assertCompleteRemoteMigrations(") < script.indexOf('wrangler(["deploy"'));
});
