import assert from "node:assert/strict";
import test from "node:test";
import {
  beginWorkspaceDeletion,
  workspaceDeletion,
} from "../src/lifecycle.ts";
import { assertRecoveryCanResume } from "../src/recovery.ts";
import { workspaceQuarantined } from "../src/effects.ts";
import { runtime } from "./runtime-fixture.ts";

test("deletion tombstone and publication quarantine commit before fresh external effects can be erased", async () => {
  const { mf, db } = await runtime();
  const workspace = "delete-settlement-workspace";
  const now = 1789035600000;
  try {
    await db
      .prepare(
        "INSERT INTO external_effects(workspace,fingerprint,delivery_id,provider,text_digest,status,created_at,updated_at) VALUES (?,?,?,?,?,'intent',?,?)",
      )
      .bind(
        workspace,
        "fresh-fingerprint",
        "delivery",
        "x",
        "digest",
        now,
        now,
      )
      .run();

    await beginWorkspaceDeletion(db, workspace, now + 1);
    assert.equal((await workspaceDeletion(db, workspace))?.state, "pending");
    assert.equal((await workspaceQuarantined(db, workspace)).quarantined, true);

    await assert.rejects(
      assertRecoveryCanResume(db, workspace, now + 60_000),
      { code: "RECOVERY_EFFECTS_IN_FLIGHT" },
    );
    const fresh = await db
      .prepare(
        "SELECT status FROM external_effects WHERE workspace=? AND fingerprint=?",
      )
      .bind(workspace, "fresh-fingerprint")
      .first<{ status: string }>();
    assert.equal(fresh?.status, "intent");

    await assertRecoveryCanResume(db, workspace, now + 120_001);
    const settled = await db
      .prepare(
        "SELECT status,reason FROM external_effects WHERE workspace=? AND fingerprint=?",
      )
      .bind(workspace, "fresh-fingerprint")
      .first<{ status: string; reason: string }>();
    assert.equal(settled?.status, "uncertain");
    assert.equal(settled?.reason, "RECOVERY_STALE_INTENT");
    assert.equal((await workspaceDeletion(db, workspace))?.state, "pending");
  } finally {
    await mf.dispose();
  }
});
