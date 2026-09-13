import { test } from "node:test";
import assert from "node:assert/strict";
import { Billing, stripeWebhook } from "../src/billing.ts";
import { MemoryStore, environment, owner } from "./helpers.ts";
import type { Entitlement } from "../src/types.ts";
import { Challenge, Credential } from "mppx";
function setup() {
  const store = new MemoryStore(),
    env = {
      ...environment,
      ADVANCED_ENABLED: "true",
      MPP_ENABLED: "true",
      MPP_SECRET: "test-secret-".repeat(5),
      STRIPE_SECRET_KEY: "sk_test_not_real",
      STRIPE_PROFILE_ID: "profile_test_not_real",
      STRIPE_PRICE_ID: "price_test",
    };
  const calls: any[] = [];
  const client: any = {
    rawRequest: async () => {
      throw new Error("Unexpected Stripe network request");
    },
    prices: {
      retrieve: async () => ({
        id: "price_test",
        livemode: false,
        active: true,
        currency: "usd",
        unit_amount: 500,
        recurring: { interval: "month", interval_count: 1 },
      }),
    },
    checkout: {
      sessions: {
        create: async (p: any, o: any) => {
          calls.push({ p, o });
          return { id: "cs_test", url: "https://checkout.stripe.com/test", livemode: false };
        },
        retrieve: async () => ({
          id: "cs_test",
          client_reference_id: owner.workspace,
          metadata: { quote: store.get<any>("billing:attempt").quote },
          livemode: false,
          status: "open",
          subscription: null,
        }),
      },
    },
    paymentIntents: {
      search: async () => ({ data: [] }),
      retrieve: async () => null,
    },
  };
  return {
    store,
    env,
    client,
    calls,
    billing: new Billing(store, env, owner.workspace, client),
  };
}
test("SDK payment verification grants one exact month and replay creates no second charge", async () => {
  const h = setup();
  let charges = 0;
  let paid: any;
  h.client.paymentIntents.create = async (params: any, options: any) => {
    charges++;
    assert.equal(params.amount, 500);
    assert.match(options.idempotencyKey, /^mpp-pass:/);
    paid = {
      ...params,
      id: "pi_simulated",
      status: "succeeded",
      livemode: false,
      latest_charge: { refunded: false, amount_refunded: 0, disputed: false },
    };
    return paid;
  };
  h.client.paymentIntents.retrieve = async () => paid;
  const quote = await h.billing.quote({ mode: "pass" }, owner);
  const url = "https://publish.example/payments/" + quote.id;
  const response = await h.billing.machinePayment(
    new Request(url, { method: "POST", body: "{}" }),
    owner,
    quote.id,
  );
  const challenge = Challenge.fromResponse(response);
  const header = Credential.serialize({
    challenge,
    payload: { spt: "spt_simulated", externalId: quote.id },
  });
  const purchase = () =>
    h.billing.machinePayment(
      new Request(url, {
        method: "POST",
        headers: { "Payment-Authorization": header },
        body: "{}",
      }),
      owner,
      quote.id,
    );
  assert.equal((await purchase()).status, 200);
  assert.equal(h.store.get<Entitlement>("entitlement")?.until, quote.end);
  assert.equal((await purchase()).status, 200);
  assert.equal(charges, 1);
  paid.latest_charge.refunded = true;
  await h.billing.reconcile();
  assert.equal(h.store.get<Entitlement>("entitlement")?.revoked, true);
});
test("altering an SDK payment challenge never reaches Stripe", async () => {
  const h = setup();
  let charges = 0;
  h.client.paymentIntents.create = async () => {
    charges++;
    throw new Error("Must not charge");
  };
  const quote = await h.billing.quote({ mode: "pass" }, owner);
  const url = "https://publish.example/payments/" + quote.id;
  const response = await h.billing.machinePayment(
    new Request(url, { method: "POST", body: "{}" }),
    owner,
    quote.id,
  );
  const challenge = Challenge.fromResponse(response);
  (challenge.request as any).amount = "1";
  const header = Credential.serialize({
    challenge,
    payload: { spt: "spt_simulated", externalId: quote.id },
  });
  const result = await h.billing.machinePayment(
    new Request(url, {
      method: "POST",
      headers: { "Payment-Authorization": header },
      body: "{}",
    }),
    owner,
    quote.id,
  );
  assert.equal(result.status, 402);
  assert.equal(charges, 0);
  assert.equal(h.store.get("entitlement"), undefined);
});
test("disabled deployment cannot quote or charge for incomplete Advanced", async () => {
  const store = new MemoryStore(),
    billing = new Billing(store, environment, owner.workspace);
  await assert.rejects(billing.quote({ mode: "subscription" }, owner), {
    code: "BILLING_UNAVAILABLE",
  });
});
test("quote fixes USD 5 and one-time access does not auto-renew", async () => {
  const h = setup();
  const q = await h.billing.quote({ mode: "pass" }, owner);
  assert.equal(q.amount, 500);
  assert.equal(q.currency, "usd");
  assert.equal(q.autoRenew, false);
  assert.equal(q.workspace, owner.workspace);
  assert.ok(q.end > q.start + 27 * 86400000);
});
test("cross-workspace or cross-actor checkout cannot charge", async () => {
  const h = setup();
  const q = await h.billing.quote({ mode: "subscription" }, owner);
  await assert.rejects(
    h.billing.checkout({ quote: q.id }, { ...owner, id: "other" }),
    { code: "INVALID_QUOTE" },
  );
  await assert.rejects(
    h.billing.checkout({ quote: q.id }, { ...owner, workspace: "other" }),
    { code: "INVALID_QUOTE" },
  );
  assert.equal(h.calls.length, 0);
});
test("duplicate checkout retrieves the stored attempt and never grants on session creation", async () => {
  const h = setup();
  const q = await h.billing.quote({ mode: "subscription" }, owner);
  const one = await h.billing.checkout({ quote: q.id }, owner),
    two = await h.billing.checkout({ quote: q.id }, owner);
  assert.equal(one.url, two.url);
  assert.equal(h.calls.length, 1);
  assert.equal(h.store.get("entitlement"), undefined);
  assert.match(h.calls[0].o.idempotencyKey, /^checkout:/);
  assert.equal(h.calls[0].p.line_items[0].price, "price_test");
});
test("price mismatch fails before any checkout session is created", async () => {
  const h = setup();
  h.client.prices.retrieve = async () => ({
    id: "wrong",
    active: true,
    unit_amount: 5000,
    currency: "usd",
    recurring: { interval: "month" },
  });
  const q = await h.billing.quote({ mode: "subscription" }, owner);
  await assert.rejects(h.billing.checkout({ quote: q.id }, owner), {
    code: "PRICE_MISMATCH",
  });
  assert.equal(h.calls.length, 0);
});
test("active coverage blocks subscription and machine-payment overlap", async () => {
  const h = setup();
  h.store.put("entitlement", {
    kind: "subscription",
    until: Date.now() + 86400000,
    reference: "sub_test",
  } satisfies Entitlement);
  await assert.rejects(h.billing.quote({ mode: "pass" }, owner), {
    code: "ALREADY_COVERED",
  });
  await assert.rejects(h.billing.quote({ mode: "subscription" }, owner), {
    code: "ALREADY_COVERED",
  });
});
test("MPP challenge is generated by the installed SDK without charging", async () => {
  const h = setup();
  const q = await h.billing.quote({ mode: "pass" }, owner);
  const response = await h.billing.machinePayment(
    new Request("https://publish.example/payments/" + q.id, {
      method: "POST",
      headers: { Accept: "application/json" },
      body: "{}",
    }),
    owner,
    q.id,
  );
  assert.equal(response.status, 402);
  const header = response.headers.get("www-authenticate");
  assert.ok(header?.includes("Payment"));
  assert.equal(h.store.get("entitlement"), undefined);
  assert.equal(h.calls.length, 0);
});
test("invalid MPP credential grants nothing", async () => {
  const h = setup();
  const q = await h.billing.quote({ mode: "pass" }, owner);
  const response = await h.billing.machinePayment(
    new Request("https://publish.example/payments/" + q.id, {
      method: "POST",
      headers: {
        "Payment-Authorization": "Payment invalid",
        Accept: "application/json",
      },
      body: "{}",
    }),
    owner,
    q.id,
  );
  assert.equal(response.status, 402);
  assert.equal(h.store.get("entitlement"), undefined);
});
test("forged Stripe webhook never reaches a workspace or grants access", async () => {
  const h = setup();
  await assert.rejects(
    stripeWebhook(
      new Request("https://publish.example/webhooks/stripe", {
        method: "POST",
        headers: { "stripe-signature": "t=1,v1=fake" },
        body: JSON.stringify({
          type: "invoice.paid",
          data: { object: { metadata: { workspace: owner.workspace } } },
        }),
      }),
      { ...h.env, STRIPE_WEBHOOK_SECRET: "whsec_test_only" },
    ),
    { code: "INVALID_SIGNATURE" },
  );
});

