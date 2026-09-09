import assert from "node:assert/strict";
import test from "node:test";
import { Fault } from "../src/common.ts";
import {
  EffectLedgerProviders,
  effectSummary,
  setWorkspaceQuarantine,
} from "../src/effects.ts";
import {
  armRecoveryPlan,
  assertRecoveryCanResume,
  prepareRecoveryPlan,
  rearmRecoveryPlanForUndo,
  reconcileRecoveryPlan,
  recoveryStatus,
  requireRecoveryPlan,
} from "../src/recovery.ts";
import type { Credential, ProviderAPI } from "../src/providers.ts";
import type { Delivery } from "../src/types.ts";
import { runtime } from "./runtime-fixture.ts";

const credential: Credential = { accessToken: "test-token-only" };
function delivery(id = "delivery-1", fingerprint = "f".repeat(64)): Delivery {
  return {
    id,
    fingerprint,
    campaign: "campaign",
    project: "project",
    account: "account",
    provider: "x",
    identity: { id: "owner-1", username: "owner" },
    binding: 1,
    text: "Exact approved copy.",
    digest: "d".repeat(64),
    dueAt: Date.now(),
    timezone: "UTC",
    status: "executing",
    createdAt: Date.now(),
    updatedAt: Date.now(),
    actor: { workspace: "workspace-a", id: "owner", scopes: ["admin"] },
    automatic: false,
    phase: "publish",
    claimId: "claim-1",
  };
}
function provider(overrides: Partial<ProviderAPI> = {}): ProviderAPI {
  return {
    identity: async () => ({ id: "owner-1", username: "owner" }),
    createContainer: async () => "container-1",
    containerStatus: async () => "FINISHED",
    publish: async () => ({ id: "post-1", url: "https://x.com/i/web/status/post-1" }),
    verify: async () => ({ verified: true }),
    metrics: async () => ({ availability: "available" }),
    ...overrides,
  };
}

test("D1 publication fence survives replay and returns existing creation evidence without a second provider write", async () => {
  const { mf, db } = await runtime();
  try {
    let writes = 0;
    const wrapped = new EffectLedgerProviders(
      provider({
        publish: async () => {
          writes++;
          return { id: "post-1", url: "https://x.com/i/web/status/post-1" };
        },
      }),
      db,
      "workspace-a",
    );
    const d = delivery();
    assert.equal((await wrapped.publish(d, credential)).id, "post-1");
    assert.equal((await wrapped.publish(d, credential)).id, "post-1");
    assert.equal(writes, 1);
    const summary = await effectSummary(db, "workspace-a");
    assert.equal(summary.created, 1);
    d.postId = "post-1";
    await wrapped.verify(d, credential);
    assert.equal((await effectSummary(db, "workspace-a")).verified, 1);
  } finally {
    await mf.dispose();
  }
});

test("uncertain provider writes and Threads container writes remain fenced across retries", async () => {
  const { mf, db } = await runtime();
  try {
    let publishWrites = 0;
    const uncertain = new EffectLedgerProviders(
      provider({
        publish: async () => {
          publishWrites++;
          throw new Fault("AMBIGUOUS_PROVIDER_WRITE", "unknown", 502);
        },
      }),
      db,
      "workspace-a",
    );
    const d = delivery("delivery-uncertain", "a".repeat(64));
    await assert.rejects(uncertain.publish(d, credential), { code: "AMBIGUOUS_PROVIDER_WRITE" });
    await assert.rejects(uncertain.publish(d, credential), { code: "AMBIGUOUS_PROVIDER_WRITE" });
    assert.equal(publishWrites, 1);
    assert.equal((await effectSummary(db, "workspace-a")).uncertain, 1);

    let containers = 0;
    const threads = new EffectLedgerProviders(
      provider({
        createContainer: async () => {
          containers++;
          return "container-stable";
        },
      }),
      db,
      "workspace-b",
    );
    const td = { ...delivery("threads-delivery", "b".repeat(64)), provider: "threads" as const };
    assert.equal(await threads.createContainer(td, credential), "container-stable");
    assert.equal(await threads.createContainer(td, credential), "container-stable");
    assert.equal(containers, 1);
    assert.equal((await effectSummary(db, "workspace-b")).containerCreated, 1);
  } finally {
    await mf.dispose();
  }
});

