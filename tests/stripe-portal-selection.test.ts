import assert from "node:assert/strict";
import test from "node:test";
import { Billing } from "../src/billing.ts";
import { MemoryStore, environment, owner } from "./helpers.ts";

const portalConfig = (overrides: Record<string, unknown> = {}) => ({
  id: "bpc_poststeward_staging",
  active: true,
  livemode: false,
  default_return_url: environment.PUBLIC_ORIGIN + "/app",
  metadata: { application: "poststeward", environment: "staging" },
  features: {
    customer_update: { enabled: true, allowed_updates: ["email", "name"] },
    invoice_history: { enabled: true },
    payment_method_update: { enabled: true },
    subscription_cancel: {
      enabled: true,
      mode: "at_period_end",
      proration_behavior: "none",
    },
    subscription_update: { enabled: false },
  },
  ...overrides,
});

function sandboxHarness(configurations = [portalConfig()]) {
  const store = new MemoryStore();
  store.put("billing:customer", "cus_staging");
  const env = {
    ...environment,
    DEPLOY_ENV: "staging",
    SIGNUP_MODE: "restricted" as const,
    ADVANCED_ENABLED: "false",
    MPP_ENABLED: "false",
    STRIPE_SANDBOX_ENABLED: "true",
    STRIPE_SECRET_KEY: "rk_test_not_real",
    STRIPE_WEBHOOK_SECRET: "whsec_not_real",
    STRIPE_PRICE_ID: "price_test",
  };
  const calls: any[] = [];
  const client: any = {
    billingPortal: {
      configurations: {
        list: async (params: any) => {
          calls.push({ kind: "list", params });
          return { data: configurations };
        },
      },
      sessions: {
        create: async (params: any, options: any) => {
          calls.push({ kind: "session", params, options });
          return { url: "https://billing.stripe.com/test/session" };
        },
      },
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

test("staging sandbox pins the reviewed PostSteward portal configuration", async () => {
  const h = sandboxHarness();
  const result = await h.billing.portal({ idempotencyKey: "portal-test" }, owner);
  assert.equal(result.url, "https://billing.stripe.com/test/session");
  assert.deepEqual(h.calls[0], {
    kind: "list",
    params: { active: true, limit: 100 },
  });
  assert.equal(h.calls[1].params.customer, "cus_staging");
  assert.equal(
    h.calls[1].params.configuration,
    "bpc_poststeward_staging",
  );
  assert.equal(
    h.calls[1].options.idempotencyKey,
    "portal:" + owner.workspace + ":portal-test",
  );
});

test("staging sandbox refuses zero or multiple owned portal configurations", async () => {
  for (const configurations of [
    [],
    [portalConfig(), portalConfig({ id: "bpc_second" })],
  ]) {
    const h = sandboxHarness(configurations as any[]);
    await assert.rejects(
      h.billing.portal({ idempotencyKey: "portal-test" }, owner),
      { code: "PORTAL_CONFIGURATION_INVALID" },
    );
    assert.equal(h.calls.some((call) => call.kind === "session"), false);
  }
});

test("staging sandbox refuses live or weakened portal configuration", async () => {
  for (const configuration of [
    portalConfig({ livemode: true }),
    portalConfig({
      features: {
        ...portalConfig().features,
        subscription_cancel: {
          enabled: true,
          mode: "immediately",
          proration_behavior: "create_prorations",
        },
      },
    }),
  ]) {
    const h = sandboxHarness([configuration] as any[]);
    await assert.rejects(
      h.billing.portal({ idempotencyKey: "portal-test" }, owner),
      { code: "PORTAL_CONFIGURATION_INVALID" },
    );
    assert.equal(h.calls.some((call) => call.kind === "session"), false);
  }
});

test("non-sandbox billing keeps the account-default portal behaviour", async () => {
  const store = new MemoryStore();
  store.put("billing:customer", "cus_live_path");
  const env = {
    ...environment,
    ADVANCED_ENABLED: "true",
    STRIPE_SECRET_KEY: "sk_test_not_real",
    STRIPE_PRICE_ID: "price_test",
  };
  let listed = 0;
  let sessionParams: any;
  const client: any = {
    billingPortal: {
      configurations: {
        list: async () => {
          listed++;
          return { data: [] };
        },
      },
      sessions: {
        create: async (params: any) => {
          sessionParams = params;
          return { url: "https://billing.stripe.com/test/session" };
        },
      },
    },
  };
  const billing = new Billing(store, env, owner.workspace, client);
  await billing.portal({ idempotencyKey: "portal-test" }, owner);
  assert.equal(listed, 0);
  assert.equal(Object.hasOwn(sessionParams, "configuration"), false);
});