test("staging sandbox Checkout does not require or enable Advanced or MPP", async () => {
  const h = setup();
  Object.assign(h.env, {
    DEPLOY_ENV: "staging", SIGNUP_MODE: "restricted",
    ADVANCED_ENABLED: "false", MPP_ENABLED: "false",
    STRIPE_SANDBOX_ENABLED: "true", STRIPE_WEBHOOK_SECRET: "whsec_sandbox",
  });
  assert.equal((await h.billing.status()).sandbox, true);
  const q = await h.billing.quote({ mode: "subscription" }, owner);
  await h.billing.checkout({ quote: q.id }, owner);
  assert.equal(h.calls.length, 1);
  assert.equal(h.store.get("entitlement"), undefined);
  await assert.rejects(h.billing.quote({ mode: "pass" }, owner), { code: "MPP_UNAVAILABLE" });
  assert.equal(h.env.ADVANCED_ENABLED, "false");
});
test("sandbox enablement refuses production, public signup, missing webhook and live keys", async () => {
  for (const changed of [
    { DEPLOY_ENV: "production" }, { SIGNUP_MODE: "public" },
    { STRIPE_WEBHOOK_SECRET: "" }, { STRIPE_SECRET_KEY: "sk_live_not_real" },
  ]) {
    const h = setup();
    Object.assign(h.env, {
      DEPLOY_ENV: "staging", SIGNUP_MODE: "restricted",
      ADVANCED_ENABLED: "false", MPP_ENABLED: "false",
      STRIPE_SANDBOX_ENABLED: "true", STRIPE_WEBHOOK_SECRET: "whsec_sandbox",
    }, changed);
    await assert.rejects(h.billing.quote({ mode: "subscription" }, owner), { code: "BILLING_UNAVAILABLE" });
    assert.equal(h.calls.length, 0);
  }
});
test("a live Stripe Price is refused before reservation and sandbox price can then be retried", async () => {
  const h = setup();
  const retrieve = h.client.prices.retrieve;
  h.client.prices.retrieve = async () => ({ ...await retrieve(), livemode: true });
  const q = await h.billing.quote({ mode: "subscription" }, owner);
  await assert.rejects(h.billing.checkout({ quote: q.id }, owner), { code: "PRICE_MISMATCH" });
  assert.equal(h.calls.length, 0);
  assert.equal(h.store.get("billing:attempt"), undefined);
  h.client.prices.retrieve = retrieve;
  await h.billing.checkout({ quote: q.id }, owner);
  assert.equal(h.calls.length, 1);
});

