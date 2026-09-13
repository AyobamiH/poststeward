import assert from "node:assert/strict";
import test from "node:test";
import { credentialRoots, envelopeRoot, rootIdentifier, seal, unseal } from "../src/crypto.ts";
import { environment, harness, owner, MemoryStore } from "./helpers.ts";
import { cutoverWorkspace } from "../src/root-cutover.ts";

const next = Buffer.alloc(32, 23).toString("base64");
const env = { ...environment, ENCRYPTION_KEY_NEXT: next, ENCRYPTION_ROOT_WRITE: "next" as const };

test("mixed legacy and next roots remain readable; root metadata and context are authenticated", async () => {
  const value = { accessToken: "private-test-credential" };
  const context = "workspace:account";
  const old = await seal(value, environment.ENCRYPTION_KEY, context, "2");
  const newEnvelope = await seal(value, credentialRoots(env), context, "2");
  assert.equal(envelopeRoot(old), undefined);
  assert.equal(envelopeRoot(newEnvelope), await rootIdentifier(next));
  assert.deepEqual(await unseal(old, credentialRoots(env), context), value);
  assert.deepEqual(await unseal(newEnvelope, credentialRoots(env), context), value);
  await assert.rejects(unseal(newEnvelope, environment.ENCRYPTION_KEY, context));
  await assert.rejects(unseal(newEnvelope, credentialRoots(env), "other:account"));
  for (const root of [undefined, await rootIdentifier(environment.ENCRYPTION_KEY), "0".repeat(64), "bad"]) {
    const changed = JSON.parse(newEnvelope); changed.root = root;
    await assert.rejects(unseal(JSON.stringify(changed), credentialRoots(env), context));
  }
  assert.throws(() => credentialRoots({ ...environment, ENCRYPTION_ROOT_WRITE: "next" }), /not configured/);
});

test("saving a next root alone does not activate it; engine writes switch only with explicit configuration", async () => {
  const h = harness();
  h.env.ENCRYPTION_KEY_NEXT = next;
  await h.setup();
  assert.equal(envelopeRoot(h.store.get<any>("account:main")?.secret || h.store.list<any>("account:")[0].secret), undefined);
  h.env.ENCRYPTION_ROOT_WRITE = "next";
  await h.engine.connect(owner, { alias: "next", provider: "threads", accessToken: "a-private-manual-test-token" });
  const account = h.store.get<any>("account:next");
  assert.equal(envelopeRoot(account.secret), await rootIdentifier(next));
  const value: any = await unseal(account.secret, credentialRoots(h.env), owner.workspace + ":next");
  assert.equal(value.accessToken, "a-private-manual-test-token");
});

test("bounded batches resume from ciphertext state and reject unknown families and stale local commits", async () => {
  class InventoryStore extends MemoryStore {
    entries(prefix: string) {
      return [...this.data].filter(([key]) => key.startsWith(prefix)).sort(([a], [b]) => a.localeCompare(b))
        .map(([key, value]) => ({ key, value: structuredClone(value) }));
    }
  }
  const store = new InventoryStore();
  store.put("workspace", "workspace");
  for (let i = 0; i < 27; i++) {
    const alias = "account" + i;
    store.put("account:" + alias, { alias, version: 1, active: false,
      secret: await seal({ accessToken: alias }, environment.ENCRYPTION_KEY, "workspace:" + alias) });
  }
  const runtimeEnv = { ...env, DEPLOY_ENV: "staging", RELEASE_SHA: "a".repeat(40), IDENTITY: {
    prepare: () => ({ bind: () => ({ all: async () => ({ success: true, results: [] }) }) }),
  } as unknown as D1Database };
  let inspect = await cutoverWorkspace(store, runtimeEnv, "workspace", { action: "inspect" });
  assert.equal(inspect.pending, 27);
  let result = await cutoverWorkspace(store, runtimeEnv, "workspace", { action: "migrate", expectedDigest: inspect.inventoryDigest });
  assert.equal(result.changed, 25);
  assert.equal(result.pending, 2);
  inspect = await cutoverWorkspace(store, runtimeEnv, "workspace", { action: "inspect" });
  const tx = store.tx.bind(store);
  store.tx = fn => { const value = store.get<any>("account:account9"); store.put("account:account9", { ...value, version: 2 }); return tx(fn); };
  await assert.rejects(cutoverWorkspace(store, runtimeEnv, "workspace", { action: "migrate", expectedDigest: inspect.inventoryDigest }), { code: "ROOT_INVENTORY_CHANGED" });
  store.tx = tx;
  inspect = await cutoverWorkspace(store, runtimeEnv, "workspace", { action: "inspect" });
  result = await cutoverWorkspace(store, runtimeEnv, "workspace", { action: "migrate", expectedDigest: inspect.inventoryDigest });
  assert.equal(result.changed, 2);
  assert.equal(store.get<any>("account:account9").version, 2);
  assert.equal((await cutoverWorkspace(store, runtimeEnv, "workspace", { action: "inspect" })).verifiedComplete, true);
  store.put("unexpected:credential", { secret: "do-not-ignore-me" });
  await assert.rejects(cutoverWorkspace(store, runtimeEnv, "workspace", { action: "inspect" }), { code: "ROTATION_UNKNOWN_CREDENTIAL" });
});
