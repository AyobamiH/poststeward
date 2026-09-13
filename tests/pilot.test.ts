import assert from "node:assert/strict";
import test from "node:test";
import { Pilot } from "../src/pilot.ts";
import { Engine } from "../src/engine.ts";
import { Fault } from "../src/common.ts";
import { harness, owner } from "./helpers.ts";
import type { OwnerAuthority } from "../src/owner-proof.ts";

async function fixture(provider: "x" | "threads" | "linkedin" = "x") {
  const h = harness(); await h.setup(provider);
  const authority: OwnerAuthority = { sessionHash: "private-session-hash", proof: {
    id: crypto.randomUUID(), issuer: "https://accounts.google.com", subject: owner.id, workspace: owner.workspace,
    authenticatedAt: h.now(), expiresAt: h.now() + 86400000, release: h.env.RELEASE_SHA,
  } };
  const make = () => new Pilot(h.store, h.env, new Engine(h.store, h.env, h.provider, h.options), h.provider, h.options.authorized, h.now);
  const pilot = make();
  const run = (name: string, input: unknown = {}, auth = authority, actor = owner) => pilot.run(name, input, actor, auth) as Promise<any>;
  const prepare = (text = "Exactly one controlled test.") => run("prepare", { alias: "account", text });
  const confirm = (r: any) => ({ reviewId: r.id, reviewDigest: r.reviewDigest, approve: true });
  return { ...h, authority, pilot, make, run, prepare, confirm };
}

test("pilot preview freezes fresh identity/copy and performs no provider write", async () => {
  const h = await fixture(); const s = await h.prepare("  Preserve all of this text.  ");
  assert.equal(s.record.text, "  Preserve all of this text.  ");
  assert.equal(s.record.account.identity.id, "user-1");
  assert.equal(s.record.owner.id, h.authority.proof.id);
  assert.equal(s.delivery, null); assert.equal(h.store.list("delivery:").length, 0);
  assert.equal(h.calls.publish, 0); assert.equal(h.calls.container, 0);
  assert.doesNotMatch(JSON.stringify(s), /private-session-hash|ownerSession|test-token-only|sessionHash/);
});

test("parallel confirmations, response recovery and new instances retain one controlled delivery", async () => {
  const h = await fixture(); const review = (await h.prepare()).record;
  const attempts = await Promise.allSettled([h.run("confirm", h.confirm(review)), h.run("confirm", h.confirm(review))]);
  assert.ok(attempts.some((r) => r.status === "fulfilled"));
  assert.equal(h.store.list("delivery:").length, 1);
  const first = h.pilot.status();
  const recovered: any = await h.make().run("confirm", h.confirm(review), owner, h.authority);
  assert.equal(recovered.delivery.id, first.delivery!.id);
  assert.equal(recovered.delivery.dueAt, h.now() + 30000);
  assert.equal(recovered.completed, false); assert.equal(h.calls.publish, 0);
  h.advance(30000); await h.engine.tick();
  assert.equal(h.calls.publish, 1); assert.equal(h.pilot.status().completed, false);
  const verified = await h.run("recheck"); assert.equal(verified.completed, true);
  assert.equal(verified.record.firstVerified.outcome, "EXACT_PROVIDER_READBACK");
  await h.make().run("confirm", h.confirm(review), owner, h.authority);
  await h.engine.tick(); assert.equal(h.calls.publish, 1);
  await assert.rejects(h.prepare("Different content cannot reset the slot."), { code: "PILOT_CONSUMED" });
});

