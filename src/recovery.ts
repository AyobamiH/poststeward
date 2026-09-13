import { digest, requireValue, uid } from "./common.ts";
import { effectSummary, workspaceQuarantined } from "./effects.ts";

export type RecoveryState = "prepared" | "armed" | "reconciled" | "cancelled";

export interface RecoveryPlanRow {
  id: string;
  workspace: string;
  actor: string;
  target_time: number;
  target_bookmark: string;
  pre_restore_bookmark: string;
  undo_bookmark?: string;
  reason: string;
  digest: string;
  state: RecoveryState;
  created_at: number;
  expires_at: number;
  updated_at: number;
}

const externalEffectSettleMs = 120000;

function publicPlan(plan?: RecoveryPlanRow | null) {
  if (!plan) return null;
  return {
    id: plan.id,
    actor: plan.actor,
    targetTime: plan.target_time,
    reason: plan.reason,
    digest: plan.digest,
    state: plan.state,
    createdAt: plan.created_at,
    expiresAt: plan.expires_at,
    updatedAt: plan.updated_at,
    undoAvailable: Boolean(plan.undo_bookmark),
  };
}

export async function recoveryStatus(db: D1Database, workspace: string) {
  const plan = await db
    .prepare(
      "SELECT * FROM workspace_recovery_plans WHERE workspace=? ORDER BY created_at DESC LIMIT 1",
    )
    .bind(workspace)
    .first<RecoveryPlanRow>();
  return {
    control: await workspaceQuarantined(db, workspace),
    effects: await effectSummary(db, workspace),
    plan: publicPlan(plan),
  };
}

export async function prepareRecoveryPlan(
  db: D1Database,
  input: {
    workspace: string;
    actor: string;
    targetTime: number;
    targetBookmark: string;
    preRestoreBookmark: string;
    reason: string;
  },
  now = Date.now(),
) {
  requireValue(
    Number.isFinite(input.targetTime) &&
      input.targetTime <= now &&
      input.targetTime >= now - 30 * 86400000,
    "RECOVERY_TARGET_INVALID",
    "Recovery target must be within the previous 30 days.",
    400,
  );
  requireValue(
    typeof input.targetBookmark === "string" &&
      input.targetBookmark.length >= 16 &&
      input.targetBookmark.length <= 256 &&
      typeof input.preRestoreBookmark === "string" &&
      input.preRestoreBookmark.length >= 16 &&
      input.preRestoreBookmark.length <= 256,
    "RECOVERY_BOOKMARK_INVALID",
    "Durable storage did not return usable recovery bookmarks.",
    502,
  );
  requireValue(
    input.reason.trim().length >= 3 && input.reason.length <= 240,
    "RECOVERY_REASON_REQUIRED",
    "Supply a concise recovery reason.",
    400,
  );
  const id = uid();
  const immutable = {
    id,
    workspace: input.workspace,
    actor: input.actor,
    targetTime: input.targetTime,
    targetBookmark: input.targetBookmark,
    preRestoreBookmark: input.preRestoreBookmark,
    reason: input.reason.trim(),
    createdAt: now,
    expiresAt: now + 10 * 60000,
  };
  const planDigest = await digest(immutable);
  await db.batch([
    db
      .prepare(
        "UPDATE workspace_recovery_plans SET state='cancelled',updated_at=? WHERE workspace=? AND state='prepared'",
      )
      .bind(now, input.workspace),
    db
      .prepare(
        "INSERT INTO workspace_recovery_plans(id,workspace,actor,target_time,target_bookmark,pre_restore_bookmark,reason,digest,state,created_at,expires_at,updated_at) VALUES (?,?,?,?,?,?,?,?, 'prepared',?,?,?)",
      )
      .bind(
        id,
        input.workspace,
        input.actor,
        input.targetTime,
        input.targetBookmark,
        input.preRestoreBookmark,
        immutable.reason,
        planDigest,
        now,
        immutable.expiresAt,
        now,
      ),
  ]);
  console.warn(
    JSON.stringify({
      event: "workspace_recovery_prepared",
      workspace: input.workspace,
      plan: id,
      targetTime: input.targetTime,
      at: now,
    }),
  );
  return {
    ...publicPlan({
      id,
      workspace: input.workspace,
      actor: input.actor,
      target_time: input.targetTime,
      target_bookmark: input.targetBookmark,
      pre_restore_bookmark: input.preRestoreBookmark,
      reason: immutable.reason,
      digest: planDigest,
      state: "prepared",
      created_at: now,
      expires_at: immutable.expiresAt,
      updated_at: now,
    }),
    targetBookmarkCaptured: true,
    preRestoreBookmarkCaptured: true,
  };
}

