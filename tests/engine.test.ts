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
test("LinkedIn Page dispatch rechecks the bound organization actor", async () => {
  const h = harness();
  await h.setup("linkedin");
  const actorUrn = "urn:li:organization:146607525";
  const account = h.store.get<Account>("account:account")!;
  account.identity = { id: actorUrn, username: actorUrn };
  account.capabilities = { oauth: true, refresh: false, readback: true };
  h.store.put("account:account", account);
  const actors: Array<string | undefined> = [];
  h.provider.identity = async (_provider, _credential, actor) => {
    actors.push(actor);
    return actor
      ? { id: actor, username: actor }
      : { id: "urn:li:person:member-123", username: "Owner" };
  };
  const c = await h.campaign("A Page-bound dispatch.");
  await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "linkedin-page-dispatch-001",
  });

  await h.engine.tick();

  assert.deepEqual(actors, [actorUrn]);
  assert.equal(h.calls.publish, 1);
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

test("ordinary receipt readback recovers verification without a second publication", async () => {
  const h = harness();
  await h.setup();
  h.provider.verify = async () => ({ verified: false });
  const c = await h.campaign();
  const reservation = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "readback-publish",
  });
  await h.engine.tick();
  const id = reservation.deliveries[0].id;
  assert.equal(
    (await h.run("receipt_get", { delivery: id })).status,
    "published_unverified",
  );
  h.provider.verify = async (d) => {
    assert.equal(d.postId, "post-1");
    return { verified: true };
  };
  const receipt = await h.run("receipt_recheck", {
    delivery: id,
    idempotencyKey: "readback-recover",
  });
  assert.equal(receipt.status, "published_verified");
  assert.equal(receipt.readbackAttempts, 1);
  await h.run("receipt_recheck", {
    delivery: id,
    idempotencyKey: "readback-recover",
  });
  assert.equal(h.calls.publish, 1);
});

test("ordinary readback bounds failures and refuses targets without a known post", async () => {
  const h = harness();
  await h.setup();
  h.provider.verify = async () => {
    throw new Error("provider timeout");
  };
  const c = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "bounded-publish",
  });
  const id = r.deliveries[0].id;
  await assert.rejects(
    h.run("receipt_recheck", {
      delivery: id,
      idempotencyKey: "no-target-check",
    }),
    { code: "NO_READBACK_TARGET" },
  );
  await h.engine.tick();
  for (let n = 0; n < 8; n++) {
    const receipt = await h.run("receipt_recheck", {
      delivery: id,
      idempotencyKey: `bounded-check-${n}`,
    });
    assert.equal(receipt.status, "published_unverified");
    assert.equal(receipt.readbackAttempts, n + 1);
    await assert.rejects(
      h.run("receipt_recheck", {
        delivery: id,
        idempotencyKey: `too-early-check-${n}`,
      }),
      { code: "READBACK_LIMIT" },
    );
    h.advance(60000);
  }
  await assert.rejects(
    h.run("receipt_recheck", {
      delivery: id,
      idempotencyKey: "exhausted-check",
    }),
    { code: "READBACK_LIMIT" },
  );
  assert.equal(h.calls.publish, 1);
});

test("late readback cannot claim verification after owner disconnect", async () => {
  const h = harness();
  await h.setup();
  h.provider.verify = async () => ({ verified: false });
  const c = await h.campaign();
  const r = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "drift-read-publish",
  });
  await h.engine.tick();
  let started!: () => void;
  let finish!: (v: { verified: boolean }) => void;
  const entered = new Promise<void>((resolve) => {
    started = resolve;
  });
  const response = new Promise<{ verified: boolean }>((resolve) => {
    finish = resolve;
  });
  h.provider.verify = async () => {
    started();
    return response;
  };
  const id = r.deliveries[0].id;
  const pending = h.run("receipt_recheck", {
    delivery: id,
    idempotencyKey: "drift-read-check",
  });
  const rejected = assert.rejects(pending, { code: "CONNECTION_INACTIVE" });
  await entered;
  await h.run("account_disconnect", {
    alias: "account",
    idempotencyKey: "drift-disconnect",
  });
  finish({ verified: true });
  await rejected;
  assert.equal(
    (await h.run("receipt_get", { delivery: id })).status,
    "published_unverified",
  );
  assert.equal(h.calls.publish, 1);
});

test("every agent-created delivery waits for an owner approval and rechecks the grant", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const agent = {
    ...owner,
    id: "agent",
    grant: "grant-hash",
    scopes: ["publish"] as const,
  };
  const reservation = await h.run(
    "publish_now",
    { campaign: c.id, idempotencyKey: "agent-publish-001" },
    agent as any,
  );
  const id = reservation.deliveries[0].id;
  assert.equal(reservation.deliveries[0].status, "pending_approval");
  await h.engine.tick();
  assert.equal(h.calls.publish, 0);

  const approved = await h.run("delivery_approve", {
    delivery: id,
    idempotencyKey: "owner-approve-001",
  });
  assert.equal(approved.status, "scheduled");
  assert.equal(approved.approval.status, "approved");
  assert.ok(!("actor" in approved));

  h.authorize(false);
  await h.engine.tick();
  assert.equal(h.calls.publish, 0);
  assert.equal(
    (await h.run("receipt_get", { delivery: id })).status,
    "drift_blocked",
  );
});

