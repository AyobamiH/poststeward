import Stripe from "stripe";
import { sandboxBillingEnabled, stripeCredentialAllowed } from "./billing-mode.ts";
import { Mppx, stripe as machineStripe } from "mppx/server";
import { addMonth, digest, Fault, json, requireValue, uid } from "./common.ts";
import type { Actor, Entitlement, Env, Store } from "./types.ts";
import type { BillingPort } from "./engine.ts";
export interface Quote {
  id: string;
  workspace: string;
  actor: string;
  mode: "subscription" | "pass";
  amount: 500;
  currency: "usd";
  version: "advanced-v1";
  start: number;
  end: number;
  expires: number;
  autoRenew: boolean;
  // Persisted with new quotes so interrupted Checkout retries keep identical parameters.
  integrationIdentifier?: string;
}
type Attempt = {
  quote: string;
  mode: "subscription" | "pass";
  startedAt: number;
  status: "pending" | "paid" | "failed";
  session?: string;
  url?: string;
  paymentIntent?: string;
  credentialHash?: string;
};
export class Billing implements BillingPort {
  private stripe?: Stripe;
  constructor(
    private store: Store,
    private env: Env,
    private workspace: string,
    client?: Stripe,
  ) {
    if (client) this.stripe = client;
    else if (env.STRIPE_SECRET_KEY)
      this.stripe = new Stripe(env.STRIPE_SECRET_KEY, {
        httpClient: Stripe.createFetchHttpClient(),
        maxNetworkRetries: 0,
        timeout: 15000,
      });
  }
  private available() {
    return (
      (this.env.ADVANCED_ENABLED === "true" || sandboxBillingEnabled(this.env)) &&
      stripeCredentialAllowed(this.env) &&
      !!this.stripe &&
      !!this.env.STRIPE_PRICE_ID
    );
  }
  private requireAvailable() {
    requireValue(
      this.available(),
      "BILLING_UNAVAILABLE",
      "Advanced purchases are not enabled for this deployment.",
      503,
    );
    return this.stripe!;
  }
  private covered() {
    const e = this.store.get<Entitlement>("entitlement");
    return e && !e.revoked && e.until > Date.now();
  }
  async status() {
    if (this.stripe && this.store.get("billing:attempt")) {
      try {
        await this.reconcile();
      } catch {
        /* Preserve last confirmed coverage; never extend on reconciliation failure. */
      }
    }
    return {
      sandbox: sandboxBillingEnabled(this.env),
      entitlement: this.store.get<Entitlement>("entitlement") || null,
      attempt: this.store.get<Attempt>("billing:attempt") || null,
      price: { amount: 500, currency: "usd", interval: "month" },
      methods: {
        checkout: { available: this.available() },
        mpp: {
          available:
            this.available() &&
            this.env.MPP_ENABLED === "true" &&
            !!this.env.STRIPE_PROFILE_ID &&
            !!this.env.MPP_SECRET,
          autoRenew: false,
          reason:
            this.env.MPP_ENABLED === "true"
              ? "Merchant and agent wallet eligibility must both be satisfied."
              : "Machine payments await merchant sandbox validation.",
        },
      },
    };
  }
  async quote(input: any, actor: Actor) {
    this.requireAvailable();
    requireValue(
      !this.covered(),
      "ALREADY_COVERED",
      "Advanced access is already active. Inspect billing status or renewal in the portal.",
      409,
    );
    requireValue(
      input.mode !== "pass" || this.env.MPP_ENABLED === "true",
      "MPP_UNAVAILABLE",
      "Direct agent payment is not enabled; use subscription checkout.",
      503,
    );
    const now = Date.now();
    const quote: Quote = {
      id: uid(),
      workspace: this.workspace,
      actor: actor.id,
      mode: input.mode,
      amount: 500,
      currency: "usd",
      version: "advanced-v1",
      start: now,
      end: addMonth(now),
      expires: now + 600000,
      autoRenew: input.mode === "subscription",
      ...(input.mode === "subscription"
        ? {
            integrationIdentifier:
              "poststeward_checkout_" +
              Array.from(crypto.getRandomValues(new Uint8Array(8)), (value) =>
                String.fromCharCode(97 + (value % 26)),
              ).join(""),
          }
        : {}),
    };
    this.store.put("quote:" + quote.id, quote);
    return {
      ...quote,
      tax: "Total USD 5.00; configured Stripe price must be tax inclusive or use no additional checkout tax.",
      paymentPath: input.mode === "pass" ? "/payments/" + quote.id : null,
    };
  }
  private getQuote(id: string, actor: Actor, mode: Quote["mode"]) {
    const q = this.store.get<Quote>("quote:" + id);
    requireValue(
      q &&
        q.workspace === actor.workspace &&
        q.actor === actor.id &&
        q.mode === mode,
      "INVALID_QUOTE",
      "Quote does not belong to this actor, workspace or payment mode.",
      403,
    );
    requireValue(
      q.expires > Date.now(),
      "QUOTE_EXPIRED",
      "Create a fresh quote; this quote can no longer start a payment.",
      409,
    );
    return q;
  }
  private claim(q: Quote) {
    return this.store.tx(() => {
      requireValue(
        !this.covered(),
        "ALREADY_COVERED",
        "Workspace already has Advanced coverage.",
        409,
      );
      const old = this.store.get<Attempt>("billing:attempt");
      requireValue(
        !this.store.get<boolean>("billing:renewing") || old?.quote === q.id,
        "SUBSCRIPTION_STILL_OPEN",
        "Manage the existing subscription in Stripe before starting a different purchase.",
        409,
      );
      requireValue(
        !old ||
          old.status === "failed" ||
          (old.status === "paid" && !this.covered()) ||
          old.quote === q.id,
        "PURCHASE_IN_PROGRESS",
        "Another purchase is pending. Inspect billing status; do not pay again.",
        409,
      );
      if (old?.quote === q.id) return old;
      if (old) this.store.put("billing:history:" + old.quote, old);
      const attempt: Attempt = {
        quote: q.id,
        mode: q.mode,
        startedAt: Date.now(),
        status: "pending",
      };
      this.store.put("billing:attempt", attempt);
      return attempt;
    });
  }
  async checkout(input: any, actor: Actor) {
    const stripe = this.requireAvailable(),
      q = this.getQuote(input.quote, actor, "subscription");
    const existing = this.store.get<Attempt>("billing:attempt");
    if (existing?.quote === q.id && existing.url)
      return {
        url: existing.url,
        session: existing.session,
        status: existing.status,
      };
    // All callers, including interrupted retries, use the same Stripe key and parameters.
    const price = await stripe.prices.retrieve(this.env.STRIPE_PRICE_ID!);
    requireValue(
      price.active &&
        price.livemode === !this.env.STRIPE_SECRET_KEY!.includes("_test_") &&
        price.currency === "usd" &&
        price.unit_amount === 500 &&
        price.recurring?.interval === "month" &&
        price.recurring.interval_count === 1,
      "PRICE_MISMATCH",
      "Stripe Price must be active USD 5 per month.",
      503,
    );
    const attempt = this.claim(q);
    const knownCustomer = this.store.get<string>("billing:customer");
    try {
      const session = await stripe.checkout.sessions.create(
        {
          mode: "subscription",
          // Legacy quotes omit the new parameter, including retries of pre-upgrade requests.
          ...(q.integrationIdentifier
            ? { integration_identifier: q.integrationIdentifier }
            : {}),
          line_items: [{ price: price.id, quantity: 1 }],
          ...(knownCustomer ? { customer: knownCustomer } : {}),
          client_reference_id: this.workspace,
          metadata: { workspace: this.workspace, quote: q.id },
          subscription_data: {
            metadata: { workspace: this.workspace, quote: q.id },
          },
          success_url: this.env.PUBLIC_ORIGIN + "/app?checkout=returned",
          cancel_url: this.env.PUBLIC_ORIGIN + "/app?checkout=cancelled",
          allow_promotion_codes: false,
          automatic_tax: { enabled: false },
        },
        { idempotencyKey: "checkout:" + this.workspace + ":" + q.id },
      );
      requireValue(
        session.livemode === !this.env.STRIPE_SECRET_KEY!.includes("_test_"),
        "BILLING_MODE_MISMATCH", "Stripe Checkout environment mismatch.", 409,
      );
      attempt.session = session.id;
      attempt.url = session.url || undefined;
      this.store.put("billing:attempt", attempt);
      return { url: session.url, status: "pending", quote: q };
    } catch {
      throw new Fault(
        "CHECKOUT_PENDING_RECONCILIATION",
        "Checkout creation did not return a confirmed result. Inspect billing status before creating another purchase.",
        502,
      );
    }
  }
  async portal(input: any, actor: Actor) {
    const stripe = this.requireAvailable(),
      customer = this.store.get<string>("billing:customer");
    requireValue(
      customer,
      "NO_BILLING_CUSTOMER",
      "There is no Stripe customer for this workspace.",
      409,
    );
    const session = await stripe.billingPortal.sessions.create(
      { customer, return_url: this.env.PUBLIC_ORIGIN + "/app" },
      {
        idempotencyKey: "portal:" + this.workspace + ":" + input.idempotencyKey,
      },
    );
    return { url: session.url };
  }
  async reconcile() {
    if (!this.stripe) return;
    requireValue(stripeCredentialAllowed(this.env), "BILLING_MODE_MISMATCH",
      "Stripe credentials do not match this deployment.", 409);
    const a = this.store.get<Attempt>("billing:attempt");
    if (!a) return;
    if (a.mode === "pass") {
      if (!a.paymentIntent) {
        const found = await this.stripe.paymentIntents.search({
          query: `metadata['workspace']:'${this.workspace}' AND metadata['quote']:'${a.quote}'`,
          limit: 2,
        });
        if (found.data.length === 1) a.paymentIntent = found.data[0].id;
        else return;
        this.store.put("billing:attempt", a);
      }
      const pi = await this.stripe.paymentIntents.retrieve(a.paymentIntent, {
        expand: ["latest_charge"],
      });
      await this.grantPass(pi, a);
      return;
    }
    if (!a.session) {
      const sessions = await this.stripe.checkout.sessions.list({
        limit: 100,
        created: { gte: Math.floor(a.startedAt / 1000) - 60 },
      });
      const match = sessions.data.find(
        (s) =>
          s.client_reference_id === this.workspace &&
          s.metadata?.quote === a.quote,
      );
      if (!match) return;
      a.session = match.id;
      this.store.put("billing:attempt", a);
    }
    const session = await this.stripe.checkout.sessions.retrieve(a.session);
    requireValue(
      session.client_reference_id === this.workspace &&
        session.metadata?.quote === a.quote,
      "BILLING_MAPPING_MISMATCH",
      "Checkout does not belong to this workspace.",
      409,
    );
    requireValue(
      session.livemode === !this.env.STRIPE_SECRET_KEY!.includes("_test_"),
      "BILLING_MODE_MISMATCH",
      "Stripe environment mismatch.",
      409,
    );
    if (session.status === "expired") {
      a.status = "failed";
      this.store.put("billing:attempt", a);
      return;
    }
    if (typeof session.customer === "string") {
      this.store.put("billing:customer", session.customer);
      await this.env.IDENTITY.prepare(
        "INSERT INTO stripe_customers(customer,workspace) VALUES (?,?) ON CONFLICT(workspace) DO UPDATE SET customer=excluded.customer",
      )
        .bind(session.customer, this.workspace)
        .run();
    }
    if (!session.subscription) return;
    const sub: any = await this.stripe.subscriptions.retrieve(
      typeof session.subscription === "string"
        ? session.subscription
        : session.subscription.id,
      {
        expand: [
          "latest_invoice.payments.data.payment.payment_intent.latest_charge",
        ],
      },
    );
    requireValue(
      sub.metadata.workspace === this.workspace,
      "BILLING_MAPPING_MISMATCH",
      "Subscription workspace mismatch.",
      409,
    );
    this.store.put(
      "billing:renewing",
      !["canceled", "incomplete_expired"].includes(sub.status),
    );
    const item = sub.items.data.find(
      (i: any) =>
        i.price.id === this.env.STRIPE_PRICE_ID &&
        i.price.unit_amount === 500 &&
        i.price.currency === "usd" &&
        i.quantity === 1,
    );
    const invoice = sub.latest_invoice;
    const paymentIntents = (invoice?.payments?.data || [])
      .map((p: any) => p.payment?.payment_intent)
      .filter((p: any) => p && typeof p === "object");
    const invalid = paymentIntents.some(
      (p: any) =>
        p.latest_charge?.refunded ||
        p.latest_charge?.amount_refunded > 0 ||
        p.latest_charge?.disputed,
    );
    // Invoice and payment are checked server-side. A subscription label or success URL alone grants nothing.
    if (
      item &&
      invoice?.status === "paid" &&
      invoice.currency === "usd" &&
      invoice.amount_paid >= 500 &&
      !invalid &&
      ["active", "past_due", "canceled"].includes(sub.status)
    ) {
      const until = Number(item.current_period_end) * 1000;
      if (Number.isFinite(until))
        this.store.put("entitlement", {
          kind: "subscription",
          until,
          reference: sub.id,
          customer:
            typeof sub.customer === "string" ? sub.customer : sub.customer.id,
        } satisfies Entitlement);
      a.status = "paid";
      this.store.put("billing:attempt", a);
    } else if (invalid) {
      const e = this.store.get<Entitlement>("entitlement");
      if (e && e.reference === sub.id) {
        e.revoked = true;
        this.store.put("entitlement", e);
      }
    }
  }
  private async grantPass(pi: any, a: Attempt) {
    const q = this.store.get<Quote>("quote:" + a.quote);
    requireValue(
      q &&
        pi.metadata?.workspace === this.workspace &&
        pi.metadata?.quote === a.quote &&
        pi.amount === 500 &&
        pi.currency === "usd" &&
        pi.livemode === !this.env.STRIPE_SECRET_KEY!.includes("_test_"),
      "PAYMENT_MISMATCH",
      "Payment does not match the server quote.",
      409,
    );
    const charge = pi.latest_charge;
    const invalid =
      typeof charge === "object" &&
      charge &&
      (charge.refunded || charge.amount_refunded > 0 || charge.disputed);
    if (pi.status === "succeeded" && !invalid) {
      this.store.put("entitlement", {
        kind: "pass",
        until: q.end,
        reference: pi.id,
      } satisfies Entitlement);
      a.status = "paid";
      a.paymentIntent = pi.id;
      this.store.put("billing:attempt", a);
    } else if (invalid) {
      const e = this.store.get<Entitlement>("entitlement");
      if (e && e.reference === pi.id) {
        e.revoked = true;
        this.store.put("entitlement", e);
      }
    }
  }
  async machinePayment(
    request: Request,
    actor: Actor,
    quoteId: string,
  ): Promise<Response> {
    const stripe = this.requireAvailable();
    requireValue(
      this.env.MPP_ENABLED === "true" &&
        this.env.MPP_SECRET &&
        this.env.STRIPE_PROFILE_ID,
      "MPP_UNAVAILABLE",
      "Machine payments await merchant validation. Subscription checkout remains available.",
      503,
    );
    requireValue(
      actor.scopes.includes("billing") || actor.scopes.includes("admin"),
      "INSUFFICIENT_SCOPE",
      "Billing authority is required.",
      403,
    );
    const old = this.store.get<Attempt>("billing:attempt");
    if (old?.quote === quoteId && old.status === "paid")
      return json(await this.status());
    const q = this.getQuote(quoteId, actor, "pass");
    if (this.covered()) return json(await this.status());
    const credential = request.headers.get("payment-authorization");
    if (credential) {
      const a = this.claim(q);
      if (a.credentialHash) {
        await this.reconcile();
        const status = this.store.get<Attempt>("billing:attempt")?.status;
        return json(
          {
            status: status === "paid" ? "paid" : "pending_reconciliation",
            billing: await this.status(),
          },
          status === "paid" ? 200 : 202,
        );
      }
      a.credentialHash = await digest(credential);
      this.store.put("billing:attempt", a);
    }
    let attempted = false;
    const wrappedClient = {
      rawRequest: stripe.rawRequest?.bind(stripe),
      paymentIntents: {
        create: async (params: any, options: any) => {
          attempted = true;
          const a = this.claim(q);
          const pi = await stripe.paymentIntents.create(
            {
              ...params,
              metadata: {
                ...params.metadata,
                workspace: this.workspace,
                quote: q.id,
              },
            },
            {
              ...options,
              idempotencyKey: "mpp-pass:" + this.workspace + ":" + q.id,
            },
          );
          a.paymentIntent = pi.id;
          this.store.put("billing:attempt", a);
          await this.grantPass(pi, a);
          return pi;
        },
      },
    };
    const methods = machineStripe
      .create({
        client: wrappedClient,
        networkId: this.env.STRIPE_PROFILE_ID,
        livemode: !this.env.STRIPE_SECRET_KEY!.includes("_test_"),
      })
      .spt.charge();
    const mppx = Mppx.create({
      methods: [methods],
      requiresAuth: true,
      secretKey: this.env.MPP_SECRET,
      realm: new URL(this.env.PUBLIC_ORIGIN).host,
    });
    const outcome = await mppx.charge({
      amount: "5.00",
      currency: "usd",
      externalId: q.id,
      metadata: { workspace: this.workspace, quote: q.id },
      scope: this.workspace + ":" + actor.id + ":" + q.id,
      expires: new Date(q.expires).toISOString(),
    })(request);
    if (outcome.status === 402) {
      if (credential && !attempted) {
        const a = this.store.get<Attempt>("billing:attempt");
        if (a?.quote === q.id) {
          delete a.credentialHash;
          a.status = "failed"; // SDK rejected the credential before a financial request.
          this.store.put("billing:attempt", a);
        }
      }
      return outcome.challenge;
    }
    return outcome.withReceipt(
      json({
        entitlement: this.store.get("entitlement"),
        quote: q,
        autoRenew: false,
      }),
    );
  }
}
export async function stripeWebhook(
  request: Request,
  env: Env,
): Promise<Response> {
  requireValue(
    env.STRIPE_SECRET_KEY && env.STRIPE_WEBHOOK_SECRET && stripeCredentialAllowed(env),
    "WEBHOOK_UNCONFIGURED",
    "Webhook is not configured.",
    503,
  );
  const signature = request.headers.get("stripe-signature");
  requireValue(
    signature,
    "INVALID_SIGNATURE",
    "Missing Stripe signature.",
    400,
  );
  const stripe = new Stripe(env.STRIPE_SECRET_KEY, {
    httpClient: Stripe.createFetchHttpClient(),
    maxNetworkRetries: 0,
    timeout: 15000,
  });
  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      await request.text(),
      signature,
      env.STRIPE_WEBHOOK_SECRET,
      300,
      Stripe.createSubtleCryptoProvider(),
    );
  } catch {
    throw new Fault(
      "INVALID_SIGNATURE",
      "Stripe signature did not verify.",
      400,
    );
  }
  requireValue(
    event.livemode === !env.STRIPE_SECRET_KEY.includes("_test_"),
    "BILLING_MODE_MISMATCH",
    "Wrong Stripe environment.",
    400,
  );
  const object = event.data.object as any;
  let workspace = object.metadata?.workspace || object.client_reference_id;
  let customer = typeof object.customer === "string" ? object.customer : undefined;
  // Dispute/refund objects can carry only a charge ID, with no customer or workspace.
  // Resolve that signed reference before acknowledging; a failed lookup must be retried.
  if (
    !workspace &&
    !customer &&
    ["dispute", "refund"].includes(object.object) &&
    typeof object.charge === "string"
  ) {
    let charge: Stripe.Charge;
    try {
      charge = await stripe.charges.retrieve(object.charge);
    } catch {
      throw new Fault(
        "BILLING_RECONCILIATION_PENDING",
        "Stripe charge lookup awaits retry.",
        503,
      );
    }
    requireValue(
      charge.id === object.charge && charge.livemode === event.livemode,
      "BILLING_MODE_MISMATCH",
      "Stripe charge does not match the signed event.",
      400,
    );
    workspace = charge.metadata?.workspace;
    customer = typeof charge.customer === "string" ? charge.customer : undefined;
  }
  if (!workspace && customer)
    workspace = (
      await env.IDENTITY.prepare(
        "SELECT workspace FROM stripe_customers WHERE customer=?",
      )
        .bind(customer)
        .first<{ workspace: string }>()
    )?.workspace;
  if (!workspace) return json({ received: true, matched: false });
  requireValue(
    /^[a-f0-9-]{36}$/.test(workspace),
    "INVALID_WORKSPACE",
    "Invalid workspace mapping.",
    400,
  );
  // Store before acknowledgement. Failed reconciliation returns non-2xx for Stripe retry.
  await env.IDENTITY.prepare(
    "INSERT INTO stripe_events(id,workspace,received_at) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING",
  )
    .bind(event.id, workspace, Date.now())
    .run();
  const row = await env.IDENTITY.prepare(
    "SELECT completed_at FROM stripe_events WHERE id=?",
  )
    .bind(event.id)
    .first<any>();
  if (row?.completed_at) return json({ received: true, duplicate: true });
  const response = await env.WORKSPACES.get(
    env.WORKSPACES.idFromName(workspace),
  ).fetch("https://workspace.internal/billing/reconcile", {
    method: "POST",
    body: JSON.stringify({ workspace }),
  });
  requireValue(
    response.ok,
    "BILLING_RECONCILIATION_PENDING",
    "Payment event is stored and awaits reconciliation.",
    503,
  );
  await env.IDENTITY.prepare(
    "UPDATE stripe_events SET completed_at=? WHERE id=?",
  )
    .bind(Date.now(), event.id)
    .run();
  return json({ received: true });
}
