import { test } from "node:test";
import assert from "node:assert/strict";
import { MemoryStore } from "./helpers.ts";
import { collectEngagement } from "../src/engagement-collector.ts";
import { threadsReplyReader } from "../src/threads-reply-reader.ts";
const scope = { workspace: "tenant-one", account: "threads-one", provider: "threads" as const, active: true, readbackGranted: true, rootIds: ["123"] };
test("two actual GET pagination pages checkpoint independently and never mix account tokens", async () => {
  const store = new MemoryStore();
  const cursors: (string | null)[] = [];
  const reader = threadsReplyReader(async (alias) => { assert.equal(alias, "threads-one"); return "tenant-one-token"; }, (async (url, options) => {
    assert.equal(options?.method, "GET");
    assert.equal(new Headers(options?.headers).get("Authorization"), "Bearer tenant-one-token");
    const cursor = new URL(String(url)).searchParams.get("after");
    cursors.push(cursor);
    return Response.json(cursor === null ? { data: [{ id: "456", text: "first", username: "person-a" }], paging: { cursors: { after: "next_page" } } } : { data: [{ id: "789", text: "second", username: "person-b" }] });
  }) as typeof fetch);
  assert.equal((await collectEngagement(store, "tenant-one", [scope], reader, 100, 10000)).collected, 1);
  assert.equal((await collectEngagement(store, "tenant-one", [scope], reader, 10200, 10000)).collected, 1);
  assert.deepEqual(cursors, [null, "next_page"]);
  assert.equal(store.list("engagement:reply:").length, 2);
  assert.equal(store.list<{cursor:string}>("engagement:checkpoint:")[0].cursor, "next_page");
});
test("readback permission denial holds without advancing the cursor or publishing", async () => {
  const store = new MemoryStore();
  let requests = 0;
  const reader = threadsReplyReader(async () => "token", (async (_url, options) => {
    requests++;
    assert.equal(options?.method, "GET");
    return new Response("{}", { status: 403 });
  }) as typeof fetch);
  const result = await collectEngagement(store, "tenant-one", [scope], reader, 100, 10000);
  assert.equal(result.collected, 0);
  assert.equal(result.held, 1);
  assert.equal(requests, 1);
  assert.equal(store.list("engagement:reply:").length, 0);
  assert.equal(store.list<{cursor?:string}>("engagement:checkpoint:")[0].cursor, undefined);
  assert.equal((await collectEngagement(store, "tenant-one", [scope], reader, 101, 10000)).attempted, 0);
});
