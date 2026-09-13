import { sandboxBillingEnabled } from "./billing-mode.ts";
import { githubSourceConfiguration } from "./github-sources.ts";
import { oauthConfiguration } from "./provider-oauth.ts";
import type { Env } from "./types.ts";

export function releaseReadiness(env: Env) {
  const providers = oauthConfiguration(env);
  const github = githubSourceConfiguration(env);
  return {
    release: env.RELEASE_SHA,
    environment: env.DEPLOY_ENV || "unknown",
    access: {
      signupMode: env.SIGNUP_MODE,
      publicSignup: env.SIGNUP_MODE === "public",
    },
    providers: Object.fromEntries(
      Object.entries(providers).map(([provider, value]) => [
        provider,
        { oauth: value.available, readback: value.readback },
      ]),
    ),
    sources: {
      github: {
        privateRepositories: github.available,
        ownerOnly: github.ownerOnly,
        repositorySelection: github.repositorySelection,
        maxRepositories: github.maxRepositories,
      },
    },
    payments: {
      sandboxEnabled: sandboxBillingEnabled(env),
      advancedEnabled: env.ADVANCED_ENABLED === "true",
      mppEnabled: env.MPP_ENABLED === "true",
      checkoutConfigured: Boolean(env.STRIPE_SECRET_KEY && env.STRIPE_PRICE_ID),
      mppConfigured: Boolean(env.STRIPE_PROFILE_ID && env.MPP_SECRET),
    },
    recovery: {
      externalEffectLedger: true,
      ownerPitr: true,
      destructiveActions: "owner_only",
    },
    evidenceStillExternal: [
      "real_owner_consent",
      "real_provider_grant",
      "controlled_live_publication",
      "native_browser_webmcp",
      "real_pitr_rehearsal",
      "payment_settlement",
      "public_release",
    ],
  };
}
