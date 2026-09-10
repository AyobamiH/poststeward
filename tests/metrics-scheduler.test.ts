import assert from "node:assert/strict";
import test from "node:test";
import { harness } from "./helpers.ts";
import type { Delivery, Profile } from "../src/types.ts";

async function profileWithPublishedDelivery() {
  const h = harness();
  await h.setup();

  const campaign = await h.campaign("Metrics candidate");
  const reservation = await h.run("publish_now", {
    campaign: campaign.id,
    idempotencyKey: "metrics-seed-publication",
  });
  await h.engine.tick();
  const id = reservation.deliveries[0].id;
  const published = h.store.get<Delivery>("delivery:" + id)!;
  assert.equal(published.status, "published_verified");
  assert.ok(published.postId);

  h.env.ADVANCED_ENABLED = "true";
  h.store.put("entitlement", {
    kind: "pass",
    until: h.now() + 10 * 86400000,
    reference: "metrics-scheduler-test",
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
    idempotencyKey: "metrics-profile-configure",
  });
  await h.run("automation_enable", {
    id: "profile",
    idempotencyKey: "metrics-profile-enable",
  });

  const profile = h.store.get<Profile>("profile:profile")!;
  published.automatic = true;
  published.policy = profile.id;
  published.policyVersion = profile.revision;
  h.store.put("delivery:" + published.id, published);
  return { h, profile, published };
}

test("scheduled metrics wake and capture independently of the source polling clock", async () => {
  const { h, profile } = await profileWithPublishedDelivery();
  profile.nextRun = h.now() + 7 * 86400000;
  profile.nextMetrics = h.now();
  h.store.put("profile:" + profile.id, profile);

  h.alarms.length = 0;
  await h.engine.scheduleNext();
  assert.equal(h.alarms.at(-1), h.now() + 1000);

  h.alarms.length = 0;
  await h.engine.tick();
  assert.equal(h.calls.metrics, 1);
  const updated = h.store.get<Profile>("profile:profile")!;
  assert.equal(updated.error, undefined);
  assert.equal(updated.metricsError, undefined);
  assert.equal(updated.lastMetricsAttempt, h.now());
  assert.equal(updated.lastMetricsSuccess, h.now());
  assert.equal(updated.nextMetrics, h.now() + 86400000);
  assert.equal(updated.nextRun, profile.nextRun);
  assert.equal(h.alarms.at(-1), updated.nextMetrics);
});

test("source failure does not starve a due metrics cycle or overwrite metrics health", async () => {
  const { h, profile } = await profileWithPublishedDelivery();
  profile.nextRun = h.now();
  profile.nextMetrics = h.now();
  h.store.put("profile:" + profile.id, profile);
  h.options.source = async () => {
    throw new Error("source unavailable");
  };

  await h.engine.tick();
  const updated = h.store.get<Profile>("profile:profile")!;
  assert.equal(h.calls.metrics, 1);
  assert.equal(updated.error, "SOURCE_UNAVAILABLE");
  assert.equal(updated.metricsError, undefined);
  assert.equal(updated.lastMetricsSuccess, h.now());
  assert.equal(updated.nextRun, h.now() + 15 * 60000);
  assert.equal(updated.nextMetrics, h.now() + 86400000);
});

test("scheduled metrics isolate per-post failures and retry the profile in fifteen minutes", async () => {
  const { h, profile, published } = await profileWithPublishedDelivery();
  const second: Delivery = {
    ...structuredClone(published),
    id: "metrics-second-delivery",
    fingerprint: "metrics-second-fingerprint",
    postId: "metrics-second-post",
    dueAt: published.dueAt + 1,
    metrics: undefined,
  };
  h.store.put("delivery:" + second.id, second);
  profile.nextRun = h.now() + 7 * 86400000;
  profile.nextMetrics = h.now();
  h.store.put("profile:" + profile.id, profile);

  let metricCalls = 0;
  h.provider.metrics = async () => {
    metricCalls++;
    if (metricCalls === 1) throw new Error("one provider read failed");
    return { availability: "available", values: { likes: 9 } };
  };

  await h.engine.tick();
  const updated = h.store.get<Profile>("profile:profile")!;
  assert.equal(metricCalls, 2);
  assert.equal(updated.error, undefined);
  assert.equal(updated.metricsError, "METRICS_UNAVAILABLE");
  assert.equal(updated.lastMetricsAttempt, h.now());
  assert.equal(updated.lastMetricsSuccess, undefined);
  assert.equal(updated.nextMetrics, h.now() + 15 * 60000);
  assert.ok(h.store.get<Delivery>("delivery:" + published.id)?.metrics);
  assert.equal(h.store.get<Delivery>("delivery:" + second.id)?.metrics, undefined);
});
