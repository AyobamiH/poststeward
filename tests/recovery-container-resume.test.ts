import assert from "node:assert/strict";
import test from "node:test";
import { assertRecoveryCanResume } from "../src/recovery.ts";
import { runtime } from "./runtime-fixture.ts";

test("recovery resume rejects unresolved Threads container creation evidence", async () => {
  const { mf, db } = await runtime();
  try {
    await db
      .prepare(
        "INSERT INTO external_containers(workspace,fingerprint,delivery_id,status,created_at,updated_at) VALUES (?,?,?,'uncertain',?,?)",
      )
      .bind(
        "workspace-a",
        "z".repeat(64),
        "threads-delivery",
        Date.now(),
        Date.now(),
      )
      .run();
    await assert.rejects(assertRecoveryCanResume(db, "workspace-a"), {
      code: "RECOVERY_EFFECTS_UNRESOLVED",
    });
  } finally {
    await mf.dispose();
  }
});
