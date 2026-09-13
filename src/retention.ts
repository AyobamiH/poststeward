import { digest, requireValue } from "./common.ts";
import type { Campaign, Delivery, Store } from "./types.ts";

export interface RetentionStore extends Store {
  entries(prefix: string): { key: string; value: any }[];
}
const age = 30 * 86400000;
const batch = 100;

function candidates(store: RetentionStore, cutoff: number) {
  const referenced = new Set(store.list<Delivery>("delivery:").map((d) => d.campaign));
  const pilot = store.get<{ id: string }>("pilot:first");
  if (pilot) referenced.add(pilot.id);
  const campaigns = store.list<Campaign>("campaign:")
    .filter((c) => c.createdAt <= cutoff && !referenced.has(c.id))
    .map((value) => ({ key: "campaign:" + value.id, value, action: "delete" as const }));
  const operations = store.entries("operation:")
    .filter(({ value }) => value.status === "complete" &&
      Number.isFinite(value.completedAt) && value.completedAt <= cutoff)
    .map(({ key, value }) => ({ key, value, action: "compact" as const }));
  return [...campaigns, ...operations].sort((a, b) => a.key.localeCompare(b.key)).slice(0, batch);
}

export async function exportRetention(store: RetentionStore, now = Date.now()) {
  const cutoff = now - age;
  const records = candidates(store, cutoff);
  const archive = { workspace: store.get<string>("workspace"), cutoff, records };
  return { ...archive, digest: await digest(archive),
    limits: { batch, minimumAgeDays: 30 },
    retained: "All delivery receipts, uncertain outcomes, pilot proof, credentials, billing state and idempotency fences remain." };
}

export async function commitRetention(store: RetentionStore, input: {
  cutoff: number; digest: string; confirmation: string;
}, now = Date.now()) {
  const workspace = store.get<string>("workspace");
  requireValue(input.confirmation === "PRUNE " + workspace &&
    Number.isFinite(input.cutoff) && input.cutoff <= now - age &&
    input.cutoff >= now - age - 15 * 60000,
    "RETENTION_CONFIRMATION_REQUIRED", "Export again and confirm this workspace within fifteen minutes.", 409);
  const records = candidates(store, input.cutoff);
  requireValue(await digest({ workspace, cutoff: input.cutoff, records }) === input.digest,
    "RETENTION_CHANGED", "The archive no longer matches current state. Export again.", 409);
  return store.tx(() => {
    requireValue(!store.get("lifecycle:deleting") && !store.get("recovery:authority-invalidated"),
      "RETENTION_FENCED", "Finish lifecycle recovery before pruning retained data.", 409);
    // Recompute after the digest await: a concurrent reservation may have
    // started referencing a previously unused campaign.
    requireValue(JSON.stringify(candidates(store, input.cutoff)) === JSON.stringify(records),
      "RETENTION_CHANGED", "Workspace state changed after export.", 409);
    for (const record of records) {
      if (record.action === "delete") store.delete(record.key);
      else store.put(record.key, { hash: record.value.hash, status: "archived" });
    }
    return { prunedCampaigns: records.filter((r) => r.action === "delete").length,
      compactedResults: records.filter((r) => r.action === "compact").length,
      archiveDigest: input.digest, deliveryReceiptsDeleted: 0, idempotencyFencesDeleted: 0 };
  });
}
