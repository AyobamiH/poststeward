import { readProviderBody } from "./providers.ts";
import type { ReadReplies } from "./engagement-collector.ts";
/** A bounded GET-only Threads reply reader; caller owns account-scoped token authority. */
export function threadsReplyReader(
  accessTokenFor: (account: string) => Promise<string>,
  http: typeof fetch = fetch,
): ReadReplies {
  return async ({ account, rootId, cursor, deadlineMs }) => {
    if (!/^[0-9]{1,40}$/.test(rootId)) throw new Error("Invalid Threads root ID");
    if (cursor && (cursor.length > 512 || !/^[A-Za-z0-9_=-]+$/.test(cursor)))
      throw new Error("Invalid Threads pagination cursor");
    const token = await accessTokenFor(account);
    if (!token) throw new Error("Threads read authority unavailable");
    const url = new URL(`https://graph.threads.net/v1.0/${rootId}/replies`);
    url.searchParams.set("fields", "id,text,username");
    url.searchParams.set("limit", "50");
    if (cursor) url.searchParams.set("after", cursor);
    const signal = AbortSignal.timeout(Math.min(15000, Math.max(1, deadlineMs)));
    const response = await http(url.toString(), {
      method: "GET", redirect: "manual", signal,
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      void response.body?.cancel().catch(() => {});
      throw new Error(`Threads read HTTP ${response.status}`);
    }
    const raw = await readProviderBody(response, signal);
    const data: unknown = JSON.parse(raw);
    if (!data || typeof data !== "object" || !Array.isArray((data as any).data))
      throw new Error("Invalid Threads reply response");
    const entries = (data as any).data as unknown[];
    if (entries.length > 100) throw new Error("Threads reply page too large");
    const replies = entries.map((item) => {
      if (!item || typeof item !== "object") throw new Error("Invalid reply entry");
      const row = item as Record<string, unknown>;
      if (typeof row.id !== "string" || !/^[0-9]{1,40}$/.test(row.id) ||
          typeof row.text !== "string" || typeof row.username !== "string")
        throw new Error("Invalid Threads reply identity");
      return { id: row.id, rootId, text: row.text, authorUsername: row.username };
    });
    const next = (data as any).paging?.cursors?.after;
    if (next !== undefined && (typeof next !== "string" || next.length > 512))
      throw new Error("Invalid Threads next cursor");
    return { replies, nextCursor: next };
  };
}
