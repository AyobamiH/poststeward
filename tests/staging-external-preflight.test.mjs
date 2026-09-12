import assert from "node:assert/strict";
import test from "node:test";
import {
  ensureStripePortalConfiguration,
  portalConfigurationBody,
  portalConfigurationMatches,
  stagingExternalPreflight,
} from "../scripts/staging-external-preflight.mjs";

const origin = "https://poststeward-staging.example";
const config = (extra = {}) => ({
  id: "bpc_test",
  active: true,
  livemode: false,
  default_return_url: origin + "/app",
  metadata: { application: "poststeward", environment: "staging" },
  business_profile: { headline: "Manage your PostSteward test subscription" },
  features: {
    customer_update: { enabled: true, allowed_updates: ["email", "name"] },
    invoice_history: { enabled: true },
    payment_method_update: { enabled: true },
    subscription_cancel: {
      enabled: true,
      mode: "at_period_end",
      proration_behavior: "none",
      cancellation_reason: { enabled: true, options: ["too_expensive", "other"] },
    },
    subscription_update: { enabled: false },
  },
  ...extra,
});

function response(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("reviewed portal configuration enables history, payment methods and safe cancellation", () => {
  const body = portalConfigurationBody(origin);
  assert.equal(body.get("features[invoice_history][enabled]"), "true");
  assert.equal(body.get("features[payment_method_update][enabled]"), "true");
  assert.equal(body.get("features[subscription_cancel][mode]"), "at_period_end");
  assert.equal(body.get("features[subscription_cancel][proration_behavior]"), "none");
  assert.equal(body.get("features[subscription_update][enabled]"), "false");
  assert.equal(body.get("metadata[application]"), "poststeward");
  assert.equal(body.get("metadata[environment]"), "staging");
  assert.equal(portalConfigurationMatches(config(), origin), true);
  assert.equal(
    portalConfigurationMatches(config({ livemode: true }), origin),
    false,
  );
});

test("portal setup is idempotent when reviewed configuration already exists", async () => {
  let calls = 0;
  const result = await ensureStripePortalConfiguration({
    secret: "rk_test_not_real",
    origin,
    send: async (_url, init) => {
      calls++;
      assert.match(new Headers(init.headers).get("authorization"), /^Bearer rk_test_/);
      return response({ data: [config()] });
    },
  });
  assert.equal(calls, 1);
  assert.equal(result.state, "present");
  assert.equal(result.created, false);
});

test("portal setup creates exactly one test configuration without logging key", async () => {
  const key = "rk_test_secretmaterial";
  const calls = [];
  const result = await ensureStripePortalConfiguration({
    secret: key,
    origin,
    send: async (url, init) => {
      calls.push({ url: String(url), init });
      if (init.method === "POST") {
        assert.equal(init.body.get("metadata[application]"), "poststeward");
        return response(config());
      }
      return response({ data: [] });
    },
  });
  assert.equal(calls.length, 2);
  assert.equal(result.state, "created");
  assert.equal(JSON.stringify(result).includes(key), false);
});

test("portal setup fails closed on ambiguous owned configurations", async () => {
  await assert.rejects(
    ensureStripePortalConfiguration({
      secret: "sk_test_not_real",
      origin,
      send: async () => response({ data: [config(), config({ id: "bpc_second" })] }),
    }),
    /Multiple active PostSteward staging portal configurations/,
  );
});

test("preflight separates setup authority from the runtime Stripe key and emits no secret", async () => {
  const operatorKey = "rk_test_operatorsecret";
  const runtimeKey = "rk_test_runtimesecret";
  let authorization;
  const report = await stagingExternalPreflight(
    {
      POSTSTEWARD_ORIGIN: origin,
      GITHUB_APP_CLIENT_ID: "Iv1.client",
      GITHUB_APP_SLUG: "poststeward-staging",
      HAS_GITHUB_APP_CLIENT_SECRET: "true",
      HAS_ENCRYPTION_KEY: "true",
      HAS_ENCRYPTION_KEY_NEXT: "false",
      STRIPE_SANDBOX_ENABLED: "false",
      STRIPE_SANDBOX_PRICE_ID: "price_test",
      HAS_STRIPE_SANDBOX_SECRET_KEY: "true",
      STRIPE_SANDBOX_OPERATOR_KEY: operatorKey,
      STRIPE_SANDBOX_SECRET_KEY: runtimeKey,
      HAS_STRIPE_SANDBOX_WEBHOOK_SECRET: "false",
    },
    async (_url, init) => {
      authorization = new Headers(init.headers).get("authorization");
      return response({ data: [config()] });
    },
  );
  assert.equal(authorization, `Bearer ${operatorKey}`);
  assert.equal(report.githubPrivateSources.configured, true);
  assert.equal(report.stripeSandbox.protectedRuntimeKeyPresent, true);
  assert.equal(report.stripeSandbox.protectedOperatorKeyPresent, true);
  assert.equal(report.stripeSandbox.portal.state, "present");
  assert.equal(JSON.stringify(report).includes(operatorKey), false);
  assert.equal(JSON.stringify(report).includes(runtimeKey), false);
});

test("missing operator key cannot consume a runtime key or mutate Stripe", async () => {
  let called = false;
  const report = await stagingExternalPreflight(
    {
      POSTSTEWARD_ORIGIN: origin,
      HAS_STRIPE_SANDBOX_SECRET_KEY: "true",
      STRIPE_SANDBOX_SECRET_KEY: "rk_test_runtime_should_not_be_used",
    },
    async () => {
      called = true;
      return response({ data: [] });
    },
  );
  assert.equal(called, false);
  assert.equal(report.stripeSandbox.protectedRuntimeKeyPresent, true);
  assert.equal(report.stripeSandbox.protectedOperatorKeyPresent, false);
  assert.equal(report.stripeSandbox.portal.state, "blocked_missing_protected_operator_key");
});
