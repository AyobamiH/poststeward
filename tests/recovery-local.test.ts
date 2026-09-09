import assert from "node:assert/strict";
import test from "node:test";
import { invalidateRestoredAuthority } from "../src/recovery-local.ts";
import { MemoryStore, owner } from "./helpers.ts";
import type { Account, Delivery, Profile } from "../src/types.ts";

function restoredFixture() {
  const store = new MemoryStore();
  store.put("workspace", owner.workspace);
  const account: Account = {
    alias: "social",
    provider: "x",
    identity: { id: "provider-user", username: "owner" },
    version: 7,
    secret: "encrypted-restored-secret",
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

test("restored provider, automation, schedule and billing authority are invalidated while content history is preserved", () => {
  const store = restoredFixture();
  const result = invalidateRestoredAuthority(store, 12345);
  const account = store.get<Account>("account:social")!;
  assert.equal(account.active, false);
  assert.equal(account.version, 8);
  assert.equal(account.verifiedAt, 12345);
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
  assert.equal(store.get("workspace"), owner.workspace);
});

test("restored authority invalidation is idempotent and does not repeatedly bump already inactive bindings", () => {
  const store = restoredFixture();
  invalidateRestoredAuthority(store, 100);
  const version = store.get<Account>("account:social")!.version;
  const revision = store.get<Profile>("profile:release")!.revision;
  const second = invalidateRestoredAuthority(store, 200);
  assert.equal(store.get<Account>("account:social")!.version, version);
  assert.equal(store.get<Profile>("profile:release")!.revision, revision);
  assert.deepEqual(second.accounts, []);
  assert.deepEqual(second.profiles, []);
  assert.deepEqual(second.deliveries, []);
});