test("an owner can reject an agent delivery before any provider effect", async () => {
  const h = harness();
  await h.setup();
  const c = await h.campaign();
  const agent = {
    ...owner,
    id: "agent",
    grant: "grant-hash",
    scopes: ["schedule"] as const,
  };
  const reservation = await h.run(
    "schedule_create",
    {
      campaign: c.id,
      at: new Date(h.now() + 60000).toISOString(),
      timezone: "UTC",
      idempotencyKey: "agent-schedule-001",
    },
    agent as any,
  );
  const id = reservation.deliveries[0].id;
  const rejected = await h.run("delivery_reject", {
    delivery: id,
    idempotencyKey: "owner-reject-001",
  });
  assert.equal(rejected.status, "cancelled");
  assert.equal(rejected.approval.status, "rejected");
  h.advance(60000);
  await h.engine.tick();
  assert.equal(h.calls.publish, 0);
});

test("X multipart delivery records and verifies each reply-chain part", async () => {
  const h = harness();
  await h.setup("x");
  const calls: Array<{ text: string; replyToId?: string }> = [];
  h.provider.publish = async (_delivery, _credential, context) => {
    calls.push({ ...context! });
    return {
      id: `post-${calls.length}`,
      url: `https://x.com/i/web/status/post-${calls.length}`,
    };
  };
  h.provider.verify = async () => ({ verified: true });
  const c = await h.campaign("A complete reviewed sentence. ".repeat(35));
  assert.equal(c.publications.account.type, "thread");
  const reservation = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "x-thread-001",
  });
  const id = reservation.deliveries[0].id;
  for (let index = 0; index < c.publications.account.parts.length; index++)
    await h.engine.tick();

  const receipt = await h.run("receipt_get", { delivery: id });
  assert.equal(receipt.status, "published_verified");
  assert.equal(receipt.partIds.length, c.publications.account.parts.length);
  assert.equal(receipt.verifiedParts, c.publications.account.parts.length);
  assert.equal(calls[0].replyToId, undefined);
  for (let index = 1; index < calls.length; index++)
    assert.equal(calls[index].replyToId, `post-${index}`);
});

test("a rejected later X thread part is a durable partial effect and never replays", async () => {
  const h = harness();
  await h.setup("x");
  let writes = 0;
  h.provider.publish = async () => {
    writes++;
    if (writes === 2)
      throw new Fault("PROVIDER_HTTP_403", "provider rejected", 502);
    return { id: "post-1", url: "https://x.com/i/web/status/post-1" };
  };
  const c = await h.campaign("A complete reviewed sentence. ".repeat(35));
  const reservation = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "x-thread-partial-001",
  });
  const id = reservation.deliveries[0].id;
  await h.engine.tick();
  await h.engine.tick();
  await h.engine.tick();
  const receipt = await h.run("receipt_get", { delivery: id });
  assert.equal(receipt.status, "partial_effect");
  assert.deepEqual(receipt.partIds, ["post-1"]);
  assert.equal(receipt.failedPartIndex, 2);
  assert.equal(writes, 2);
});

test("Threads exposes waiting_container for each chained part before publishing", async () => {
  const h = harness();
  await h.setup("threads");
  const containers: Array<{ text: string; replyToId?: string }> = [];
  const writes: Array<{ text: string; replyToId?: string }> = [];
  h.provider.createContainer = async (_delivery, _credential, context) => {
    containers.push({ ...context! });
    return `container-${containers.length}`;
  };
  h.provider.containerStatus = async () => "FINISHED";
  h.provider.publish = async (_delivery, _credential, context) => {
    writes.push({ ...context! });
    return { id: `thread-${writes.length}` };
  };
  h.provider.verify = async () => ({ verified: true });
  const c = await h.campaign("A complete reviewed sentence. ".repeat(25));
  assert.equal(c.publications.account.type, "thread");
  const reservation = await h.run("publish_now", {
    campaign: c.id,
    idempotencyKey: "threads-chain-001",
  });
  const id = reservation.deliveries[0].id;

  for (let index = 0; index < c.publications.account.parts.length; index++) {
    await h.engine.tick();
    assert.equal(
      (await h.run("receipt_get", { delivery: id })).status,
      "waiting_container",
    );
    h.advance(30000);
    await h.engine.tick();
  }
  const receipt = await h.run("receipt_get", { delivery: id });
  assert.equal(receipt.status, "published_verified");
  assert.equal(containers[0].replyToId, undefined);
  for (let index = 1; index < containers.length; index++)
    assert.equal(containers[index].replyToId, `thread-${index}`);
});
