import assert from "node:assert/strict";
import test from "node:test";
import { createHmac } from "node:crypto";
import { setWorkspaceQuarantine } from "../src/effects.ts";
import { runtime } from "./runtime-fixture.ts";

const origin = "https://publish.example";
const workspace = "00000000-0000-4000-8000-000000000091";
const webhookSecret = "whsec_recovery_boundary_test_only";

function signedEvent(id: string) {
  const timestamp = Math.floor(Date.now() / 1000);
  const payload = JSON.stringify({
    id,
    object: "event",
    created: timestamp,
    data: {
      object: {
        id: "sub_recovery_test",
        object: "subscription",
        metadata: { workspace },
      },
    },
    livemode: false,
    pending_webhooks: 1,
    request: { id: null, idempotency_key: null },
    type: "customer.subscription.updated",
  });
  const v1 = createHmac("sha256", webhookSecret)
    .update(`${timestamp}.${payload}`)
    .digest("hex");
  return { payload, signature: `t=${timestamp},v1=${v1}` };
}

async function sendWebhook(
  mf: Awaited<ReturnType<typeof runtime>>["mf"],
  id: string,
) {
  const event = signedEvent(id);
  return mf.dispatchFetch(`${origin}/webhooks/stripe`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Stripe-Signature": event.signature,
    },
    body: event.payload,
  });
}

test("signed Stripe webhook reaches actorless internal reconciliation and is acknowledged only after it completes", async () => {
  const { mf, db } = await runtime(undefined, {
    ADVANCED_ENABLED: "true",
    STRIPE_SECRET_KEY: "sk_test_recovery_only",
    STRIPE_PRICE_ID: "price_recovery_only",
    STRIPE_WEBHOOK_SECRET: webhookSecret,
  });
  try {
    const response = await sendWebhook(mf, "evt_recovery_unquarantined");
    assert.equal(response.status, 200, await response.clone().text());
    const row = await db
      .prepare("SELECT completed_at FROM stripe_events WHERE id=?")
      .bind("evt_recovery_unquarantined")
      .first<{ completed_at?: number }>();
    assert.equal(typeof row?.completed_at, "number");
  } finally {
    await mf.dispose();
  }
});

test("recovery quarantine leaves a signed Stripe event incomplete and retryable until quarantine clears", async () => {
  const { mf, db } = await runtime(undefined, {
    ADVANCED_ENABLED: "true",
    STRIPE_SECRET_KEY: "sk_test_recovery_only",
    STRIPE_PRICE_ID: "price_recovery_only",
    STRIPE_WEBHOOK_SECRET: webhookSecret,
  });
  try {
    await setWorkspaceQuarantine(db, workspace, true, "recovery webhook test");
    const blocked = await sendWebhook(mf, "evt_recovery_retry");
    assert.equal(blocked.status, 503, await blocked.clone().text());
    let row = await db
      .prepare("SELECT completed_at FROM stripe_events WHERE id=?")
      .bind("evt_recovery_retry")
      .first<{ completed_at?: number }>();
    assert.equal(row?.completed_at == null, true);

    await setWorkspaceQuarantine(db, workspace, false, "recovery reconciled");
    const retried = await sendWebhook(mf, "evt_recovery_retry");
    assert.equal(retried.status, 200, await retried.clone().text());
    row = await db
      .prepare("SELECT completed_at FROM stripe_events WHERE id=?")
      .bind("evt_recovery_retry")
      .first<{ completed_at?: number }>();
    assert.equal(typeof row?.completed_at, "number");
  } finally {
    await mf.dispose();
  }
});
