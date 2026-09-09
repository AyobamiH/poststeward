import assert from "node:assert/strict";
import test from "node:test";
import { readProviderBody, safeThreadsUrl, SocialProviders } from "../src/providers.ts";
import type { Delivery } from "../src/types.ts";
const credential = { accessToken: "private-test-only-token" };
const delivery = { provider: "threads", identity: { id: "123", username: "previous_name" }, postId: "456", text: "Exact copy", containerId: "789" } as Delivery;

test("Threads readback proves stable owner, post ID and exact copy, not a recyclable username", async () => {
  for (const [data, verified] of [
    [{ id: "456", owner: { id: "123" }, username: "renamed_account", text: "Exact copy" }, true],
    [{ id: "456", owner: { id: "attacker" }, username: "previous_name", text: "Exact copy" }, false],
    [{ id: "456", username: "previous_name", text: "Exact copy" }, false],
    [{ id: "456", owner: { id: "123" }, text: "Changed copy" }, false],
    [{ id: "wrong-post", owner: { id: "123" }, text: "Exact copy" }, false],
  ] as const) {
    let requests = 0;
    const p = new SocialProviders((async (url, init) => {
      requests++; assert.equal(init?.method || "GET", "GET"); assert.equal(init?.redirect, "manual");
      const u = new URL(String(url)); assert.ok(u.searchParams.get("fields")?.split(",").includes("owner"));
      return Response.json(data);
    }) as typeof fetch);
    assert.equal((await p.verify(delivery, credential)).verified, verified); assert.equal(requests, 1);
  }
});

test("provider navigation rejects lookalike hosts, credentials and executable schemes", () => {
  const good = "https://www.threads.com/@owner/post/ABC-123";
  assert.equal(safeThreadsUrl(good + "?tracking=1#x"), good);
  for (const url of ["https://www.threads.com.evil.example/@owner/post/ABC", "https://www.threads.evil/@owner/post/ABC", "https://user:password@www.threads.com/@owner/post/ABC", "http://www.threads.com/@owner/post/ABC", "javascript:alert(1)", "https://www.threads.com:8443/@owner/post/ABC", "https://www.threads.com/not-a-post"])
    assert.equal(safeThreadsUrl(url), undefined);
});

test("a 2xx public write followed by stream loss, bad JSON or oversized evidence remains ambiguous", async () => {
  const responses = [
    () => new Response(new ReadableStream({ start(controller) { controller.error(new Error("secret-bearing socket failure")); } })),
    () => new Response("{ incomplete"),
    () => new Response("x".repeat(262145)),
    () => Response.json(null),
  ];
  for (const response of responses) {
    let attempts = 0;
    const p = new SocialProviders((async () => { attempts++; return response(); }) as typeof fetch);
    await assert.rejects(p.publish({ ...delivery, provider: "x" }, credential), (error: any) => {
      assert.equal(error.code, "AMBIGUOUS_PROVIDER_WRITE"); assert.doesNotMatch(error.message, /secret-bearing|private-test/); return true;
    });
    assert.equal(attempts, 1);
  }
});

test("provider response deadline includes a stalled body and cancels its reader", async () => {
  const abort = new AbortController(); let cancelled = false;
  const response = new Response(new ReadableStream({ pull() { return new Promise(() => {}); }, cancel() { cancelled = true; } }));
  const read = readProviderBody(response, abort.signal); abort.abort();
  await assert.rejects(read); await new Promise((resolve) => setTimeout(resolve, 0)); assert.equal(cancelled, true);
});

test("Threads container creation explicitly disables immediate publication", async () => {
  const p = new SocialProviders((async (url, init) => {
    assert.ok(String(url).endsWith("/123/threads")); assert.equal(init?.method, "POST");
    const body = new URLSearchParams(init!.body as URLSearchParams);
    assert.equal(body.get("auto_publish_text"), "false"); assert.equal(body.get("text"), delivery.text);
    return Response.json({ id: "789" });
  }) as typeof fetch);
  assert.equal(await p.createContainer(delivery, credential), "789");
});
