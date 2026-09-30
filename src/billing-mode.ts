import type { Env } from "./types.ts";

export function stripeCredentialAllowed(env: Env) {
  const key = env.STRIPE_SECRET_KEY || "";
  return /^(sk|rk)_(test|live)_/.test(key) &&
    (env.DEPLOY_ENV !== "staging" || /^(sk|rk)_test_/.test(key)) &&
    (env.DEPLOY_ENV !== "production" || /^(sk|rk)_live_/.test(key)) &&
    (env.STRIPE_SANDBOX_ENABLED !== "true" ||
      (env.DEPLOY_ENV === "staging" && /^(sk|rk)_test_/.test(key)));
}

export function sandboxBillingEnabled(env: Env) {
  return env.STRIPE_SANDBOX_ENABLED === "true" &&
    env.DEPLOY_ENV === "staging" && env.SIGNUP_MODE === "restricted" &&
    env.ADVANCED_ENABLED === "false" && env.MPP_ENABLED === "false" &&
    stripeCredentialAllowed(env) &&
    Boolean(env.STRIPE_PRICE_ID && env.STRIPE_WEBHOOK_SECRET);
}

/** Public GBP launch price has no default; historical staging sandbox remains USD 5. */
export function billingPrice(env: Pick<Env, "STRIPE_SANDBOX_ENABLED" | "DEPLOY_ENV" | "ADVANCED_PRICE_AMOUNT_PENCE" | "STRIPE_PRICE_ID">) {
  const sandbox = env.DEPLOY_ENV === "staging" && env.STRIPE_SANDBOX_ENABLED === "true";
  const raw = env.ADVANCED_PRICE_AMOUNT_PENCE || "";
  const amount = sandbox ? 500 : /^[1-9][0-9]{0,6}$/.test(raw) ? Number(raw) : null;
  return { amount, currency: sandbox ? "usd" : "gbp", interval: "month", configured: amount !== null && Boolean(env.STRIPE_PRICE_ID) };
}
export function billingConfigured(env: Env) {
  return billingPrice(env).configured && stripeCredentialAllowed(env) &&
    Boolean(env.STRIPE_SECRET_KEY && env.STRIPE_WEBHOOK_SECRET);
}