test("subscription settlement reconciles once and later refund or dispute revokes confirmed coverage", async () => {
  for (const outcome of ["refund", "dispute"]) {
    const h = setup();
    const q = await h.billing.quote({ mode: "subscription" }, owner);
    await h.billing.checkout({ quote: q.id }, owner);
    h.client.checkout.sessions.retrieve = async () => ({
      id: "cs_test", client_reference_id: owner.workspace,
      metadata: { quote: q.id }, livemode: false, status: "complete",
      subscription: "sub_test",
    });
    const charge = { refunded: false, amount_refunded: 0, disputed: false };
    const until = Math.floor(Date.now() / 1000) + 86400;
    h.client.subscriptions = { retrieve: async (_id: string, params: any) => {
      assert.deepEqual(params.expand, ["latest_invoice.payments"]);
      return ({
      id: "sub_test", status: "active", customer: "cus_test", metadata: { workspace: owner.workspace },
      items: { data: [{ quantity: 1, current_period_end: until,
        price: { id: "price_test", unit_amount: 500, currency: "usd" } }] },
      latest_invoice: { status: "paid", currency: "usd", amount_paid: 500,
        payments: { has_more: false, data: [{ payment: { payment_intent: "pi_test" } }] } },
    }); } };
    h.client.paymentIntents.retrieve = async (id: string, params: any) => {
      assert.equal(id, "pi_test");
      assert.deepEqual(params.expand, ["latest_charge"]);
      return { id, livemode: false, status: "succeeded", currency: "usd", latest_charge: charge };
    };
    const retrievePayment = h.client.paymentIntents.retrieve;
    h.client.paymentIntents.retrieve = async () => { throw new Error("Provider unavailable"); };
    await assert.rejects(h.billing.reconcile(), /Provider unavailable/);
    assert.equal(h.store.get("entitlement"), undefined);
    assert.equal(h.store.get<any>("billing:attempt").status, "pending");
    h.client.paymentIntents.retrieve = retrievePayment;
    await h.billing.reconcile();
    await h.billing.reconcile();
    assert.equal(h.store.get<Entitlement>("entitlement")?.until, until * 1000);
    assert.equal(h.calls.length, 1);
    if (outcome === "refund") charge.amount_refunded = 500;
    else charge.disputed = true;
    await h.billing.reconcile();
    assert.equal(h.store.get<Entitlement>("entitlement")?.revoked, true);
    assert.equal(h.calls.length, 1);
  }
});

