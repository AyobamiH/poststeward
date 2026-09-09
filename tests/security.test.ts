import { test } from "node:test";
import assert from "node:assert/strict";
import { allowOwner } from "../src/auth.ts";
import { boundedBody, limitEdge, limitWorkspace } from "../src/security.ts";
import { MemoryStore } from "./helpers.ts";
import type { Env } from "../src/types.ts";

test("restricted signup rejects unverified and unlisted identities and unknown configuration", () => {
  const env = {
    SIGNUP_MODE: "restricted",
    ALLOWED_OWNER_EMAILS: "owner@example.com",
  } as Env;
  allowOwner({ email: "Owner@example.com", email_verified: true }, env);
  for (const claims of [
    { email: "owner@example.com", email_verified: false },
    { email: "owner@example.com", email_verified: "true" },
    { email: "outsider@example.com", email_verified: true },
    {},
  ])
    assert.throws(() => allowOwner(claims, env), /invited owners/);
  assert.throws(
    () =>
      allowOwner({ email: "owner@example.com", email_verified: true }, {
        ...env,
        SIGNUP_MODE: undefined,
      } as any),
    /invited owners/,
  );
});
test("workspace request budget is atomic, durable and independent of supplied actor or transport", () => {
  const store = new MemoryStore();
  limitWorkspace(store, "2", 1000);
  limitWorkspace(store, "2", 1001);
  assert.throws(() => limitWorkspace(store, "2", 1002), /limit reached/);
  assert.equal(store.get<any>("security:rate").count, 2);
  limitWorkspace(store, "2", 60000);
  assert.equal(store.get<any>("security:rate").count, 1);
  assert.throws(() => limitWorkspace(store, "NaN"), /unavailable/);
});
test("edge admission fails closed and does not trust X-Forwarded-For as the client identity", async () => {
  const keys: string[] = [];
  const limiter = {
    limit: async ({ key }: { key: string }) => {
      keys.push(key);
      return { success: keys.length <= 1 };
    },
  };
  const env = {
    EDGE_LIMITER: limiter,
    LOGIN_LIMITER: limiter,
  } as unknown as Env;
  await limitEdge(
    new Request("https://publish.example/api/session", {
      headers: { "X-Forwarded-For": "1.2.3.4" },
    }),
    env,
  );
  await assert.rejects(
    limitEdge(
      new Request("https://publish.example/api/session", {
        headers: { "X-Forwarded-For": "5.6.7.8" },
      }),
      env,
    ),
    /limit reached/,
  );
  assert.equal(keys[0], keys[1]);
  assert.ok(!keys[0].includes("1.2.3.4"));
  await assert.rejects(
    limitEdge(new Request("https://publish.example/auth/login"), {} as Env),
    /unavailable/,
  );
  await limitEdge(new Request("https://publish.example/help.json"), {} as Env);
});
test("streamed bodies cannot bypass byte or wall-clock limits", async () => {
  const make = (body: ReadableStream) =>
    new Request("https://publish.example/mcp", {
      method: "POST",
      body,
      duplex: "half",
    } as RequestInit);
  const oversized = make(
    new ReadableStream({
      start(c) {
        c.enqueue(new Uint8Array(8));
        c.enqueue(new Uint8Array(8));
        c.close();
      },
    }),
  );
  await assert.rejects(boundedBody(oversized, 10), /size limit/);
  let cancelled = false;
  const slow = make(
    new ReadableStream({
      cancel() {
        cancelled = true;
      },
    }),
  );
  await assert.rejects(boundedBody(slow, 10, 10), /in time/);
  assert.equal(cancelled, true);
  const normal = await boundedBody(
    new Request("https://publish.example/mcp", {
      method: "POST",
      body: '{"ok":true}',
    }),
    100,
  );
  assert.deepEqual(await normal.json(), { ok: true });
});
