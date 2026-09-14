import { Fault } from "./common.ts";

export interface RecoveryPitrStorage {
  sync?: () => Promise<void>;
  getCurrentBookmark?: () => Promise<string>;
  getBookmarkForTime?: (timestamp: number | Date) => Promise<string>;
}

type PitrStage = "sync" | "current_bookmark" | "target_bookmark";

function safeErrorName(error: unknown) {
  const name = error instanceof Error ? error.name : "NonError";
  return /^[A-Za-z0-9_.:-]{1,80}$/.test(name) ? name : "UnknownError";
}

function pitrPrimitiveFailure(stage: PitrStage, error: unknown): never {
  // Platform error messages can contain opaque provider references. Record only
  // the bounded primitive and error class; never bookmark values or raw text.
  console.warn(
    JSON.stringify({
      event: "workspace_recovery_pitr_primitive_failed",
      stage,
      errorName: safeErrorName(error),
    }),
  );

  if (stage === "sync")
    throw new Fault(
      "RECOVERY_PITR_SYNC_FAILED",
      "Point-in-time recovery could not synchronise Durable Object storage.",
      502,
    );
  if (stage === "current_bookmark")
    throw new Fault(
      "RECOVERY_PITR_CURRENT_BOOKMARK_FAILED",
      "Point-in-time recovery could not read the current Durable Object bookmark.",
      502,
    );
  throw new Fault(
    "RECOVERY_PITR_TARGET_BOOKMARK_FAILED",
    "Point-in-time recovery could not resolve the requested Durable Object bookmark.",
    502,
  );
}

async function syncRecoveryStorage(storage: RecoveryPitrStorage) {
  if (!storage.sync) return;
  try {
    await storage.sync();
  } catch (error) {
    pitrPrimitiveFailure("sync", error);
  }
}

/**
 * Capture an exact bookmark for the current durable state. This primitive is
 * the basis for durable recovery checkpoints and does not depend on Cloudflare's
 * approximate timestamp-to-bookmark lookup.
 */
export async function captureCurrentRecoveryBookmark(
  storage: RecoveryPitrStorage,
) {
  await syncRecoveryStorage(storage);
  try {
    return await storage.getCurrentBookmark!();
  } catch (error) {
    pitrPrimitiveFailure("current_bookmark", error);
  }
}

/**
 * Capture both bookmarks for the legacy approximate timestamp path without
 * conflating platform failures. Timestamp recovery remains an optional
 * convenience; exact checkpoints should be preferred when available.
 */
export async function captureRecoveryBookmarks(
  storage: RecoveryPitrStorage,
  targetTime: number,
) {
  const preRestoreBookmark = await captureCurrentRecoveryBookmark(storage);

  let targetBookmark: string;
  try {
    targetBookmark = await storage.getBookmarkForTime!(targetTime);
  } catch (error) {
    pitrPrimitiveFailure("target_bookmark", error);
  }

  return { preRestoreBookmark, targetBookmark };
}
