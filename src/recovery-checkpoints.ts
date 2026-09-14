import { digest, requireValue, uid } from "./common.ts";
import { effectSummary } from "./effects.ts";
import {
  captureCurrentRecoveryBookmark,
  type RecoveryPitrStorage,
} from "./recovery-pitr.ts";
import { SQLiteStore } from "./store.ts";
import type { Env } from "./types.ts";

export type RecoveryCheckpointSource = "automatic" | "owner" | "release";

export interface RecoveryCheckpointRow {
  id: string;
  workspace: string;
  bookmark: string;
  captured_at: number;
  release: string;
  root_write: string;
  state_digest: string;
  source: RecoveryCheckpointSource;
  created_at: number;
}

const thirtyDaysMs = 30 * 86400000;
const maxRetainedPerWorkspace = 128;

function requireBookmark(bookmark: string) {
  requireValue(
    typeof bookmark === "string" && bookmark.length >= 16 && bookmark.length <= 256,
    "RECOVERY_BOOKMARK_INVALID",
    "Durable storage did not return a usable recovery bookmark.",
    502,
  );
}

function publicCheckpoint(row: RecoveryCheckpointRow) {
  return {
    id: row.id,
    capturedAt: row.captured_at,
    release: row.release,
    rootWrite: row.root_write,
    stateDigest: row.state_digest,
    source: row.source,
  };
}

export async function checkpointStateDigest(input: {
  release: string;
  rootWrite: string;
  usage: { records: number; bytes: number };
  publishingPaused: boolean;
  accountCount: number;
  profileCount: number;
  deliveryCount: number;
  effects: Record<string, number>;
}) {
  // This intentionally hashes a non-secret operational summary, not encrypted
  // credential values. It is evidence for checkpoint selection/reconciliation,
  // not a claim that the digest authenticates the entire Durable Object image.
  return digest({ version: 1, ...input });
}

export async function createRecoveryCheckpoint(
  db: D1Database,
  input: {
    workspace: string;
    bookmark: string;
    capturedAt: number;
    release: string;
    rootWrite: string;
    stateDigest: string;
    source: RecoveryCheckpointSource;
  },
  now = Date.now(),
) {
  requireBookmark(input.bookmark);
  requireValue(
    Number.isFinite(input.capturedAt) &&
      input.capturedAt <= now + 60000 &&
      input.capturedAt >= now - thirtyDaysMs,
    "RECOVERY_CHECKPOINT_TIME_INVALID",
    "Recovery checkpoint time must fall inside the hosted PITR retention window.",
    409,
  );
  requireValue(
    /^[a-f0-9]{64}$/.test(input.stateDigest),
    "RECOVERY_CHECKPOINT_DIGEST_INVALID",
    "Recovery checkpoint state digest is invalid.",
    500,
  );
  requireValue(
    /^[a-f0-9]{40}$/.test(input.release),
    "RECOVERY_CHECKPOINT_RELEASE_INVALID",
    "Recovery checkpoint release identity is invalid.",
    500,
  );
  requireValue(
    input.rootWrite === "legacy" || input.rootWrite === "next",
    "RECOVERY_CHECKPOINT_ROOT_INVALID",
    "Recovery checkpoint root writer is invalid.",
    500,
  );

  const id = uid();
  await db
    .prepare(
      "INSERT INTO workspace_recovery_checkpoints(id,workspace,bookmark,captured_at,release,root_write,state_digest,source,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
    )
    .bind(
      id,
      input.workspace,
      input.bookmark,
      input.capturedAt,
      input.release,
      input.rootWrite,
      input.stateDigest,
      input.source,
      now,
    )
    .run();

  // Bookmarks themselves remain private. Keep a bounded 30-day catalogue so
  // recovery selection does not become a second unbounded state store.
  await db
    .prepare(
      `DELETE FROM workspace_recovery_checkpoints
       WHERE workspace=? AND (
         captured_at < ? OR id NOT IN (
           SELECT id FROM workspace_recovery_checkpoints
           WHERE workspace=? ORDER BY captured_at DESC LIMIT ?
         )
       )`,
    )
    .bind(input.workspace, now - thirtyDaysMs, input.workspace, maxRetainedPerWorkspace)
    .run();

  console.warn(
    JSON.stringify({
      event: "workspace_recovery_checkpoint_captured",
      workspace: input.workspace,
      checkpoint: id,
      source: input.source,
      release: input.release,
      capturedAt: input.capturedAt,
    }),
  );

  return publicCheckpoint({
    id,
    workspace: input.workspace,
    bookmark: input.bookmark,
    captured_at: input.capturedAt,
    release: input.release,
    root_write: input.rootWrite,
    state_digest: input.stateDigest,
    source: input.source,
    created_at: now,
  });
}

export async function captureWorkspaceRecoveryCheckpoint(
  db: D1Database,
  store: SQLiteStore,
  storage: RecoveryPitrStorage,
  env: Env,
  workspace: string,
  source: RecoveryCheckpointSource,
  now = Date.now(),
) {
  const bookmark = await captureCurrentRecoveryBookmark(storage);
  const effects = await effectSummary(db, workspace);
  const stateDigest = await checkpointStateDigest({
    release: env.RELEASE_SHA,
    rootWrite: env.ENCRYPTION_ROOT_WRITE === "next" ? "next" : "legacy",
    usage: store.usage(),
    publishingPaused:
      env.PUBLISHING_PAUSED === "true" || store.get<boolean>("paused") === true,
    accountCount: store.list("account:").length,
    profileCount: store.list("profile:").length,
    deliveryCount: store.list("delivery:").length,
    effects,
  });
  return createRecoveryCheckpoint(
    db,
    {
      workspace,
      bookmark,
      capturedAt: now,
      release: env.RELEASE_SHA,
      rootWrite: env.ENCRYPTION_ROOT_WRITE === "next" ? "next" : "legacy",
      stateDigest,
      source,
    },
    now,
  );
}

export async function listRecoveryCheckpoints(
  db: D1Database,
  workspace: string,
  now = Date.now(),
) {
  const result = await db
    .prepare(
      "SELECT * FROM workspace_recovery_checkpoints WHERE workspace=? AND captured_at>=? ORDER BY captured_at DESC LIMIT ?",
    )
    .bind(workspace, now - thirtyDaysMs, maxRetainedPerWorkspace)
    .all<RecoveryCheckpointRow>();
  return result.results.map(publicCheckpoint);
}

export async function requireRecoveryCheckpoint(
  db: D1Database,
  workspace: string,
  id: string,
  now = Date.now(),
) {
  const row = await db
    .prepare("SELECT * FROM workspace_recovery_checkpoints WHERE id=? AND workspace=?")
    .bind(id, workspace)
    .first<RecoveryCheckpointRow>();
  requireValue(
    row && row.captured_at >= now - thirtyDaysMs && row.captured_at <= now + 60000,
    "RECOVERY_CHECKPOINT_UNAVAILABLE",
    "The selected exact recovery checkpoint is missing or outside the hosted PITR retention window.",
    409,
  );
  requireBookmark(row.bookmark);
  return row;
}