test("interrupted Checkout retries preserve the integration identifier across worker reconstruction", async () => {
  const h = setup();
  const q = await h.billing.quote({ mode: "subscription" }, owner);
  const attempts: any[] = [];
  h.client.checkout.sessions.create = async (params: any, options: any) => {
    attempts.push({ params, options });
    if (attempts.length === 1) throw new Error("Simulated lost Stripe response");
    return { id: "cs_test", url: "https://checkout.stripe.com/test", livemode: false };
  };
  await assert.rejects(h.billing.checkout({ quote: q.id }, owner), {
    code: "CHECKOUT_PENDING_RECONCILIATION",
  });
  const restarted = new Billing(h.store, h.env, owner.workspace, h.client);
  await restarted.checkout({ quote: q.id }, owner);
  assert.equal(attempts.length, 2);
  assert.match(attempts[0].params.integration_identifier, /^poststeward_checkout_[a-z]{8}$/);
  assert.deepEqual(attempts[1], attempts[0]);
  assert.equal(h.store.get("entitlement"), undefined);
});

test("pre-upgrade quotes keep their original Checkout parameters on retry", async () => {
  const h = setup();
  const q = await h.billing.quote({ mode: "subscription" }, owner);
  const legacy: any = h.store.get("quote:" + q.id);
  delete legacy.integrationIdentifier;
  h.store.put("quote:" + q.id, legacy);
  h.store.put("billing:attempt", {
    quote: q.id, mode: "subscription", startedAt: Date.now(), status: "pending",
  });
  await h.billing.checkout({ quote: q.id }, owner);
  assert.equal(h.calls.length, 1);
  assert.equal(Object.hasOwn(h.calls[0].p, "integration_identifier"), false);
  assert.equal(h.calls[0].o.idempotencyKey, "checkout:" + owner.workspace + ":" + q.id);
});

test("billing evidence reads only this workspace and precedes status reconciliation", async () => {
  const h = setup();
  const order: string[] = [];
  h.env.IDENTITY = { prepare(sql: string) {
    assert.match(sql, /WHERE workspace=\? ORDER BY received_at DESC, id DESC LIMIT 20/);
    return { bind(workspace: string) {
      assert.equal(workspace, owner.workspace);
      return { async all() {
        order.push("ledger");
        return { success: true, results: [
          { id: "evt_complete", received_at: 100, completed_at: 200, private: "omit" },
          { id: "evt_pending", received_at: 300, completed_at: null },
        ] };
      } };
    } };
  } } as any;
  h.store.put("billing:attempt", { status: "pending" });
  h.billing.reconcile = async () => { order.push("reconcile"); };
  const result = await h.billing.status();
  assert.deepEqual(order, ["ledger", "reconcile"]);
  assert.equal(result.webhookEvidence.available, true);
  assert.deepEqual(result.webhookEvidence.events, [
    { id: "evt_complete", receivedAt: 100, completedAt: 200 },
    { id: "evt_pending", receivedAt: 300, completedAt: null },
  ]);
});
test("unavailable webhook ledger is not reported as a successful empty inventory", async () => {
  const h = setup();
  const result = await h.billing.status();
  assert.equal(result.webhookEvidence.available, false);
  assert.deepEqual(result.webhookEvidence.events, []);
});