test("approval does not allocate on missing consent, stale review/sign-in, tampering or tenant/agent mismatch", async () => {
  const h = await fixture(); const a = (await h.prepare()).record;
  await assert.rejects(h.run("confirm", { ...h.confirm(a), approve: false }), { code: "INVALID_INPUT" });
  await assert.rejects(h.run("prepare", { alias: "account", text: "x", owner: {} }), { code: "INVALID_INPUT" });
  await assert.rejects(h.run("status", {}, h.authority, { ...owner, grant: "agent", scopes: ["admin"] }), { code: "OWNER_SESSION_REQUIRED" });
  await assert.rejects(h.run("status", {}, { ...h.authority, proof: { ...h.authority.proof, workspace: "another" } }), { code: "OWNER_SESSION_REQUIRED" });
  const b = (await h.prepare("Replacement preview.")).record;
  await assert.rejects(h.run("confirm", h.confirm(a)), { code: "REVIEW_CHANGED" });
  h.store.put("pilot:first", { ...b, text: "Tampered copy" });
  await assert.rejects(h.run("confirm", h.confirm(b)), { code: "REVIEW_INTEGRITY" });
  h.store.put("pilot:first", b); h.advance(10 * 60000 + 1);
  await assert.rejects(h.run("confirm", h.confirm(b)), { code: "REVIEW_EXPIRED" });
  h.advance(5 * 60000); await assert.rejects(h.prepare(), { code: "FRESH_SIGNIN_REQUIRED" });
  assert.equal(h.store.list("delivery:").length, 0); assert.equal(h.calls.publish, 0);
});

test("changed account, runtime, pause and lost session block an unstarted pilot", async () => {
  for (const mode of ["account", "runtime", "pause", "session"]) {
    const h = await fixture(); const r = (await h.prepare()).record;
    if (mode === "account") await h.engine.connect(owner, { alias: "account", provider: "x", accessToken: "replacement-token", funding: "customer_app" });
    if (mode === "runtime") h.env.RELEASE_SHA = "another-revision";
    if (mode === "pause") h.store.put("paused", true);
    if (mode === "session") h.authorize(false);
    await assert.rejects(h.run("confirm", h.confirm(r)));
    assert.equal(h.store.list("delivery:").length, 0); assert.equal(h.calls.publish, 0);
  }
  const h = await fixture(); const r = (await h.prepare()).record;
  await h.run("confirm", h.confirm(r)); h.authorize(false); h.advance(30000); await h.engine.tick();
  assert.equal(h.pilot.status().delivery!.status, "drift_blocked"); assert.equal(h.calls.publish, 0);
});

test("atomic review commit rolls back the delivery on journal failure; alarm recovery cannot allocate twice", async () => {
  const h = await fixture(); const r = (await h.prepare()).record;
  const put = h.store.put.bind(h.store); let fail = true;
  h.store.put = (key, value: any) => {
    if (fail && key === "pilot:first" && value.state === "reserved") throw new Error("Simulated storage failure");
    put(key, value);
  };
  await assert.rejects(h.run("confirm", h.confirm(r)));
  assert.equal(h.store.list("delivery:").length, 0); assert.equal(h.store.list("fingerprint:").length, 0);
  assert.equal(h.pilot.status().record!.state, "prepared");
  fail = false; const wake = h.options.wake; h.options.wake = async () => { throw new Error("Simulated alarm response loss"); };
  await assert.rejects(h.run("confirm", h.confirm(r)));
  assert.equal(h.store.list("delivery:").length, 1);
  h.options.wake = wake;
  await h.make().run("confirm", h.confirm(r), owner, h.authority);
  assert.equal(h.store.list("delivery:").length, 1); assert.ok(h.alarms.length > 0);
});

test("pilot cancellation redacts internal session authority and cannot be bypassed with general publishing", async () => {
  const h = await fixture(); const r = (await h.prepare()).record;
  const reserved = await h.run("confirm", h.confirm(r));
  const cancelled = await h.run("cancel");
  assert.equal(cancelled.cancellation.cancelled, true);
  assert.doesNotMatch(JSON.stringify(cancelled), /private-session-hash|ownerSession|sessionHash/);
  await assert.rejects(h.run("confirm", { ...h.confirm(r), reviewDigest: "f".repeat(64) }), { code: "REVIEW_CHANGED" });
  await assert.rejects(h.run("prepare", { alias: "account", text: "Another" }), { code: "PILOT_CONSUMED" });
  await assert.rejects(h.engine.run("publish_now", { campaign: r.id, idempotencyKey: "bypass-pilot-001" }, owner), { code: "CONTROLLED_PUBLICATION" });
  const ordinary = await h.campaign("Ordinary approved content.");
  await assert.rejects(h.engine.run("schedule_replace", { delivery: reserved.delivery.id, campaign: ordinary.id,
    at: new Date(h.now() + 60000).toISOString(), timezone: "UTC", idempotencyKey: "bypass-replace-001" }, owner), { code: "CONTROLLED_PUBLICATION" });
  const copy = await h.campaign(r.text);
  const cloned: any = await h.engine.run("publish_now", { campaign: copy.id, idempotencyKey: "copy-pilot-001" }, owner);
  assert.equal(cloned.deliveries[0].id, reserved.delivery.id);
  h.advance(60000); await h.engine.tick(); assert.equal(h.calls.publish, 0);
});

