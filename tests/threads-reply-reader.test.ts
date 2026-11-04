import { test } from "node:test";
import assert from "node:assert/strict";
import { threadsReplyReader } from "../src/threads-reply-reader.ts";
test("Threads reader uses GET and exact account token without provider writes", async () => {
  let requests = 0;
  const read = threadsReplyReader(async (account) => {
    assert.equal(account, "customer-threads");
    return "fixture-token";
  }, (async (url, init) => {
    requests++;
    assert.equal(init?.method, "GET");
    assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer fixture-token");
    assert.equal(new URL(String(url)).pathname, "/v1.0/123/replies");
    return Response.json({ data: [{ id: "456", text: "hello", username: "visitor" }], paging: { cursors: { after: "next" } } });
  }) as typeof fetch);
  const result = await read({ account: "customer-threads", rootId: "123", deadlineMs: 1000 });
  assert.equal(requests, 1);
  assert.deepEqual(result.replies, [{ id: "456", rootId: "123", text: "hello", authorUsername: "visitor" }]);
  assert.equal(result.nextCursor, "next");
});
test("reader fails closed on denied read authority", async () => {
  const read = threadsReplyReader(async () => "token", (async () => new Response("{}", { status: 403 })) as typeof fetch);
  await assert.rejects(read({ account: "a", rootId: "123", deadlineMs: 1000 }), /HTTP 403/);
});
