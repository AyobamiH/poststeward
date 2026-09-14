import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  canonicalStringDigest,
  classifyExactRecoveryPreparationStatus,
  configuredD1DatabaseName,
  flattenD1Results,
  PITR_PREPARE_RETRY_MS,
  PITR_PREPARE_SETTLE_TIMEOUT_MS,
  sqlLiteral,
} from "../scripts/staging-pitr-erasure-acceptance.mjs";

test("canonicalStringDigest matches PostSteward string canonicalization", () => {
  assert.equal(
    canonicalStringDigest("owner@example.test"),
    "e16cd5afed45419922fe184c8ae1573ec522e83153478b7f983730446874b28f",
  );
});

test("sqlLiteral escapes apostrophes and preserves no executable delimiter", () => {
  assert.equal(sqlLiteral("owner's"), "'owner''s'");
});

test("flattenD1Results only returns result rows", () => {
  assert.deepEqual(
    flattenD1Results([
      { results: [{ state: "completed" }], success: true },
      { success: true },
      { results: [{ count: 1 }] },
    ]),
    [{ state: "completed" }, { count: 1 }],
  );
  assert.deepEqual(flattenD1Results({}), []);
});

test("configuredD1DatabaseName uses the reviewed environment database", () => {
  assert.equal(
    configuredD1DatabaseName({
      d1_databases: [{ database_name: "poststeward-identity-staging" }],
    }),
    "poststeward-identity-staging",
  );
  assert.throws(
    () =>
      configuredD1DatabaseName({
        d1_databases: [{ database_name: "poststeward-identity" }],
      }),
    /configured environment D1/,
  );
});

test("exact recovery prepare retry is bounded", () => {
  assert.ok(PITR_PREPARE_RETRY_MS >= 1000);
  assert.ok(PITR_PREPARE_SETTLE_TIMEOUT_MS > PITR_PREPARE_RETRY_MS);
  assert.ok(PITR_PREPARE_SETTLE_TIMEOUT_MS <= 3 * 60 * 1000);
});

test("failed exact preparation retries only when durable status proves no recovery is active", () => {
  const checkpoint = "00000000-0000-4000-8000-000000000010";
  const reason = "Disposable exact checkpoint recovery acceptance";
  assert.equal(
    classifyExactRecoveryPreparationStatus(
      { control: { quarantined: false }, plan: null },
      checkpoint,
      reason,
    ),
    "retryable",
  );
  assert.equal(
    classifyExactRecoveryPreparationStatus(
      {
        control: { quarantined: true },
        plan: {
          id: "00000000-0000-4000-8000-000000000001",
          digest: "a".repeat(64),
          state: "prepared",
          targetMode: "exact_checkpoint",
          checkpointId: checkpoint,
          reason,
        },
      },
      checkpoint,
      reason,
    ),
    "prepared",
  );
  assert.equal(
    classifyExactRecoveryPreparationStatus(
      {
        control: { quarantined: true },
        plan: {
          id: "00000000-0000-4000-8000-000000000002",
          digest: "b".repeat(64),
          state: "armed",
          targetMode: "exact_checkpoint",
          checkpointId: checkpoint,
          reason,
        },
      },
      checkpoint,
      reason,
    ),
    "blocked",
  );
  assert.equal(
    classifyExactRecoveryPreparationStatus(
      { control: { quarantined: true }, plan: null },
      checkpoint,
      reason,
    ),
    "blocked",
  );
});

test("a different exact checkpoint plan is never adopted after an uncertain response", () => {
  assert.equal(
    classifyExactRecoveryPreparationStatus(
      {
        control: { quarantined: true },
        plan: {
          id: "00000000-0000-4000-8000-000000000003",
          digest: "c".repeat(64),
          state: "prepared",
          targetMode: "exact_checkpoint",
          checkpointId: "00000000-0000-4000-8000-000000000099",
          reason: "different plan",
        },
      },
      "00000000-0000-4000-8000-000000000010",
      "Disposable exact checkpoint recovery acceptance",
    ),
    "blocked",
  );
});

test("destructive acceptance uses exact checkpoints and never calls timestamp resolution", () => {
  const source = readFileSync(
    "scripts/staging-pitr-erasure-acceptance.mjs",
    "utf8",
  );
  assert.match(source, /\/api\/recovery\/checkpoints/);
  assert.match(source, /targetMode: "exact_checkpoint"/);
  assert.match(source, /timestampResolutionUsed: false/);
  assert.doesNotMatch(source, /settledPitrTarget|getBookmarkForTime|restoreTarget/);
});
