import assert from "node:assert/strict";
import test from "node:test";
import { seal, unseal } from "../src/crypto.ts";
import {
  rewrapWorkspaceCredentials,
  rootRotationMaintenance,
  rotateCredentialEnvelope,
  verifyWorkspaceCredentials,
} from "../src/root-rotation-runtime.ts";
import type { Account, Env } from "../src/types.ts";
import { MemoryStore, environment, owner } from "./helpers.ts";

const oldRoot = Buffer.alloc(32, 11).toString("base64");
const newRoot = Buffer.alloc(32, 12).toString("base64");

function env(mode: "rewrap" | "verify"): Env {
  return {
    ...environment,
    DEPLOY_ENV: "staging",
    PUBLISHING_PAUSED: "true",
    ENCRYPTION_KEY: mode === "rewrap" ? oldRoot : newRoot,
    ENCRYPTION_KEY_NEXT: mode === "rewrap" ? newRoot : undefined,
    ENCRYPTION_KEY_VERSION: "2",
    ROOT_ROTATION_MODE: mode,
    ROOT_ROTATION_ID: "acceptance-20260912-root",
  };
}

async function inventory() {
  const store = new MemoryStore();
  const account: Account = {
    alias: "threads-main",
    provider: "threads",
    identity: { id: "threads-1", username: "proofandstate" },
    version: 1,
    secret: await seal(
      { accessToken: "threads-test-token", expiresAt: Date.now() + 86400000 },
      oldRoot,
      `${owner.workspace}:threads-main`,
      "2",
    ),
    active: true,
    verifiedAt: Date.now(),
  };
  store.put("account:threads-main", account);
  store.put("oauth:threads-main", {
    alias: "threads-main",
    provider: "threads",
    scopes: ["threads_basic", "threads_content_publish"],
    strategy: "threads_long_lived",
    accessExpiresAt: Date.now() + 86400000,
    status: "healthy",
    secret: await seal(
      { refreshToken: "refresh-test-token" },
      oldRoot,
      `${owner.workspace}:oauth:threads-main`,
      "2",
    ),
  });
  return store;
}

test("workspace rewrap is atomic, idempotent and rejects the old root", async () => {
  const store = await inventory();
  const first = await rewrapWorkspaceCredentials(store, env("rewrap"), owner.workspace);
  assert.deepEqual(first, { credentialCount: 2, changedCount: 2 });

  const account = store.get<Account>("account:threads-main")!;
  const credential = await unseal<any>(
    account.secret,
    newRoot,
    `${owner.workspace}:threads-main`,
  );
  assert.equal(credential.accessToken, "threads-test-token");
  await assert.rejects(
    unseal(account.secret, oldRoot, `${owner.workspace}:threads-main`),
  );

  const oauth = store.get<any>("oauth:threads-main")!;
  const refresh = await unseal<any>(
    oauth.secret,
    newRoot,
    `${owner.workspace}:oauth:threads-main`,
  );
  assert.equal(refresh.refreshToken, "refresh-test-token");
  await assert.rejects(
    unseal(oauth.secret, oldRoot, `${owner.workspace}:oauth:threads-main`),
  );

  const second = await rewrapWorkspaceCredentials(store, env("rewrap"), owner.workspace);
  assert.deepEqual(second, { credentialCount: 2, changedCount: 0 });
});

test("post-cutover verification reads every protected workspace credential", async () => {
  const store = await inventory();
  await rewrapWorkspaceCredentials(store, env("rewrap"), owner.workspace);
  const verified = await verifyWorkspaceCredentials(
    store,
    env("verify"),
    owner.workspace,
  );
  assert.deepEqual(verified, { credentialCount: 2 });
});

test("single-envelope rewrap tolerates interrupted retry but not unknown roots", async () => {
  const context = `${owner.workspace}:example`;
  const original = await seal({ token: "value" }, oldRoot, context, "2");
  const first = await rotateCredentialEnvelope(
    original,
    oldRoot,
    newRoot,
    context,
    "2",
  );
  assert.equal(first.changed, true);
  const retry = await rotateCredentialEnvelope(
    first.next,
    oldRoot,
    newRoot,
    context,
    "2",
  );
  assert.equal(retry.changed, false);
  await assert.rejects(
    rotateCredentialEnvelope(
      await seal({ token: "value" }, Buffer.alloc(32, 13).toString("base64"), context, "2"),
      oldRoot,
      newRoot,
      context,
      "2",
    ),
    { code: "ROOT_ROTATION_ENVELOPE_AMBIGUOUS" },
  );
});

test("rotation maintenance fails closed on invalid configuration", async () => {
  assert.equal(rootRotationMaintenance({ ...environment, ROOT_ROTATION_MODE: "typo" } as Env), true);
  const store = await inventory();
  await assert.rejects(
    rewrapWorkspaceCredentials(
      store,
      {
        ...env("rewrap"),
        PUBLISHING_PAUSED: "false",
      },
      owner.workspace,
    ),
    { code: "ROOT_ROTATION_NOT_PAUSED" },
  );
  await assert.rejects(
    rewrapWorkspaceCredentials(
      store,
      {
        ...env("rewrap"),
        ENCRYPTION_KEY_NEXT: oldRoot,
      },
      owner.workspace,
    ),
    { code: "ROOT_ROTATION_NEXT_ROOT_INVALID" },
  );
});
