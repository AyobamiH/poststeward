import { requireValue } from "./common.ts";

export interface WorkspaceDeletion {
  workspace: string;
  state: "pending" | "completed";
  requested_at: number;
  completed_at?: number;
  updated_at: number;
}

export async function workspaceDeletion(
  db: D1Database,
  workspace: string,
): Promise<WorkspaceDeletion | undefined> {
  return (
    (await db
      .prepare("SELECT * FROM workspace_deletions WHERE workspace=?")
      .bind(workspace)
      .first<WorkspaceDeletion>()) || undefined
  );
}

export async function assertWorkspaceNotDeleted(
  db: D1Database,
  workspace: string,
) {
  const row = await workspaceDeletion(db, workspace);
  requireValue(
    !row,
    "WORKSPACE_DELETION_IN_PROGRESS",
    row?.state === "completed"
      ? "This workspace was deleted and cannot be reactivated. Sign in again to create a new workspace."
      : "Workspace deletion is in progress. Only the deletion retry endpoint remains available.",
    410,
  );
}

export async function beginWorkspaceDeletion(
  db: D1Database,
  workspace: string,
  now = Date.now(),
) {
  const existing = await workspaceDeletion(db, workspace);
  requireValue(
    existing?.state !== "completed",
    "WORKSPACE_DELETED",
    "This workspace has already been deleted.",
    410,
  );
  if (existing) return existing;
  const result = await db
    .prepare(
      "INSERT INTO workspace_deletions(workspace,state,requested_at,completed_at,updated_at) VALUES (?,'pending',?,NULL,?) ON CONFLICT(workspace) DO NOTHING",
    )
    .bind(workspace, now, now)
    .run();
  requireValue(
    result.meta.changes === 1,
    "WORKSPACE_DELETE_RACE",
    "Workspace deletion state changed. Inspect status before retrying.",
    409,
  );
  console.warn(
    JSON.stringify({ event: "workspace_deletion_started", workspace, at: now }),
  );
  return workspaceDeletion(db, workspace);
}

/**
 * D1 cleanup is one batch so a failed purge leaves the owner session available
 * to retry an already-pending deletion. The completed tombstone intentionally
 * remains outside the deleted Durable Object and is never removed by retention.
 */
export async function completeWorkspaceDeletion(
  db: D1Database,
  workspace: string,
  now = Date.now(),
) {
  const row = await workspaceDeletion(db, workspace);
  requireValue(
    row?.state === "pending",
    "WORKSPACE_DELETE_NOT_PENDING",
    "Workspace deletion has not been started.",
    409,
  );
  const statements = [
    db.prepare("DELETE FROM stripe_events WHERE workspace=?").bind(workspace),
    db.prepare("DELETE FROM stripe_customers WHERE workspace=?").bind(workspace),
    db.prepare("DELETE FROM external_effects WHERE workspace=?").bind(workspace),
    db.prepare("DELETE FROM external_containers WHERE workspace=?").bind(workspace),
    db.prepare("DELETE FROM workspace_recovery_plans WHERE workspace=?").bind(workspace),
    db.prepare("DELETE FROM workspace_controls WHERE workspace=?").bind(workspace),
    db.prepare("DELETE FROM provider_oauth_states WHERE workspace=?").bind(workspace),
    db.prepare("DELETE FROM grants WHERE workspace=?").bind(workspace),
    // owner_proofs and remaining provider OAuth state cascade with sessions.
    db.prepare("DELETE FROM sessions WHERE workspace=?").bind(workspace),
    db.prepare("DELETE FROM principals WHERE workspace=?").bind(workspace),
    db.prepare(
      "UPDATE workspace_deletions SET state='completed',completed_at=?,updated_at=? WHERE workspace=? AND state='pending'",
    ).bind(now, now, workspace),
  ];
  const results = await db.batch(statements);
  requireValue(
    results.at(-1)?.meta.changes === 1,
    "WORKSPACE_DELETE_COMMIT_FAILED",
    "Workspace deletion did not reach its completed tombstone.",
    500,
  );
  console.warn(
    JSON.stringify({ event: "workspace_deletion_completed", workspace, at: now }),
  );
  return workspaceDeletion(db, workspace);
}
