import { test } from "node:test";
import assert from "node:assert/strict";
import { collectEngagement } from "../src/engagement-collector.ts";
import { MemoryStore } from "./helpers.ts";
const scope = (workspace: string, account = "threads-a") => ({
  workspace, account, provider: "threads" as const, active: true,
  readbackGranted: true, rootIds: ["root-1"],
});
test("durable cursor and reply evidence survive restart without duplication", async () => {
  const store = new MemoryStore();
  let calls = 0;
  const read = async ({ cursor }: { cursor?: string }) => {
    calls++;
    return { replies: cursor ? [] : [{ id: "reply-1", rootId: "root-1", text: "Hello", authorUsername: "member-1" }], nextCursor: "cursor-2" };
  };
  const first = await collectEngagement(store, "workspace-a", [scope("workspace-a")], read, 100, 10000);
  const restarted = await collectEngagement(store, "workspace-a", [scope("workspace-a")], read, 200, 10000);
  assert.equal(first.collected, 1);
  assert.equal(restarted.collected, 0);
  assert.equal(calls, 2);
  assert.equal(store.list("engagement:reply:").length, 1);
  assert.equal(store.list<{ cursor: string }>("engagement:checkpoint:")[0].cursor, "cursor-2");
});
test("crash before commit replays safely and no cross-workspace reads occur", async () => {
  const store = new MemoryStore();
  await assert.rejects(collectEngagement(store, "workspace-a", [scope("workspace-b")], async () => { throw Error("must not run"); }, 100, 10000), /Cross-workspace/);
  assert.equal(store.list("engagement:").length, 0);
  let fail = true;
  const read = async () => {
    if (fail) { fail = false; throw Error("process interrupted"); }
    return { replies: [{ id: "reply-1", rootId: "root-1", text: "Hello", authorUsername: "member-1" }], nextCursor: "cursor-2" };
  };
  assert.equal((await collectEngagement(store, "workspace-a", [scope("workspace-a")], read, 100, 10000)).collected, 0);
  assert.equal((await collectEngagement(store, "workspace-a", [scope("workspace-a")], read, 61000, 10000)).collected, 1);
  assert.equal(store.list("engagement:reply:").length, 1);
});
test("permission-held accounts cannot invoke provider reads", async () => {
  const store = new MemoryStore();
  const result = await collectEngagement(store, "workspace-a", [{ ...scope("workspace-a"), readbackGranted: false }], async () => { throw Error("must not run"); }, 100, 10000);
  assert.deepEqual(result, { collected: 0, attempted: 0, held: 1 });
});
test("concurrent scans cannot read or commit the same root twice", async () => {
  const store = new MemoryStore();
  let reads = 0;
  let release!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  const reader = async () => {
    reads++;
    await wait;
    return { replies: [{ id: "reply-1", rootId: "root-1", text: "Hello", authorUsername: "member-1" }], nextCursor: "next" };
  };
  const first = collectEngagement(store, "workspace-a", [scope("workspace-a")], reader, 100, 10000);
  const second = await collectEngagement(store, "workspace-a", [scope("workspace-a")], reader, 100, 10000);
  release();
  assert.equal((await first).collected, 1);
  assert.equal(second.attempted, 0);
  assert.equal(reads, 1);
});
test("workspace operation exposes checkpoint inspection without scan claims or reply content", async () => {
  const { harness } = await import("./helpers.ts");
  const h = harness();
  h.store.put("engagement:checkpoint:test:account:root", { account: "account", rootId: "root", cursor: "next", claimId: "secret-claim" });
  h.store.put("engagement:reply:test:account:root:reply", { text: "private reply" });
  const result = await h.run("engagement_inspect", {});
  assert.equal(result.replyEvidenceCount, 1);
  assert.equal(result.readOnly, true);
  assert.equal(result.publishingAuthority, false);
  assert.equal("claimId" in result.checkpoints[0], false);
  assert.equal(JSON.stringify(result).includes("private reply"), false);
});
