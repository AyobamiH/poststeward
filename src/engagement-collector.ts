import type { Store } from "./types.ts";
import { planEngagementReads, type EngagementScope } from "./engagement-read-plan.ts";

/** No posting/replying methods are available to this read-only collector. */
export interface ReplyPage {
  replies: { id: string; rootId: string; text: string; authorUsername: string }[];
  nextCursor?: string;
  retryAt?: number;
}
export type ReadReplies = (input: {
  account: string;
  rootId: string;
  cursor?: string;
  deadlineMs: number;
}) => Promise<ReplyPage>;
export interface EngagementCheckpoint {
  account: string;
  rootId: string;
  cursor?: string;
  lastSuccessAt?: number;
  retryAt?: number;
  error?: string;
  claimUntil?: number;
  claimId?: string;
}
const idPattern = /^[A-Za-z0-9_-]{1,128}$/;
const keyFor = (workspace: string, account: string, root: string) =>
  `engagement:checkpoint:${workspace}:${account}:${root}`;
const replyKey = (workspace: string, account: string, root: string, id: string) =>
  `engagement:reply:${workspace}:${account}:${root}:${id}`;
/**
 * One bounded, sequential, read-only pass. Checkpoints advance atomically with
 * reply evidence. If a process crashes before that transaction, replaying the
 * page is idempotent; after commit, the cursor prevents repeated reads.
 */
export async function collectEngagement(
  store: Store,
  workspace: string,
  scopes: readonly EngagementScope[],
  readReplies: ReadReplies,
  now: number,
  budgetMs: number,
): Promise<{ collected: number; attempted: number; held: number }> {
  const plan = planEngagementReads(workspace, scopes, now, budgetMs);
  let collected = 0, attempted = 0, held = plan.skipped.length;
  for (const account of plan.ready) {
    if (account.provider !== "threads") continue;
    for (const rootId of account.roots) {
      if (!idPattern.test(rootId)) throw new Error("Invalid receipt-backed root ID");
      const key = keyFor(workspace, account.account, rootId);
      const checkpoint = store.get<EngagementCheckpoint>(key) || { account: account.account, rootId };
      if (checkpoint.retryAt && checkpoint.retryAt > now) { held++; continue; }
      if (account.budgetMs <= 0) { held++; continue; }
      const claimId = crypto.randomUUID();
      const claimed = store.tx(() => {
        const current = store.get<EngagementCheckpoint>(key) || { account: account.account, rootId };
        if ((current.claimUntil || 0) > now) return false;
        store.put(key, { ...current, claimId, claimUntil: now + Math.max(1000, account.budgetMs) });
        return true;
      });
      if (!claimed) { held++; continue; }
      attempted++;
      let page: ReplyPage;
      try {
        page = await readReplies({ account: account.account, rootId, cursor: checkpoint.cursor, deadlineMs: account.budgetMs });
        if (!Array.isArray(page.replies) || page.replies.length > 100 ||
          (page.nextCursor !== undefined && (!page.nextCursor || page.nextCursor.length > 512)))
          throw new Error("Invalid provider reply page");
        for (const reply of page.replies) {
          if (!idPattern.test(reply.id) || reply.rootId !== rootId || !reply.authorUsername ||
              typeof reply.text !== "string" || reply.text.length > 10000)
            throw new Error("Invalid provider reply evidence");
        }
      } catch {
        // Do not advance a cursor or claim a successful read after an error.
        store.tx(() => {
          const current = store.get<EngagementCheckpoint>(key);
          if (current?.claimId === claimId)
            store.put(key, { ...checkpoint, retryAt: now + 60000, error: "read_unavailable" });
        });
        held++;
        continue;
      }
      store.tx(() => {
        const current = store.get<EngagementCheckpoint>(key);
        if (current?.claimId !== claimId || (current.claimUntil || 0) <= now)
          throw new Error("Engagement scan claim superseded");
        for (const reply of page.replies) {
          const replyId = replyKey(workspace, account.account, rootId, reply.id);
          if (!store.get(replyId)) {
            store.put(replyId, { ...reply, workspace, account: account.account, observedAt: now });
            collected++;
          }
        }
        store.put(key, {
          account: account.account, rootId, cursor: page.nextCursor ?? checkpoint.cursor,
          lastSuccessAt: now, retryAt: page.retryAt, error: undefined,
        } satisfies EngagementCheckpoint);
      });
    }
  }
  return { collected, attempted, held };
}
