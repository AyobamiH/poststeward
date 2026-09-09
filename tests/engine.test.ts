import { test } from "node:test";
import assert from "node:assert/strict";
import { Engine } from "../src/engine.ts";
import { Fault, explicitTime, addMonth } from "../src/common.ts";
import { catalog } from "../src/operations/catalog.ts";
import { harness, owner } from "./helpers.ts";
import type { Account, Delivery } from "../src/types.ts";

test("catalogue matches real handlers and every documented example validates", () => {
  const h = harness();
  assert.deepEqual(
    catalog.map((o) => o.name).sort(),
    Object.keys(h.engine.handlers).sort(),
  );
  for (const o of catalog)
    assert.ok(o.schema.safeParse(o.example).success, o.name);
});
test("Free goes from verified account to actual provider receipt without billing", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const reservation = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "publish-001",
  });
  assert.equal(reservation.deliveries[0].status, "scheduled");
  assert.equal(h.calls.publish, 0);
  await h.engine.tick();
  const receipt = await h.run("receipt_get", {
    delivery: reservation.deliveries[0].id,
  });
  assert.equal(receipt.status, "published_verified");
  assert.equal(receipt.postId, "post-1");
  assert.equal(h.calls.publish, 1);
  assert.ok(!("actor" in receipt));
});
test("concurrent transports and fresh keys cannot duplicate approved content", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  await Promise.all([
    h.run("publish_now", { campaign: c.id, idempotencyKey: "same-key-001" }),
    h.run("publish_now", { campaign: c.id, idempotencyKey: "same-key-001" }),
  ]);
  await h.engine.tick();
  const c2 = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c2.id,
    idempotencyKey: "different-key",
  });
  assert.equal(r.reused, 1);
  await h.engine.tick();
  assert.equal(h.calls.publish, 1);
});
test("same idempotency key with changed inputs conflicts", async () => {
  const h = harness();
  await h.setup();
  await h.run("publishing_pause", {
    paused: true,
    idempotencyKey: "pause-001",
  });
  await assert.rejects(
    h.run("publishing_pause", { paused: false, idempotencyKey: "pause-001" }),
    { code: "IDEMPOTENCY_CONFLICT" },
  );
});
test("explicit future job survives a fresh Engine instance and client disconnect", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  await h.run("schedule_create", {
    campaign: c.id,
    at: new Date(h.now() + 60000).toISOString(),
    timezone: "UTC",
    idempotencyKey: "schedule-001",
  });
  await h.engine.tick();
  assert.equal(h.calls.publish, 0);
  h.advance(60000);
  const restart = new Engine(h.store, h.env, h.provider, h.options);
  await restart.tick();
  assert.equal(h.calls.publish, 1);
});
test("cancellation before a due claim prevents provider effects", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "publish-001",
  });
  assert.equal(
    (
      await h.run("schedule_cancel", {
        delivery: r.deliveries[0].id,
        idempotencyKey: "cancel-001",
      })
    ).cancelled,
    true,
  );
  await h.engine.tick();
  assert.equal(h.calls.publish, 0);
});
test("cancellation honestly loses once a dispatch owns the claim", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "publish-001",
  });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  h.provider.identity = async () => {
    await gate;
    return { id: "user-1", username: "test_user" };
  };
  const tick = h.engine.tick();
  await new Promise((resolve) => setTimeout(resolve, 5));
  const cancellation = await h.run("schedule_cancel", {
    delivery: r.deliveries[0].id,
    idempotencyKey: "cancel-001",
  });
  assert.equal(cancellation.cancelled, false);
  assert.equal(cancellation.reason, "already_executing");
  release();
  await tick;
  assert.equal(h.calls.publish, 1);
});
test("account reconnection cannot redirect captured schedules", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "publish-001",
  });
  await h.engine.connect(owner, {
    alias: "account",
    provider: "x",
    accessToken: "a-new-test-token",
    funding: "customer_app",
  });
  await h.engine.tick();
  assert.equal(
    (await h.run("receipt_get", { delivery: r.deliveries[0].id })).status,
    "drift_blocked",
  );
  assert.equal(h.calls.publish, 0);
});
test("revoked grants prevent later scheduled dispatch", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  await h.run("publish_now", { campaign: c.id, idempotencyKey: "publish-001" });
  h.authorize(false);
  await h.engine.tick();
  assert.equal(h.calls.publish, 0);
  assert.equal(h.store.list<Delivery>("delivery:")[0].status, "drift_blocked");
});
test("read-only agent cannot publish or purchase", async () => {
  const h = harness();
  const reader = { ...owner, scopes: ["read"] as const };
  await assert.rejects(
    h.engine.run(
      "publish_now",
      { campaign: "id", idempotencyKey: "publish-001" },
      reader as any,
    ),
    { code: "INSUFFICIENT_SCOPE" },
  );
  await assert.rejects(
    h.engine.run(
      "billing_quote",
      { mode: "subscription", idempotencyKey: "billing-001" },
      reader as any,
    ),
    { code: "INSUFFICIENT_SCOPE" },
  );
});
test("provider timeout becomes ambiguous and is never blindly repeated", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "publish-001",
  });
  h.provider.publish = async () => {
    h.calls.publish++;
    throw new Fault("AMBIGUOUS_PROVIDER_WRITE", "timeout", 502);
  };
  await h.engine.tick();
  await h.engine.tick();
  await h.run("publish_now", { campaign: c.id, idempotencyKey: "another-key" });
  await h.engine.tick();
  assert.equal(h.calls.publish, 1);
  assert.equal(
    (await h.run("receipt_get", { delivery: r.deliveries[0].id })).status,
    "ambiguous_effect",
  );
});
test("interrupted write claim is recovered as uncertainty", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "publish-001",
  });
  const d = h.store.get<Delivery>("delivery:" + r.deliveries[0].id)!;
  Object.assign(d, {
    status: "executing",
    phase: "publish",
    claimUntil: h.now() - 1,
  });
  h.store.put("delivery:" + d.id, d);
  await h.engine.tick();
  assert.equal(
    h.store.get<Delivery>("delivery:" + d.id)?.status,
    "ambiguous_effect",
  );
  assert.equal(h.calls.publish, 0);
});
test("creation ID remains durable when readback fails", async () => {
  const h = harness();
  await h.setup("linkedin");
  h.provider.verify = async () => {
    throw new Error("permission denied");
  };
  const c = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "publish-001",
  });
  await h.engine.tick();
  const d = await h.run("receipt_get", { delivery: r.deliveries[0].id });
  assert.equal(d.status, "published_unverified");
  assert.equal(d.postId, "post-1");
});
test("Threads waits through delayed visibility and only publishes a FINISHED container", async () => {
  const h = harness();
  await h.setup("threads");
  const statuses = ["NOT_VISIBLE", "IN_PROGRESS", "FINISHED"];
  h.provider.containerStatus = async () => statuses.shift()!;
  const c = await h.campaign();
  await h.run("publish_now", { campaign: c.id, idempotencyKey: "publish-001" });
  await h.engine.tick();
  assert.equal(h.calls.container, 1);
  assert.equal(h.calls.publish, 0);
  for (let i = 0; i < 2; i++) {
    h.advance(30000);
    await h.engine.tick();
    assert.equal(h.calls.publish, 0);
  }
  h.advance(30000);
  await h.engine.tick();
  assert.equal(h.calls.publish, 1);
});
for (const status of ["ERROR", "EXPIRED", "PUBLISHED"])
  test(`Threads ${status} blocks a fresh publication`, async () => {
    const h = harness();
    await h.setup("threads");
    h.provider.containerStatus = async () => status;
    const c = await h.campaign();
    await h.run("publish_now", {
      campaign: c.id,
      idempotencyKey: "publish-001",
    });
    await h.engine.tick();
    h.advance(30000);
    await h.engine.tick();
    assert.equal(h.calls.publish, 0);
    assert.equal(
      h.store.list<Delivery>("delivery:")[0].status,
      status === "PUBLISHED" ? "ambiguous_effect" : "failed",
    );
  });