export async function requireRecoveryPlan(
  db: D1Database,
  input: {
    id: string;
    digest: string;
    workspace: string;
    actor: string;
    states: RecoveryState[];
    allowExpiredPrepared?: boolean;
  },
  now = Date.now(),
) {
  const plan = await db
    .prepare("SELECT * FROM workspace_recovery_plans WHERE id=? AND workspace=?")
    .bind(input.id, input.workspace)
    .first<RecoveryPlanRow>();
  requireValue(
    plan &&
      plan.actor === input.actor &&
      plan.digest === input.digest &&
      input.states.includes(plan.state),
    "RECOVERY_PLAN_CHANGED",
    "Recovery plan is missing, changed, belongs to another actor, or is in the wrong state.",
    409,
  );
  if (plan.state === "prepared" && !input.allowExpiredPrepared)
    requireValue(
      plan.expires_at > now,
      "RECOVERY_PLAN_EXPIRED",
      "Prepare a fresh recovery plan before restoring state.",
      409,
    );
  return plan;
}

function requireBookmark(value: string) {
  requireValue(
    typeof value === "string" && value.length >= 16 && value.length <= 256,
    "RECOVERY_BOOKMARK_INVALID",
    "Durable storage did not return an undo bookmark.",
    502,
  );
}

export async function armRecoveryPlan(
  db: D1Database,
  plan: RecoveryPlanRow,
  undoBookmark: string,
  now = Date.now(),
) {
  requireBookmark(undoBookmark);
  const result = await db
    .prepare(
      "UPDATE workspace_recovery_plans SET state='armed',undo_bookmark=?,updated_at=? WHERE id=? AND workspace=? AND digest=? AND state='prepared'",
    )
    .bind(undoBookmark, now, plan.id, plan.workspace, plan.digest)
    .run();
  requireValue(
    result.meta.changes === 1,
    "RECOVERY_PLAN_CHANGED",
    "Recovery plan changed before restore was armed.",
    409,
  );
  console.warn(
    JSON.stringify({
      event: "workspace_recovery_armed",
      workspace: plan.workspace,
      plan: plan.id,
      at: now,
    }),
  );
}

export async function rearmRecoveryPlanForUndo(
  db: D1Database,
  plan: RecoveryPlanRow,
  redoBookmark: string,
  now = Date.now(),
) {
  requireBookmark(redoBookmark);
  const result = await db
    .prepare(
      "UPDATE workspace_recovery_plans SET state='armed',undo_bookmark=?,updated_at=? WHERE id=? AND workspace=? AND digest=? AND state='reconciled'",
    )
    .bind(redoBookmark, now, plan.id, plan.workspace, plan.digest)
    .run();
  requireValue(
    result.meta.changes === 1,
    "RECOVERY_PLAN_CHANGED",
    "Recovery plan changed before undo was armed.",
    409,
  );
  console.warn(
    JSON.stringify({
      event: "workspace_recovery_undo_armed",
      workspace: plan.workspace,
      plan: plan.id,
      at: now,
    }),
  );
}

export async function reconcileRecoveryPlan(
  db: D1Database,
  plan: RecoveryPlanRow,
  now = Date.now(),
) {
  const result = await db
    .prepare(
      "UPDATE workspace_recovery_plans SET state='reconciled',updated_at=? WHERE id=? AND workspace=? AND digest=? AND state='armed'",
    )
    .bind(now, plan.id, plan.workspace, plan.digest)
    .run();
  requireValue(
    result.meta.changes === 1,
    "RECOVERY_PLAN_CHANGED",
    "Recovery plan changed before reconciliation completed.",
    409,
  );
  console.warn(
    JSON.stringify({
      event: "workspace_recovery_reconciled",
      workspace: plan.workspace,
      plan: plan.id,
      at: now,
    }),
  );
  return recoveryStatus(db, plan.workspace);
}

async function settleStaleExternalIntents(
  db: D1Database,
  workspace: string,
  now: number,
) {
  const cutoff = now - externalEffectSettleMs;
  const [posts, containers] = await db.batch([
    db
      .prepare(
        "UPDATE external_effects SET status='uncertain',reason=COALESCE(reason,'RECOVERY_STALE_INTENT'),updated_at=? WHERE workspace=? AND status='intent' AND updated_at<=?",
      )
      .bind(now, workspace, cutoff),
    db
      .prepare(
        "UPDATE external_containers SET status='uncertain',reason=COALESCE(reason,'RECOVERY_STALE_INTENT'),updated_at=? WHERE workspace=? AND status='intent' AND updated_at<=?",
      )
      .bind(now, workspace, cutoff),
  ]);
  if (posts.meta.changes || containers.meta.changes)
    console.warn(
      JSON.stringify({
        event: "recovery_stale_external_intent_fenced",
        workspace,
        publications: posts.meta.changes,
        containers: containers.meta.changes,
        at: now,
      }),
    );
}

export async function assertRecoveryCanResume(
  db: D1Database,
  workspace: string,
  now = Date.now(),
) {
  // Fresh intents may still represent provider I/O that began just before
  // quarantine. Give them two minutes to settle. After that the existing D1
  // fingerprint fence remains authoritative, but the uncertainty no longer
  // blocks unrelated work in the recovered workspace forever.
  await settleStaleExternalIntents(db, workspace, now);
  const counts = await effectSummary(db, workspace);
  requireValue(
    counts.intent === 0 && counts.containerIntent === 0,
    "RECOVERY_EFFECTS_IN_FLIGHT",
    "A provider write began immediately before recovery quarantine. Retry after its bounded settlement window; do not republish it.",
    409,
  );
  return counts;
}