test("known no-effect rejection removes only the provisional fence so a later reviewed retry can proceed", async () => {
  const { mf, db } = await runtime();
  try {
    let writes = 0;
    const wrapped = new EffectLedgerProviders(
      provider({
        publish: async () => {
          writes++;
          if (writes === 1) throw new Fault("PROVIDER_HTTP_422", "rejected", 502);
          return { id: "post-after-correction" };
        },
      }),
      db,
      "workspace-a",
    );
    const d = delivery("delivery-retry", "c".repeat(64));
    await assert.rejects(wrapped.publish(d, credential), { code: "PROVIDER_HTTP_422" });
    assert.equal((await effectSummary(db, "workspace-a")).intent, 0);
    assert.equal((await wrapped.publish(d, credential)).id, "post-after-correction");
    assert.equal(writes, 2);
  } finally {
    await mf.dispose();
  }
});

test("recovery quarantine blocks external writes before the provider and reports no secret material", async () => {
  const { mf, db } = await runtime();
  try {
    let writes = 0;
    const wrapped = new EffectLedgerProviders(
      provider({
        publish: async () => {
          writes++;
          return { id: "should-not-exist" };
        },
      }),
      db,
      "workspace-a",
    );
    await setWorkspaceQuarantine(db, "workspace-a", true, "incident recovery");
    await assert.rejects(wrapped.publish(delivery(), credential), { code: "RECOVERY_QUARANTINED" });
    assert.equal(writes, 0);
    const status = await recoveryStatus(db, "workspace-a");
    assert.equal(status.control.quarantined, true);
    assert.doesNotMatch(JSON.stringify(status), /test-token-only|bookmark/);
  } finally {
    await mf.dispose();
  }
});

test("recovery plans are owner-bound, digest-bound, externally durable and support reconcile plus exact undo re-arm", async () => {
  const { mf, db } = await runtime();
  try {
    const now = Date.UTC(2026, 8, 9, 20);
    await setWorkspaceQuarantine(db, "workspace-a", true, "restore exact state", now);
    const prepared: any = await prepareRecoveryPlan(
      db,
      {
        workspace: "workspace-a",
        actor: "owner",
        targetTime: now - 3600000,
        targetBookmark: "0000007b-target-bookmark-0001",
        preRestoreBookmark: "0000007c-current-bookmark-0001",
        reason: "restore exact state",
      },
      now,
    );
    assert.equal(prepared.state, "prepared");
    const plan = await requireRecoveryPlan(
      db,
      {
        id: prepared.id,
        digest: prepared.digest,
        workspace: "workspace-a",
        actor: "owner",
        states: ["prepared"],
      },
      now,
    );
    await assert.rejects(
      requireRecoveryPlan(
        db,
        {
          id: prepared.id,
          digest: prepared.digest,
          workspace: "workspace-a",
          actor: "other-owner",
          states: ["prepared"],
        },
        now,
      ),
      { code: "RECOVERY_PLAN_CHANGED" },
    );
    await armRecoveryPlan(db, plan, "0000007d-undo-bookmark-000001", now + 1);
    let armed = await requireRecoveryPlan(
      db,
      {
        id: prepared.id,
        digest: prepared.digest,
        workspace: "workspace-a",
        actor: "owner",
        states: ["armed"],
      },
      now + 2,
    );
    await reconcileRecoveryPlan(db, armed, now + 3);
    let status = await recoveryStatus(db, "workspace-a");
    assert.equal(status.plan?.state, "reconciled");
    assert.equal(status.plan?.undoAvailable, true);
    assert.doesNotMatch(JSON.stringify(status), /target-bookmark|undo-bookmark|current-bookmark/);
    const reconciled = await requireRecoveryPlan(
      db,
      {
        id: prepared.id,
        digest: prepared.digest,
        workspace: "workspace-a",
        actor: "owner",
        states: ["reconciled"],
      },
      now + 4,
    );
    await rearmRecoveryPlanForUndo(db, reconciled, "0000007e-redo-bookmark-000001", now + 5);
    status = await recoveryStatus(db, "workspace-a");
    assert.equal(status.plan?.state, "armed");
    assert.equal(status.plan?.digest, prepared.digest);
    armed = await requireRecoveryPlan(
      db,
      {
        id: prepared.id,
        digest: prepared.digest,
        workspace: "workspace-a",
        actor: "owner",
        states: ["armed"],
      },
      now + 6,
    );
    assert.equal(armed.undo_bookmark, "0000007e-redo-bookmark-000001");
  } finally {
    await mf.dispose();
  }
});

