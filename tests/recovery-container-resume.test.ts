import assert from "node:assert/strict";
import test from "node:test";
import { assertRecoveryCanResume } from "../src/recovery.ts";
import { runtime } from "./runtime-fixture.ts";

test("fresh Threads container intent blocks recovery, then becomes permanently fenced uncertainty after the settlement window", async () => {
  const { mf, db } = await runtime();
  try {
    const now = Date.UTC(2026, 8, 9, 22, 0, 0);
    await db
      .prepare(
        "INSERT INTO external_containers(workspace,fingerprint,delivery_id,status,created_at,updated_at) VALUES (?,?,?,'intent',?,?)",
      )
      .bind(
        "workspace-a",
        "z".repeat(64),
        "threads-delivery",
        now,
        now,
      )
      .run();
    await assert.rejects(assertRecoveryCanResume(db, "workspace-a", now), {
      code: "RECOVERY_EFFECTS_IN_FLIGHT",
    });
    const settled = await assertRecoveryCanResume(db, "workspace-a", now + 120001);
    assert.equal(settled.containerIntent, 0);
    assert.equal(settled.containerUncertain, 1);
  } finally {
    await mf.dispose();
  }
});