test("failed replacement is atomic and preserves original reservation", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "publish-001",
  });
  await assert.rejects(
    h.run("schedule_replace", {
      delivery: r.deliveries[0].id,
      campaign: "missing",
      at: new Date(h.now() + 60000).toISOString(),
      idempotencyKey: "replace-001",
    }),
    { code: "NOT_FOUND" },
  );
  assert.equal(h.store.list<Delivery>("delivery:")[0].status, "scheduled");
});
test("rescheduling cancelled exact content preserves the old receipt and creates a new reservation", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const first = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "publish-001",
  });
  await h.run("schedule_cancel", {
    delivery: first.deliveries[0].id,
    idempotencyKey: "cancel-001",
  });
  const second = await h.run("schedule_create", {
    campaign: c.id,
    at: new Date(h.now() + 60000).toISOString(),
    idempotencyKey: "schedule-002",
  });
  assert.notEqual(first.deliveries[0].id, second.deliveries[0].id);
  assert.equal(
    (await h.run("receipt_get", { delivery: first.deliveries[0].id })).status,
    "cancelled",
  );
  assert.equal(second.deliveries[0].status, "scheduled");
});
test("workspace storage and encrypted connection secrets are isolated", async () => {
  const h = harness(),
    other = harness();
  await h.setup();
  const c = await h.campaign();
  await assert.rejects(other.run("campaign_get", { campaign: c.id }), {
    code: "NOT_FOUND",
  });
  const listed = await h.run("accounts_list");
  assert.ok(!JSON.stringify(listed).includes("test-token"));
  assert.ok(!("secret" in listed[0]));
  const account = h.store.get<Account>("account:account")!;
  assert.ok(!account.secret.includes("test-token"));
});
test("daily reservations are bounded atomically across multiple destinations", async () => {
  const h = harness();
  h.env.DAILY_DELIVERY_LIMIT = "1";
  await h.setup();
  await h.engine.connect(owner, {
    alias: "second",
    provider: "threads",
    accessToken: "test-token-2",
  });
  await h.run("project_put", {
    id: "project",
    name: "Project",
    accounts: ["account", "second"],
    idempotencyKey: "project-two",
  });
  const c = await h.campaign("Approved", ["account", "second"]);
  await assert.rejects(
    h.run("publish_now", { campaign: c.id, idempotencyKey: "publish-001" }),
    { code: "DELIVERY_LIMIT" },
  );
  assert.equal(h.store.list("delivery:").length, 0);
});
test("billing expiry stops automation and preserves manual schedules and history", async () => {
  const h = harness();
  await h.setup();
  h.env.ADVANCED_ENABLED = "true";
  h.store.put("entitlement", {
    kind: "pass",
    until: h.now() + 1,
    reference: "pi_test",
  });
  const profile = {
    id: "profile",
    project: "project",
    repository: "owner/product",
    branch: "main",
    path: "README.md",
    templates: { account: "Source update {commit}" },
    family: "development",
    intervalMinutes: 60,
    minSpacingMinutes: 60,
    idempotencyKey: "profile-001",
  };
  await h.run("automation_configure", profile);
  await h.run("automation_enable", {
    id: "profile",
    idempotencyKey: "enable-001",
  });
  const c = await h.campaign();
  await h.run("publish_now", { campaign: c.id, idempotencyKey: "manual-001" });
  h.advance(2);
  await h.engine.tick();
  assert.equal(h.calls.publish, 1);
  assert.equal((await h.run("automation_inspect")).profiles[0].enabled, false);
  assert.equal((await h.run("workspace_status")).plan, "free");
});
test("source baseline produces no backfill; later change replenishes reviewed inventory", async () => {
  const h = harness();
  await h.setup();
  h.env.ADVANCED_ENABLED = "true";
  h.store.put("entitlement", {
    kind: "pass",
    until: h.now() + 86400000,
    reference: "pi_test",
  });
  await h.run("automation_configure", {
    id: "profile",
    project: "project",
    repository: "owner/product",
    branch: "main",
    path: "README.md",
    templates: { account: "Development update {commit}" },
    family: "development",
    intervalMinutes: 60,
    minSpacingMinutes: 60,
    idempotencyKey: "profile-001",
  });
  await h.run("automation_enable", {
    id: "profile",
    idempotencyKey: "enable-001",
  });
  await h.engine.tick();
  assert.equal(h.calls.publish, 0);
  h.source("b".repeat(40));
  h.advance(15 * 60000);
  await h.engine.tick();
  assert.equal(h.calls.publish, 1);
  assert.match(h.store.list<Delivery>("delivery:")[0].text, /b{40}/);
});
test("paid features require payment, while automation inspection and stopping remain free", async () => {
  const h = harness();
  await assert.rejects(
    h.run("automation_enable", { id: "profile", idempotencyKey: "enable-001" }),
    { code: "ADVANCED_REQUIRED" },
  );
  assert.deepEqual(await h.run("automation_inspect"), {
    profiles: [],
    deliveries: [],
  });
});
test("explicit offsets and calendar month ends are preserved", () => {
  assert.throws(() => explicitTime("2026-11-01T01:30:00"), {
    code: "EXPLICIT_OFFSET_REQUIRED",
  });
  assert.equal(
    explicitTime("2026-11-01T01:30:00-04:00"),
    Date.parse("2026-11-01T05:30:00Z"),
  );
  assert.equal(
    new Date(addMonth(Date.parse("2027-01-31T12:00:00Z"))).toISOString(),
    "2027-02-28T12:00:00.000Z",
  );
});
