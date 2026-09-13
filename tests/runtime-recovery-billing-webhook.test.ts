import assert from "node:assert/strict";
import test from "node:test";
import { createHmac } from "node:crypto";
import { Response as RuntimeResponse } from "miniflare";
import { setWorkspaceQuarantine } from "../src/effects.ts";
import { runtime } from "./runtime-fixture.ts";

const origin = "https://publish.example";
const workspace = "00000000-0000-4000-8000-000000000091";
const webhookSecret = "whsec_recovery_boundary_test_only";

function signedEvent(id: string, eventObject?: Record<string, unknown>, eventType = "customer.subscription.updated") {
  const timestamp = Math.floor(Date.now() / 1000);
  const payload = JSON.stringify({
    id,
    object: "event",
    created: timestamp,
    data: {
      object: eventObject || {
        id: "sub_recovery_test",
        object: "subscription",
        metadata: { workspace },
      },
    },
    livemode: false,
    pending_webhooks: 1,
    request: { id: null, idempotency_key: null },
    type: eventType,
  });
  const v1 = createHmac("sha256", webhookSecret)
    .update(`${timestamp}.${payload}`)
    .digest("hex");
  return { payload, signature: `t=${timestamp},v1=${v1}` };
}

async function sendWebhook(
  mf: Awaited<ReturnType<typeof runtime>>["mf"],
  id: string,
  eventObject?: Record<string, unknown>,
  eventType?: string,
) {
  const event = signedEvent(id, eventObject, eventType);
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

test("signed dispute charge lookup maps the customer and stays retryable across lookup failure and quarantine", async () => {
  let lookupMode = "unavailable";
  let chargeReads = 0;
  const { mf, db } = await runtime(async (request) => {
    const url = new URL(request.url);
    assert.equal(request.method, "GET");
    assert.equal(url.origin, "https://api.stripe.com");
    assert.equal(url.pathname, "/v1/charges/ch_dispute_fixture");
    chargeReads++;
    if (lookupMode === "unavailable")
      return new RuntimeResponse(JSON.stringify({ error: { type: "api_error", message: "Fixture unavailable" } }), {
        status: 503, headers: { "Content-Type": "application/json" },
      });
    return new RuntimeResponse(JSON.stringify({
      id: "ch_dispute_fixture", object: "charge", customer: "cus_dispute_fixture",
      metadata: {}, livemode: lookupMode === "wrong-mode",
    }), { headers: { "Content-Type": "application/json" } });
  }, {
    ADVANCED_ENABLED: "true",
    STRIPE_SECRET_KEY: "sk_test_dispute_fixture",
    STRIPE_PRICE_ID: "price_dispute_fixture",
    STRIPE_WEBHOOK_SECRET: webhookSecret,
  });
  const eventObject = {
    id: "du_fixture", object: "dispute", charge: "ch_dispute_fixture", metadata: {},
  };
  const send = () => sendWebhook(mf, "evt_charge_dispute_fixture", eventObject, "charge.dispute.created");
  try {
    await db.prepare("INSERT INTO stripe_customers(customer,workspace) VALUES (?,?)")
      .bind("cus_dispute_fixture", workspace).run();
    const forged = signedEvent("evt_forged_dispute", eventObject, "charge.dispute.created");
    const rejected = await mf.dispatchFetch(origin + "/webhooks/stripe", {
      method: "POST", headers: { "Content-Type": "application/json", "Stripe-Signature": "t=1,v1=invalid" },
      body: forged.payload,
    });
    assert.equal(rejected.status, 400);
    assert.equal(chargeReads, 0);

    const unavailable = await send();
    assert.equal(unavailable.status, 503, await unavailable.clone().text());
    assert.equal(chargeReads, 1, "Lookup does not perform hidden SDK retries");
    assert.equal(await db.prepare("SELECT id FROM stripe_events WHERE id=?")
      .bind("evt_charge_dispute_fixture").first(), null);

    lookupMode = "wrong-mode";
    const wrongMode = await send();
    assert.equal(wrongMode.status, 400, await wrongMode.clone().text());

    lookupMode = "available";
    await setWorkspaceQuarantine(db, workspace, true, "Dispute recovery fixture");
    const quarantined = await send();
    assert.equal(quarantined.status, 503, await quarantined.clone().text());
    const pending = await db.prepare("SELECT workspace,completed_at FROM stripe_events WHERE id=?")
      .bind("evt_charge_dispute_fixture").first<{ workspace: string; completed_at: number | null }>();
    assert.equal(pending?.workspace, workspace);
    assert.equal(pending?.completed_at == null, true);

    await setWorkspaceQuarantine(db, workspace, false, "Dispute recovery reconciled");
    const completed = await send();
    assert.equal(completed.status, 200, await completed.clone().text());
    const receipt = await db.prepare("SELECT completed_at FROM stripe_events WHERE id=?")
      .bind("evt_charge_dispute_fixture").first<{ completed_at: number }>();
    assert.equal(typeof receipt?.completed_at, "number");
    const duplicate = await send();
    assert.equal(duplicate.status, 200);
    assert.equal((await duplicate.json() as any).duplicate, true);
  } finally {
    await mf.dispose();
  }
});
