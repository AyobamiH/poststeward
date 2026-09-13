import { Fault } from "./common.ts";

export interface RecoveryPitrStorage {
  sync?: () => Promise<void>;
  getCurrentBookmark: () => Promise<string>;
  getBookmarkForTime: (timestamp: number | Date) => Promise<string>;
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

/**
 * Capture both PITR bookmarks without conflating platform failures. The sync is
 * a persistence barrier only; it does not retry or restore anything.
 */
export async function captureRecoveryBookmarks(
  storage: RecoveryPitrStorage,
  targetTime: number,
) {
  if (storage.sync) {
    try {
      await storage.sync();
    } catch (error) {
      pitrPrimitiveFailure("sync", error);
    }
  }

  let preRestoreBookmark: string;
  try {
    preRestoreBookmark = await storage.getCurrentBookmark();
  } catch (error) {
    pitrPrimitiveFailure("current_bookmark", error);
  }

  let targetBookmark: string;
  try {
    targetBookmark = await storage.getBookmarkForTime(targetTime);
  } catch (error) {
    pitrPrimitiveFailure("target_bookmark", error);
  }

  return { preRestoreBookmark, targetBookmark };
}
