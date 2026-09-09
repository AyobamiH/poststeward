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
test("LinkedIn uses the server creation header and reports unverified readback", async () => {
  let payload: any;
  const p = new SocialProviders((async (url, init) => {
    payload = JSON.parse(init!.body as string);
    return new Response(null, {
      status: 201,
      headers: { "x-restli-id": "urn:li:share:123" },
    });
  }) as typeof fetch);
  const d = {
    ...delivery,
    provider: "linkedin",
    identity: { id: "urn:li:person:42", username: "Person" },
  } as Delivery;
  const result = await p.publish(d, credential);
  assert.equal(result.id, "urn:li:share:123");
  assert.equal(payload.author, "urn:li:person:42");
  assert.equal(payload.commentary, "Approved text");
  assert.equal((await p.verify(d, credential)).verified, false);
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
