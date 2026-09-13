import type { Env } from "./types.ts";

export function stripeCredentialAllowed(env: Env) {
  const key = env.STRIPE_SECRET_KEY || "";
  return /^(sk|rk)_(test|live)_/.test(key) &&
    (env.DEPLOY_ENV !== "staging" || /^(sk|rk)_test_/.test(key)) &&
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
