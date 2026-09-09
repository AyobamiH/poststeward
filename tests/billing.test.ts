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
          return { id: "cs_test", url: "https://checkout.stripe.com/test" };
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
