import assert from "node:assert/strict";
import test from "node:test";
import {
  advancedCanaryBucket,
  advancedRolloutDecision,
} from "../src/advanced-rollout.ts";

const workspace = "00000000-0000-4000-8000-000000000001";

function env(overrides: Record<string, string> = {}) {
  return {
    ADVANCED_ENABLED: "false",
    ADVANCED_ROLLOUT_MODE: "disabled",
    ADVANCED_CANARY_BPS: "0",
    ADVANCED_CANARY_SEED: "",
    ...overrides,
  } as any;
}

test("the master kill switch wins over every rollout mode", () => {
  assert.deepEqual(
    advancedRolloutDecision(
      env({
        ADVANCED_ROLLOUT_MODE: "global",
        ADVANCED_CANARY_BPS: "10000",
        ADVANCED_CANARY_SEED: "advanced-v1",
      }),
      workspace,
    ),
    {
      masterEnabled: false,
      mode: "global",
      canaryBps: 10000,
      eligible: false,
    },
  );
});

test("canary membership is deterministic and bounded by basis points", () => {
  const seed = "advanced-v1";
  const bucket = advancedCanaryBucket(workspace, seed);
  assert.equal(bucket, advancedCanaryBucket(workspace, seed));
  assert.ok(bucket >= 0 && bucket < 10000);

  const below = Math.max(0, bucket);
  const excluded = advancedRolloutDecision(
    env({
      ADVANCED_ENABLED: "true",
      ADVANCED_ROLLOUT_MODE: "canary",
      ADVANCED_CANARY_BPS: String(below),
      ADVANCED_CANARY_SEED: seed,
    }),
    workspace,
  );
  assert.equal(excluded.eligible, false);

  const included = advancedRolloutDecision(
    env({
      ADVANCED_ENABLED: "true",
      ADVANCED_ROLLOUT_MODE: "canary",
      ADVANCED_CANARY_BPS: String(Math.min(10000, bucket + 1)),
      ADVANCED_CANARY_SEED: seed,
    }),
    workspace,
  );
  assert.equal(included.eligible, true);
});

test("invalid or incomplete canary configuration fails closed at runtime", () => {
  for (const overrides of [
    { ADVANCED_CANARY_BPS: "not-a-number", ADVANCED_CANARY_SEED: "advanced-v1" },
    { ADVANCED_CANARY_BPS: "10001", ADVANCED_CANARY_SEED: "advanced-v1" },
    { ADVANCED_CANARY_BPS: "1000", ADVANCED_CANARY_SEED: "short" },
  ])
    assert.equal(
      advancedRolloutDecision(
        env({
          ADVANCED_ENABLED: "true",
          ADVANCED_ROLLOUT_MODE: "canary",
          ...overrides,
        }),
        workspace,
      ).eligible,
      false,
    );
});

test("global mode exists in runtime semantics but remains a deployment-policy concern", () => {
  assert.equal(
    advancedRolloutDecision(
      env({
        ADVANCED_ENABLED: "true",
        ADVANCED_ROLLOUT_MODE: "global",
      }),
      workspace,
    ).eligible,
    true,
  );
});
