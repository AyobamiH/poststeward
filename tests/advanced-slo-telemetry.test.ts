import assert from "node:assert/strict";
import test from "node:test";
import { advancedCanaryBucket } from "../src/advanced-rollout.ts";
import {
  advancedCanaryTelemetryContext,
  recordAdvancedSloEvent,
} from "../src/slo-telemetry.ts";

function eligibleWorkspace(seed: string, bps: number) {
  for (let index = 0; index < 100000; index++) {
    const candidate = `workspace-${index}`;
    if (advancedCanaryBucket(candidate, seed) < bps) return candidate;
  }
  throw new Error("fixture could not find eligible workspace");
}

function fakeEnv({ fail = false, enabled = true } = {}) {
  const rows: any[] = [];
  const database = {
    prepare(sql: string) {
      return {
        bind(...params: any[]) {
          return {
            async run() {
              if (fail) throw new Error("telemetry unavailable");
              rows.push({ sql, params });
              return { success: true, meta: { changes: 1 } };
            },
          };
        },
      };
    },
  };
  const env: any = {
    DEPLOY_ENV: "staging",
    RELEASE_SHA: "a".repeat(40),
    ADVANCED_ENABLED: enabled ? "true" : "false",
    ADVANCED_ROLLOUT_MODE: enabled ? "canary" : "disabled",
    ADVANCED_CANARY_BPS: enabled ? "1000" : "0",
    ADVANCED_CANARY_SEED: "stable-seed-v1",
    IDENTITY: database,
  };
  return { env, rows };
}

test("telemetry records only eligible staging canary workspaces", async () => {
  const { env, rows } = fakeEnv();
  const workspace = eligibleWorkspace(env.ADVANCED_CANARY_SEED, 1000);
  assert.deepEqual(advancedCanaryTelemetryContext(env, workspace), {
    release: "a".repeat(40),
    canaryBps: 1000,
  });
  assert.equal(
    await recordAdvancedSloEvent(
      env,
      workspace,
      "source_change",
      { dedupeKey: "campaign-1", durationMs: 12 },
      123456,
    ),
    true,
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].params[1], workspace);
  assert.equal(rows[0].params[2], "source_change");
  assert.equal(rows[0].params[3], 123456);
  assert.equal(rows[0].params[5], 12);
  assert.equal(rows[0].params[6], "a".repeat(40));
  assert.equal(rows[0].params[7], 1000);
  assert.equal(String(rows[0].params[0]).includes("campaign-1"), false);
});

test("telemetry is inert outside a bounded staging canary", async () => {
  const { env, rows } = fakeEnv({ enabled: false });
  assert.equal(
    await recordAdvancedSloEvent(
      env,
      "workspace-1",
      "advanced_operation",
    ),
    false,
  );
  assert.equal(rows.length, 0);
});

test("telemetry failure never fails the product path", async () => {
  const { env } = fakeEnv({ fail: true });
  const workspace = eligibleWorkspace(env.ADVANCED_CANARY_SEED, 1000);
  assert.equal(
    await recordAdvancedSloEvent(
      env,
      workspace,
      "advanced_operation",
      { dedupeKey: "operation-1" },
    ),
    false,
  );
});
