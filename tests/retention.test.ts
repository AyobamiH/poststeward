import assert from "node:assert/strict";
import test from "node:test";
import { exportRetention, commitRetention } from "../src/retention.ts";
import { harness } from "./helpers.ts";

function fixture() {
  const h = harness();
  const store = Object.assign(h.store, {
    entries(prefix: string) {
      return [...h.store.data.entries()].filter(([key]) => key.startsWith(prefix))
        .map(([key, value]) => ({ key, value: structuredClone(value) }));
    },
  });
  return { ...h, store };
}
test("retention exports first, retains uncertain receipts and blocks replay after compaction", async () => {
  const h = fixture();
  await h.setup();
  const campaign = await h.campaign("Old unused draft");
  h.advance(31 * 86400000);
  h.store.put("delivery:uncertain", { id: "uncertain", campaign: "protected", status: "uncertain" });
  h.store.put("campaign:protected", { id: "protected", createdAt: 1, text: { account: "keep" } });
  const snapshot = await exportRetention(h.store, h.now());
  assert.ok(snapshot.records.some((r) => r.key === "campaign:" + campaign.id));
  assert.ok(!snapshot.records.some((r) => r.key === "campaign:protected"));
  assert.ok(h.store.get("campaign:" + campaign.id));
  const result = await commitRetention(h.store, { ...snapshot,
    confirmation: "PRUNE " + h.store.get("workspace") }, h.now());
  assert.equal(result.prunedCampaigns, 1);
  assert.ok(h.store.get("delivery:uncertain"));
  assert.ok(h.store.get("campaign:protected"));
  const before = h.store.list("project:").length;
  await assert.rejects(h.run("project_put", { id: "project", name: "Project",
    accounts: ["account"], idempotencyKey: "project-account" }), /archived/);
  assert.equal(h.store.list("project:").length, before);
});
test("changed archive and fresh drafts cannot be pruned", async () => {
  const h = fixture();
  await h.setup();
  const c = await h.campaign();
  const fresh = await exportRetention(h.store, h.now());
  assert.equal(fresh.records.length, 0);
  h.advance(31 * 86400000);
  const old = await exportRetention(h.store, h.now());
  h.store.put("delivery:new", { campaign: c.id, status: "scheduled" });
  await assert.rejects(commitRetention(h.store, { ...old,
    confirmation: "PRUNE " + h.store.get("workspace") }, h.now()), /no longer matches/);
  assert.ok(h.store.get("campaign:" + c.id));
});