test("missing creation evidence stays ambiguous; bounded readback never republishes", async () => {
  const h = await fixture(); const r = (await h.prepare()).record;
  await h.run("confirm", h.confirm(r));
  h.provider.publish = async () => { h.calls.publish++; throw new Fault("AMBIGUOUS_PROVIDER_WRITE", "Unknown", 502); };
  h.advance(30000); await h.engine.tick();
  assert.equal(h.pilot.status().delivery!.status, "ambiguous_effect");
  await assert.rejects(h.run("recheck"), { code: "NO_READBACK_TARGET" });
  await h.run("confirm", h.confirm(r)); await h.engine.tick(); assert.equal(h.calls.publish, 1);

  const q = await fixture(); const preview = (await q.prepare()).record; await q.run("confirm", q.confirm(preview));
  q.provider.verify = async () => { throw new Error("Do not expose credential-bearing upstream error"); };
  q.advance(30000); await q.engine.tick();
  assert.equal(q.pilot.status().delivery!.postId, "post-1");
  for (let n = 0; n < 8; n++) {
    const s = await q.run("recheck"); assert.equal(s.record.readbackAttempts, n + 1); assert.equal(s.completed, false);
    assert.doesNotMatch(JSON.stringify(s), /credential-bearing/);
    await assert.rejects(q.run("recheck"), { code: "READBACK_LIMIT" }); q.advance(30000);
  }
  await assert.rejects(q.run("recheck"), { code: "READBACK_LIMIT" }); assert.equal(q.calls.publish, 1);
});

test("fencing stops a stale dispatch after its claim is recovered during provider I/O", async () => {
  const h = await fixture(); const r = (await h.prepare()).record; await h.run("confirm", h.confirm(r));
  h.advance(30000);
  let release!: () => void, entered!: () => void;
  const started = new Promise<void>((resolve) => { entered = resolve; });
  const paused = new Promise<void>((resolve) => { release = resolve; });
  h.provider.identity = async () => { entered(); await paused; return { id: "user-1", username: "test_user" }; };
  const first = h.engine.tick(); await started; h.advance(60001);
  await h.engine.tick(); release(); await first;
  assert.equal(h.calls.publish, 0); assert.equal(h.pilot.status().delivery!.status, "failed");
});

test("late provider creation evidence survives a same-claim watchdog without another publication", async () => {
  const h = await fixture(); const r = (await h.prepare()).record; await h.run("confirm", h.confirm(r)); h.advance(30000);
  let release!: () => void, entered!: () => void;
  const started = new Promise<void>((resolve) => { entered = resolve; });
  const paused = new Promise<void>((resolve) => { release = resolve; });
  h.provider.publish = async () => { h.calls.publish++; entered(); await paused; return { id: "late-post" }; };
  const first = h.engine.tick(); await started; h.advance(60001); await h.engine.tick();
  assert.equal(h.pilot.status().delivery!.status, "ambiguous_effect");
  release(); await first; assert.equal(h.pilot.status().delivery!.postId, "late-post");
  await h.engine.tick(); assert.equal(h.calls.publish, 1);
});

test("pilot refuses unsupported readback providers and provider identity drift", async () => {
  const linkedin = await fixture("linkedin"); await assert.rejects(linkedin.prepare(), { code: "READBACK_UNSUPPORTED" });
  const h = await fixture(); h.provider.identity = async () => ({ id: "wrong-user", username: "test_user" });
  await assert.rejects(h.prepare(), { code: "ACCOUNT_DRIFT" }); assert.equal(h.calls.publish, 0);
});
