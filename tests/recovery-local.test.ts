import assert from "node:assert/strict";
import test from "node:test";
import { unseal } from "../src/crypto.ts";
import type { Credential } from "../src/providers.ts";
import { invalidateRestoredAuthority } from "../src/recovery-local.ts";
import { environment, MemoryStore, owner } from "./helpers.ts";
import type { Account, Delivery, Profile } from "../src/types.ts";

function restoredFixture() {
  const store = new MemoryStore();
  store.put("workspace", owner.workspace);
  const account: Account = {
    alias: "social",
    provider: "x",
    identity: { id: "provider-user", username: "owner" },
    version: 7,
    secret: "encrypted-restored-secret-must-be-overwritten",
    active: true,
    verifiedAt: 10,
  };
  const profile: Profile = {
    id: "release",
    revision: 3,
    project: "product",
    repository: "owner/product",
    branch: "main",
    path: "README.md",
    templates: { social: "Reviewed release {commit}" },
    family: "development",
    intervalMinutes: 60,
    minSpacingMinutes: 60,
    enabled: true,
    nextRun: 20,
    nextMetrics: 30,
    authority: owner,
  };
  const delivery: Delivery = {
    id: "delivery",
    fingerprint: "f".repeat(64),
    campaign: "campaign",
    project: "product",
    account: "social",
    provider: "x",
    identity: account.identity,
    binding: 7,
    text: "Reviewed copy",
    digest: "d".repeat(64),
    dueAt: 100,
    timezone: "UTC",
    status: "scheduled",
    createdAt: 1,
    updatedAt: 1,
    actor: owner,
    automatic: false,
  };
  store.put("account:social", account);
  store.put("oauth:social", { strategy: "refresh_token", secret: "restored-refresh" });
  store.put("profile:release", profile);
  store.put("delivery:delivery", delivery);
  store.put("quote:quote-1", { id: "quote-1", amount: 500 });
  store.put("entitlement", { kind: "subscription", until: Date.now() + 86400000, reference: "restored" });
  store.put("billing:attempt", { quote: "quote-1", status: "pending" });
  store.put("billing:customer", "cus_restored");
  store.put("billing:renewing", true);
  store.put("billing:next", Date.now() + 3600000);
  return store;
}

test("restored provider ciphertext, automation, schedules and billing authority are invalidated while content history is preserved", async () => {
  const store = restoredFixture();
  const oldSecret = store.get<Account>("account:social")!.secret;
  const result = await invalidateRestoredAuthority(
    store,
    environment,
    owner.workspace,
    12345,
  );
  const account = store.get<Account>("account:social")!;
  assert.equal(account.active, false);
  assert.equal(account.version, 8);
  assert.equal(account.verifiedAt, 12345);
  assert.notEqual(account.secret, oldSecret);
  assert.doesNotMatch(account.secret, /encrypted-restored-secret/);
  const tombstone = await unseal<Credential>(
    account.secret,
    environment.ENCRYPTION_KEY,
    owner.workspace + ":social",
  );
  assert.deepEqual(tombstone, {
    accessToken: "recovery-invalidated",
    expiresAt: 0,
  });
  assert.equal(store.get("oauth:social"), undefined);
  const profile = store.get<Profile>("profile:release")!;
  assert.equal(profile.enabled, false);
  assert.equal(profile.revision, 4);
  assert.equal(profile.error, "RECOVERY_REAUTHORIZATION_REQUIRED");
  const delivery = store.get<Delivery>("delivery:delivery")!;
  assert.equal(delivery.status, "drift_blocked");
  assert.match(delivery.reason || "", /recovery invalidated/i);
  assert.equal(store.get("quote:quote-1"), undefined);
  for (const key of ["entitlement", "billing:attempt", "billing:customer", "billing:renewing", "billing:next"])
    assert.equal(store.get(key), undefined);
  assert.deepEqual(result.accounts, ["social"]);
  assert.deepEqual(result.profiles, ["release"]);
  assert.deepEqual(result.deliveries, ["delivery"]);
  assert.deepEqual(result.quotes, ["quote-1"]);
  assert.equal(result.billingReset, true);
  assert.equal(store.get("workspace"), owner.workspace);
});

test("restored authority invalidation is exactly-once within one restored snapshot", async () => {
  const store = restoredFixture();
  await invalidateRestoredAuthority(store, environment, owner.workspace, 100);
  const account = store.get<Account>("account:social")!;
  const version = account.version;
  const secret = account.secret;
  const revision = store.get<Profile>("profile:release")!.revision;
  const second = await invalidateRestoredAuthority(
    store,
    environment,
    owner.workspace,
    200,
  );
  assert.equal(store.get<Account>("account:social")!.version, version);
  assert.equal(store.get<Account>("account:social")!.secret, secret);
  assert.equal(store.get<Profile>("profile:release")!.revision, revision);
  assert.deepEqual(second.accounts, []);
  assert.deepEqual(second.profiles, []);
  assert.deepEqual(second.deliveries, []);
  assert.equal(second.billingReset, false);
});
