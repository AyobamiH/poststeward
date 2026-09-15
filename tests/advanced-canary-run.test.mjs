import assert from "node:assert/strict";
import test from "node:test";
import { canaryBoundaryFromConfig } from "../scripts/advanced-canary-run.mjs";

const release = "a".repeat(40);

test("active canary boundary hashes the seed and exposes no raw seed", () => {
  const value = canaryBoundaryFromConfig(
    {
      vars: {
        RELEASE_SHA: release,
        ADVANCED_ENABLED: "true",
        ADVANCED_ROLLOUT_MODE: "canary",
        ADVANCED_CANARY_BPS: "500",
        ADVANCED_CANARY_SEED: "stable-seed-v1",
      },
    },
    1234,
  );
  assert.equal(value.state, "active");
  assert.equal(value.release, release);
  assert.equal(value.bps, 500);
  assert.equal(value.at, 1234);
  assert.match(value.seedHash, /^[a-f0-9]{64}$/);
  assert.equal(JSON.stringify(value).includes("stable-seed-v1"), false);
});

test("disabled deployment closes a run without inventing a canary", () => {
  assert.deepEqual(
    canaryBoundaryFromConfig(
      {
        vars: {
          RELEASE_SHA: release,
          ADVANCED_ENABLED: "false",
          ADVANCED_ROLLOUT_MODE: "disabled",
          ADVANCED_CANARY_BPS: "0",
          ADVANCED_CANARY_SEED: "stable-seed-v1",
        },
      },
      5678,
    ),
    { state: "stopped", release, at: 5678 },
  );
});

test("global, oversized and contradictory rollout states cannot create SLO boundaries", () => {
  for (const vars of [
    {
      RELEASE_SHA: release,
      ADVANCED_ENABLED: "true",
      ADVANCED_ROLLOUT_MODE: "global",
      ADVANCED_CANARY_BPS: "10000",
      ADVANCED_CANARY_SEED: "stable-seed-v1",
    },
    {
      RELEASE_SHA: release,
      ADVANCED_ENABLED: "true",
      ADVANCED_ROLLOUT_MODE: "canary",
      ADVANCED_CANARY_BPS: "2000",
      ADVANCED_CANARY_SEED: "stable-seed-v1",
    },
    {
      RELEASE_SHA: release,
      ADVANCED_ENABLED: "false",
      ADVANCED_ROLLOUT_MODE: "canary",
      ADVANCED_CANARY_BPS: "100",
      ADVANCED_CANARY_SEED: "stable-seed-v1",
    },
  ])
    assert.throws(
      () => canaryBoundaryFromConfig({ vars }, 1),
      /bounded staging canaries|contradictory/,
    );
});