function deletionSetup(subscriptions: any[] = []) {
  const h = setup();
  h.env.IDENTITY = { prepare: () => ({ bind: () => ({ first: async () => ({ customer: "cus_delete" }) }) }) } as any;
  let cancelCalls = 0;
  h.client.subscriptions = {
    list: async () => ({ has_more: false, data: subscriptions }),
    cancel: async (id: string, params: any) => {
      cancelCalls++;
      assert.deepEqual(params, { invoice_now: false, prorate: false });
      subscriptions.find(s => s.id === id).status = "canceled";
    },
    retrieve: async (id: string) => subscriptions.find(s => s.id === id),
  };
  return { ...h, cancelCalls: () => cancelCalls };
}
const deletionSubscription = () => ({ id: "sub_delete", customer: "cus_delete", livemode: false, metadata: { workspace: owner.workspace }, status: "active" });

test("deletion confirms workspace subscription cancellation and retries without another cancellation", async () => {
  const h = deletionSetup([deletionSubscription()]);
  assert.equal((await h.billing.clearForDeletion()).cleared, true);
  assert.equal(h.cancelCalls(), 1);
  await h.billing.clearForDeletion();
  assert.equal(h.cancelCalls(), 1);
  assert.equal(h.store.get("lifecycle:deleting"), true);
});

test("deletion rejects incomplete or cross-workspace inventory before cancellation", async () => {
  const h = deletionSetup([{ ...deletionSubscription(), metadata: { workspace: "another-workspace" } }]);
  await assert.rejects(h.billing.clearForDeletion(), { code: "BILLING_MAPPING_MISMATCH" });
  assert.equal(h.cancelCalls(), 0);
  h.client.subscriptions.list = async () => ({ data: [deletionSubscription()], has_more: true });
  await assert.rejects(h.billing.clearForDeletion(), { code: "BILLING_DELETE_PENDING" });
  assert.equal(h.cancelCalls(), 0);
});

test("deletion retains an unresolved Checkout attempt and fences an overlapping purchase claim", async () => {
  const h = deletionSetup();
  const q = await h.billing.quote({ mode: "subscription" }, owner);
  h.store.put("billing:attempt", { quote: q.id, mode: "subscription", status: "pending", startedAt: Date.now() });
  await assert.rejects(h.billing.clearForDeletion(), { code: "BILLING_DELETE_PENDING" });
  assert.equal(h.store.get<any>("billing:attempt")?.quote, q.id);
  await assert.rejects(h.billing.checkout({ quote: q.id }, owner), { code: "WORKSPACE_DELETION_IN_PROGRESS" });
  assert.equal(h.calls.length, 0);
});

test("deletion blocks erasure when cancellation readback is not terminal", async () => {
  const h = deletionSetup([deletionSubscription()]);
  h.client.subscriptions.cancel = async () => ({ status: "canceled" });
  await assert.rejects(h.billing.clearForDeletion(), { code: "BILLING_DELETE_PENDING" });
});

test("deletion expires pending Checkout before cancelling its subscription", async () => {
  const h = deletionSetup([deletionSubscription()]);
  h.store.put("billing:attempt", { quote: "q_delete", mode: "subscription", status: "pending", session: "cs_delete" });
  let expired = false;
  h.client.checkout.sessions.retrieve = async () => ({ id: "cs_delete", status: expired ? "expired" : "open", client_reference_id: owner.workspace, metadata: { quote: "q_delete" }, livemode: false, customer: "cus_delete" });
  h.client.checkout.sessions.expire = async () => { expired = true; };
  await h.billing.clearForDeletion();
  assert.equal(expired, true);
  assert.equal(h.cancelCalls(), 1);
});