test("fresh publication intents block recovery until their bounded write window settles", async () => {
  const { mf, db } = await runtime();
  try {
    const now = Date.UTC(2026, 8, 9, 22, 0, 0);
    await db
      .prepare(
        "INSERT INTO external_effects(workspace,fingerprint,delivery_id,provider,text_digest,status,created_at,updated_at) VALUES (?,?,?,?,?,'intent',?,?)",
      )
      .bind("workspace-a", "x".repeat(64), "delivery", "x", "d".repeat(64), now, now)
      .run();
    await assert.rejects(assertRecoveryCanResume(db, "workspace-a", now), {
      code: "RECOVERY_EFFECTS_IN_FLIGHT",
    });
    const settled = await assertRecoveryCanResume(db, "workspace-a", now + 120001);
    assert.equal(settled.intent, 0);
    assert.equal(settled.uncertain, 1);
  } finally {
    await mf.dispose();
  }
});

test("ambiguous publication evidence stays individually fenced without wedging unrelated recovery", async () => {
  const { mf, db } = await runtime();
  try {
    const now = Date.UTC(2026, 8, 9, 22, 0, 0);
    await db
      .prepare(
        "INSERT INTO external_effects(workspace,fingerprint,delivery_id,provider,text_digest,status,created_at,updated_at) VALUES (?,?,?,?,?,'uncertain',?,?)",
      )
      .bind("workspace-a", "u".repeat(64), "delivery", "x", "d".repeat(64), now, now)
      .run();
    const counts = await assertRecoveryCanResume(db, "workspace-a", now);
    assert.equal(counts.uncertain, 1);
    assert.equal(counts.intent, 0);
  } finally {
    await mf.dispose();
  }
});


test("an expired prepared recovery cannot execute but remains explicitly cancellable", async () => {
  const { mf, db } = await runtime();
  try {
    const now = Date.UTC(2026, 8, 9, 22, 0, 0);
    const prepared: any = await prepareRecoveryPlan(
      db,
      {
        workspace: "workspace-expired",
        actor: "owner",
        targetTime: now - 60000,
        targetBookmark: "0000007b-target-bookmark-expired",
        preRestoreBookmark: "0000007c-current-bookmark-expired",
        reason: "test expired cancellation",
      },
      now,
    );
    await assert.rejects(
      requireRecoveryPlan(
        db,
        {
          id: prepared.id,
          digest: prepared.digest,
          workspace: "workspace-expired",
          actor: "owner",
          states: ["prepared"],
        },
        now + 600001,
      ),
      { code: "RECOVERY_PLAN_EXPIRED" },
    );
    const cancellable = await requireRecoveryPlan(
      db,
      {
        id: prepared.id,
        digest: prepared.digest,
        workspace: "workspace-expired",
        actor: "owner",
        states: ["prepared"],
        allowExpiredPrepared: true,
      },
      now + 600001,
    );
    assert.equal(cancellable.id, prepared.id);
  } finally {
    await mf.dispose();
  }
});

test("quarantine and external-effect intent acquisition are atomic in D1", async () => {
  const { mf, db } = await runtime();
  try {
    let publications = 0;
    let containers = 0;
    const wrapped = new EffectLedgerProviders(
      provider({
        publish: async () => { publications++; return { id: "forbidden" }; },
        createContainer: async () => { containers++; return "forbidden-container"; },
      }),
      db,
      "workspace-atomic",
    );
    await setWorkspaceQuarantine(db, "workspace-atomic", true, "atomic acquisition test");
    await assert.rejects(wrapped.publish(delivery("pub-atomic", "q".repeat(64)), credential), { code: "RECOVERY_QUARANTINED" });
    const threads = { ...delivery("container-atomic", "r".repeat(64)), provider: "threads" as const };
    await assert.rejects(wrapped.createContainer(threads, credential), { code: "RECOVERY_QUARANTINED" });
    assert.equal(publications, 0);
    assert.equal(containers, 0);
    const postRows = await db.prepare("SELECT count(*) AS n FROM external_effects WHERE workspace=?").bind("workspace-atomic").first<{ n: number }>();
    const containerRows = await db.prepare("SELECT count(*) AS n FROM external_containers WHERE workspace=?").bind("workspace-atomic").first<{ n: number }>();
    assert.equal(Number(postRows?.n || 0), 0);
    assert.equal(Number(containerRows?.n || 0), 0);
  } finally {
    await mf.dispose();
  }
});
