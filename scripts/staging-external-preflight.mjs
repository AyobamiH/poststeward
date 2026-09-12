import { pathToFileURL } from "node:url";

const stripeApi = "https://api.stripe.com/v1";
const portalApplication = "poststeward";
const portalEnvironment = "staging";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

export function portalConfigurationMatches(config, origin) {
  const allowed = new Set(config?.features?.customer_update?.allowed_updates || []);
  return Boolean(
    config?.id &&
      config.active === true &&
      config.livemode === false &&
      config.metadata?.application === portalApplication &&
      config.metadata?.environment === portalEnvironment &&
      config.default_return_url === `${origin}/app` &&
      config.features?.customer_update?.enabled === true &&
      allowed.has("email") &&
      allowed.has("name") &&
      config.features?.invoice_history?.enabled === true &&
      config.features?.payment_method_update?.enabled === true &&
      config.features?.subscription_cancel?.enabled === true &&
      config.features?.subscription_cancel?.mode === "at_period_end" &&
      config.features?.subscription_cancel?.proration_behavior === "none" &&
      config.features?.subscription_update?.enabled === false,
  );
}

export function portalConfigurationBody(origin) {
  const body = new URLSearchParams();
  const values = {
    "business_profile[headline]": "Manage your PostSteward test subscription",
    default_return_url: `${origin}/app`,
    "features[customer_update][enabled]": "true",
    "features[customer_update][allowed_updates][0]": "email",
    "features[customer_update][allowed_updates][1]": "name",
    "features[invoice_history][enabled]": "true",
    "features[payment_method_update][enabled]": "true",
    "features[subscription_cancel][enabled]": "true",
    "features[subscription_cancel][mode]": "at_period_end",
    "features[subscription_cancel][proration_behavior]": "none",
    "features[subscription_cancel][cancellation_reason][enabled]": "true",
    "features[subscription_cancel][cancellation_reason][options][0]": "too_expensive",
    "features[subscription_cancel][cancellation_reason][options][1]": "missing_features",
    "features[subscription_cancel][cancellation_reason][options][2]": "switched_service",
    "features[subscription_cancel][cancellation_reason][options][3]": "unused",
    "features[subscription_cancel][cancellation_reason][options][4]": "other",
    "features[subscription_update][enabled]": "false",
    "metadata[application]": portalApplication,
    "metadata[environment]": portalEnvironment,
  };
  for (const [key, value] of Object.entries(values)) body.set(key, value);
  return body;
}

async function stripeJson(url, secret, init = {}, send = fetch) {
  demand(/^(sk|rk)_test_[A-Za-z0-9_]+$/.test(secret || ""),
    "Stripe staging operator key must be a test-mode secret or restricted key.");
  const response = await send(url, {
    ...init,
    headers: {
      Authorization: `Bearer ${secret}`,
      ...(init.body ? { "Content-Type": "application/x-www-form-urlencoded" } : {}),
      ...(init.headers || {}),
    },
    redirect: "error",
    signal: AbortSignal.timeout(15000),
  });
  const data = await response.json().catch(() => null);
  demand(response.ok && data && !data.error,
    `Stripe staging configuration request failed (HTTP ${response.status}).`);
  return data;
}

export async function ensureStripePortalConfiguration({ secret, origin, send = fetch }) {
  if (!secret)
    return {
      state: "blocked_missing_protected_operator_key",
      created: false,
      active: false,
      livemode: false,
    };
  const list = await stripeJson(
    `${stripeApi}/billing_portal/configurations?active=true&limit=100`,
    secret,
    {},
    send,
  );
  const owned = (list.data || []).filter(
    (config) =>
      config?.metadata?.application === portalApplication &&
      config?.metadata?.environment === portalEnvironment,
  );
  demand(owned.length <= 1,
    "Multiple active PostSteward staging portal configurations exist; refuse ambiguous mutation.");
  if (owned.length === 1 && portalConfigurationMatches(owned[0], origin))
    return {
      state: "present",
      created: false,
      active: true,
      livemode: false,
      configuration: owned[0].id,
    };

  const endpoint = owned.length
    ? `${stripeApi}/billing_portal/configurations/${encodeURIComponent(owned[0].id)}`
    : `${stripeApi}/billing_portal/configurations`;
  const configured = await stripeJson(
    endpoint,
    secret,
    { method: "POST", body: portalConfigurationBody(origin) },
    send,
  );
  demand(portalConfigurationMatches(configured, origin),
    "Stripe returned a portal configuration that does not match the reviewed staging contract.");
  return {
    state: owned.length ? "updated" : "created",
    created: owned.length === 0,
    active: true,
    livemode: false,
    configuration: configured.id,
  };
}

export async function stagingExternalPreflight(env, send = fetch) {
  const origin = env.POSTSTEWARD_ORIGIN || "https://poststeward-staging.woeinvests.workers.dev";
  const portal = await ensureStripePortalConfiguration({
    secret: env.STRIPE_SANDBOX_OPERATOR_KEY || "",
    origin,
    send,
  });
  return {
    observedAt: new Date().toISOString(),
    origin,
    githubPrivateSources: {
      publicConfigurationPresent: Boolean(env.GITHUB_APP_CLIENT_ID && env.GITHUB_APP_SLUG),
      protectedClientSecretPresent: env.HAS_GITHUB_APP_CLIENT_SECRET === "true",
      configured:
        Boolean(env.GITHUB_APP_CLIENT_ID && env.GITHUB_APP_SLUG) &&
        env.HAS_GITHUB_APP_CLIENT_SECRET === "true",
    },
    rootSecret: {
      currentPresent: env.HAS_ENCRYPTION_KEY === "true",
      nextPresent: env.HAS_ENCRYPTION_KEY_NEXT === "true",
    },
    stripeSandbox: {
      enabledVariable: env.STRIPE_SANDBOX_ENABLED === "true",
      priceVariablePresent: /^price_[A-Za-z0-9_]+$/.test(env.STRIPE_SANDBOX_PRICE_ID || ""),
      protectedRuntimeKeyPresent: env.HAS_STRIPE_SANDBOX_SECRET_KEY === "true",
      protectedOperatorKeyPresent: Boolean(env.STRIPE_SANDBOX_OPERATOR_KEY),
      protectedWebhookSecretPresent: env.HAS_STRIPE_SANDBOX_WEBHOOK_SECRET === "true",
      portal,
    },
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const report = await stagingExternalPreflight(process.env);
  const serialized = JSON.stringify(report);
  for (const secret of [
    process.env.STRIPE_SANDBOX_OPERATOR_KEY,
    process.env.ENCRYPTION_KEY,
    process.env.ENCRYPTION_KEY_NEXT,
    process.env.GITHUB_APP_CLIENT_SECRET,
    process.env.STRIPE_SANDBOX_SECRET_KEY,
    process.env.STRIPE_SANDBOX_WEBHOOK_SECRET,
  ].filter(Boolean))
    demand(!serialized.includes(secret), "External-gate preflight attempted to emit protected material.");
  console.log("POSTSTEWARD_EXTERNAL_GATE_PREFLIGHT " + serialized);
}
