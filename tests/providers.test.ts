import { test } from "node:test";
import assert from "node:assert/strict";
import { SocialProviders, validateText } from "../src/providers.ts";
import type { Delivery } from "../src/types.ts";
const delivery = {
  provider: "x",
  identity: { id: "123", username: "me" },
  text: "Approved text",
  postId: "99",
} as Delivery;
const credential = { accessToken: "test-only-token" };
test("provider network errors and 5xx are ambiguous after the write boundary", async () => {
  for (const http of [
    async () => {
      throw new Error("timeout");
    },
    async () => new Response("{}", { status: 503 }),
  ]) {
    const p = new SocialProviders(http as typeof fetch);
    await assert.rejects(p.publish(delivery, credential), {
      code: "AMBIGUOUS_PROVIDER_WRITE",
    });
  }
});
test("a successful response without a creation ID cannot be called failed or retried", async () => {
  const p = new SocialProviders((async () =>
    Response.json({ data: {} })) as typeof fetch);
  await assert.rejects(p.publish(delivery, credential), {
    code: "AMBIGUOUS_PROVIDER_WRITE",
  });
});
test("LinkedIn preserves the creation URN and independently reads back exact author and commentary", async () => {
  let payload: any;
  let writes = 0;
  let reads = 0;
  const p = new SocialProviders((async (url, init) => {
    if (init?.method === "POST") {
      writes++;
      payload = JSON.parse(init.body as string);
      return new Response(null, {
        status: 201,
        headers: { "x-restli-id": "urn:li:share:123" },
      });
    }
    reads++;
    assert.match(String(url), /\/rest\/posts\/urn%3Ali%3Ashare%3A123\?viewContext=AUTHOR$/);
    assert.equal(new Headers(init?.headers).get("LinkedIn-Version"), "202608");
    return Response.json({
      id: "urn:li:share:123",
      author: "urn:li:person:42",
      commentary: "Approved text",
      lifecycleState: "PUBLISHED",
    });
  }) as typeof fetch);
  const d = {
    ...delivery,
    provider: "linkedin",
    identity: { id: "urn:li:person:42", username: "Person" },
  } as Delivery;
  const result = await p.publish(d, credential);
  d.postId = result.id;
  d.url = result.url;
  assert.equal(result.id, "urn:li:share:123");
  assert.equal(payload.author, "urn:li:person:42");
  assert.equal(payload.commentary, "Approved text");
  const evidence = await p.verify(d, credential);
  assert.equal(evidence.verified, true);
  assert.equal(evidence.url, "https://www.linkedin.com/feed/update/urn%3Ali%3Ashare%3A123/");
  assert.equal(writes, 1);
  assert.equal(reads, 1);
});
test("LinkedIn readback rejects wrong author, text, lifecycle or post ID", async () => {
  const base = {
    ...delivery,
    provider: "linkedin",
    postId: "urn:li:share:123",
    identity: { id: "urn:li:person:42", username: "Person" },
  } as Delivery;
  const samples = [
    { id: "urn:li:share:999", author: base.identity.id, commentary: base.text, lifecycleState: "PUBLISHED" },
    { id: base.postId, author: "urn:li:person:attacker", commentary: base.text, lifecycleState: "PUBLISHED" },
    { id: base.postId, author: base.identity.id, commentary: "Changed", lifecycleState: "PUBLISHED" },
    { id: base.postId, author: base.identity.id, commentary: base.text, lifecycleState: "DRAFT" },
  ];
  for (const sample of samples) {
    const p = new SocialProviders((async () => Response.json(sample)) as typeof fetch);
    assert.equal((await p.verify(base, credential)).verified, false);
  }
});
test("LinkedIn readback permission denial is not converted into a verified receipt", async () => {
  const p = new SocialProviders(
    (async () => new Response("{}", { status: 403 })) as typeof fetch,
  );
  const d = {
    ...delivery,
    provider: "linkedin",
    postId: "urn:li:share:123",
    identity: { id: "urn:li:person:42", username: "Person" },
  } as Delivery;
  await assert.rejects(p.verify(d, credential), { code: "PROVIDER_HTTP_403" });
});
test("Threads readiness 404 remains transient before any publish request", async () => {
  const p = new SocialProviders(
    (async () => new Response("{}", { status: 404 })) as typeof fetch,
  );
  assert.equal(await p.containerStatus("container", credential), "NOT_VISIBLE");
});
test("X weighted length accepts URL handling and rejects oversized emoji payloads unchanged", () => {
  assert.doesNotThrow(() =>
    validateText("x", "Read " + "https://example.com/" + "a".repeat(300)),
  );
  assert.throws(() => validateText("x", "😀".repeat(141)), {
    code: "CONTENT_TOO_LONG",
  });
});
test("metrics failure is unavailable rather than a zero snapshot", async () => {
  const p = new SocialProviders(
    (async () => new Response("{}", { status: 403 })) as typeof fetch,
  );
  const metrics: any = await p.metrics(delivery, credential);
  assert.equal(metrics.availability, "unavailable");
  assert.equal(metrics.values, undefined);
});
