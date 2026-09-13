import assert from "node:assert/strict";
import test from "node:test";
import {
  canonicalStringDigest,
  classifyRecoveryPreparationStatus,
  configuredD1DatabaseName,
  flattenD1Results,
  PITR_HISTORY_WARMUP_MS,
  PITR_PREPARE_RETRY_MS,
  PITR_PREPARE_SETTLE_TIMEOUT_MS,
  PITR_TARGET_AGE_MS,
  settledPitrTarget,
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

test("disposable PITR chooses a settled target inside initialised history", () => {
  assert.ok(PITR_HISTORY_WARMUP_MS > PITR_TARGET_AGE_MS);
  const initializedAt = 100000;
  const afterWarmup = initializedAt + PITR_HISTORY_WARMUP_MS;
  assert.equal(
    settledPitrTarget(afterWarmup, initializedAt),
    afterWarmup - PITR_TARGET_AGE_MS,
  );
  assert.ok(settledPitrTarget(afterWarmup, initializedAt) > initializedAt);
});

test("disposable PITR refuses a target on the new-object history edge", () => {
  const initializedAt = 100000;
  assert.throws(
    () => settledPitrTarget(initializedAt + PITR_TARGET_AGE_MS, initializedAt),
    /newly-created object history edge/,
  );
});

test("prepare retry is bounded and slower than its retry interval", () => {
  assert.ok(PITR_PREPARE_RETRY_MS >= 1000);
  assert.ok(PITR_PREPARE_SETTLE_TIMEOUT_MS > PITR_PREPARE_RETRY_MS);
  assert.ok(PITR_PREPARE_SETTLE_TIMEOUT_MS <= 10 * 60 * 1000);
});

test("failed preparation retries only when durable status proves no recovery is active", () => {
  const target = 123456;
  const reason = "Disposable staging PITR acceptance";
  assert.equal(
    classifyRecoveryPreparationStatus(
      { control: { quarantined: false }, plan: null },
      target,
      reason,
    ),
    "retryable",
  );
  assert.equal(
    classifyRecoveryPreparationStatus(
      {
        control: { quarantined: true },
        plan: {
          id: "00000000-0000-4000-8000-000000000001",
          digest: "a".repeat(64),
          state: "prepared",
          targetTime: target,
          reason,
        },
      },
      target,
      reason,
    ),
    "prepared",
  );
  assert.equal(
    classifyRecoveryPreparationStatus(
      {
        control: { quarantined: true },
        plan: {
          id: "00000000-0000-4000-8000-000000000002",
          digest: "b".repeat(64),
          state: "armed",
          targetTime: target,
          reason,
        },
      },
      target,
      reason,
    ),
    "blocked",
  );
  assert.equal(
    classifyRecoveryPreparationStatus(
      { control: { quarantined: true }, plan: null },
      target,
      reason,
    ),
    "blocked",
  );
});

test("a different prepared plan is never adopted after an uncertain response", () => {
  assert.equal(
    classifyRecoveryPreparationStatus(
      {
        control: { quarantined: true },
        plan: {
          id: "00000000-0000-4000-8000-000000000003",
          digest: "c".repeat(64),
          state: "prepared",
          targetTime: 999999,
          reason: "different plan",
        },
      },
      123456,
      "Disposable staging PITR acceptance",
    ),
    "blocked",
  );
});
