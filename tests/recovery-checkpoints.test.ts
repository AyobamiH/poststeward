import assert from "node:assert/strict";
import test from "node:test";
import {
  createRecoveryCheckpoint,
  listRecoveryCheckpoints,
  requireRecoveryCheckpoint,
} from "../src/recovery-checkpoints.ts";
import { prepareRecoveryPlan, recoveryStatus } from "../src/recovery.ts";
import { runtime } from "./runtime-fixture.ts";

test("checkpoint catalogue keeps bookmarks private while exact plan binds checkpoint identity", async () => {
  const { mf, db } = await runtime();
  try {
    const now = Date.UTC(2026, 8, 14, 16, 0, 0);
    const checkpoint = await createRecoveryCheckpoint(
      db,
      {
        workspace: "workspace-checkpoint",
        bookmark: "00000111-exact-checkpoint-bookmark",
        capturedAt: now - 60000,
        release: "a".repeat(40),
        rootWrite: "next",
        stateDigest: "b".repeat(64),
        source: "owner",
      },
      now,
    );
    assert.equal(checkpoint.source, "owner");
    assert.equal(checkpoint.release, "a".repeat(40));
    assert.doesNotMatch(JSON.stringify(checkpoint), /exact-checkpoint-bookmark/);

    const listed = await listRecoveryCheckpoints(db, "workspace-checkpoint", now);
    assert.equal(listed.length, 1);
    assert.equal(listed[0].id, checkpoint.id);
    assert.doesNotMatch(JSON.stringify(listed), /exact-checkpoint-bookmark/);

    const internal = await requireRecoveryCheckpoint(
      db,
      "workspace-checkpoint",
      checkpoint.id,
      now,
    );
    assert.equal(internal.bookmark, "00000111-exact-checkpoint-bookmark");

    const prepared: any = await prepareRecoveryPlan(
      db,
      {
        workspace: "workspace-checkpoint",
        actor: "owner",
        targetTime: internal.captured_at,
        targetBookmark: internal.bookmark,
        preRestoreBookmark: "00000112-pre-restore-bookmark",
        reason: "restore known checkpoint",
        targetMode: "exact_checkpoint",
        checkpointId: checkpoint.id,
      },
      now,
    );
    assert.equal(prepared.targetMode, "exact_checkpoint");
    assert.equal(prepared.checkpointId, checkpoint.id);
    assert.doesNotMatch(JSON.stringify(prepared), /exact-checkpoint-bookmark|pre-restore-bookmark/);

    const status = await recoveryStatus(db, "workspace-checkpoint");
    assert.equal(status.plan?.targetMode, "exact_checkpoint");
    assert.equal(status.plan?.checkpointId, checkpoint.id);
    assert.doesNotMatch(JSON.stringify(status), /exact-checkpoint-bookmark|pre-restore-bookmark/);
  } finally {
    await mf.dispose();
  }
});

test("expired exact checkpoints fail closed", async () => {
  const { mf, db } = await runtime();
  try {
    const capturedAt = Date.UTC(2026, 7, 1);
    const now = capturedAt + 31 * 86400000;
    const checkpoint = await createRecoveryCheckpoint(
      db,
      {
        workspace: "workspace-expired-checkpoint",
        bookmark: "00000113-expired-checkpoint-bookmark",
        capturedAt,
        release: "c".repeat(40),
        rootWrite: "legacy",
        stateDigest: "d".repeat(64),
        source: "automatic",
      },
      capturedAt,
    );
    await assert.rejects(
      requireRecoveryCheckpoint(
        db,
        "workspace-expired-checkpoint",
        checkpoint.id,
        now,
      ),
      { code: "RECOVERY_CHECKPOINT_UNAVAILABLE" },
    );
  } finally {
    await mf.dispose();
  }
});
