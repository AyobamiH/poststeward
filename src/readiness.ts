import { sandboxBillingEnabled } from "./billing-mode.ts";
import { githubSourceConfiguration } from "./github-sources.ts";
import { oauthConfiguration } from "./provider-oauth.ts";
import {
  externalEvidenceGateIds,
  releaseGateMap,
  releasePolicyViolations,
  runtimeCapabilitySnapshot,
} from "./release-gates.ts";
import type { Env } from "./types.ts";

export function releaseReadiness(env: Env) {
  const providers = oauthConfiguration(env);
  const github = githubSourceConfiguration(env);
  const runtime = runtimeCapabilitySnapshot(env);
  const violations = releasePolicyViolations(env);
  return {
    schemaVersion: 2,
    release: env.RELEASE_SHA,
    environment: env.DEPLOY_ENV || "unknown",
    access: {
      signupMode: env.SIGNUP_MODE,
      publicSignup: env.SIGNUP_MODE === "public",
    },
    providers: Object.fromEntries(
      Object.entries(providers).map(([provider, value]) => [
        provider,
        {
          oauth: value.available,
          readback: value.readback,
          requiredScopes: value.requiredScopes,
          optionalScopes: value.optionalScopes,
          capabilities: value.capabilities,
        },
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
      exactCheckpoints: true,
      approximateTimestampPath: true,
      destructiveActions: "owner_only",
    },
    runtimeCapabilities: runtime,
    gates: releaseGateMap(),
    policy: {
      healthy: violations.length === 0,
      violations,
    },
    // Kept for backwards-compatible clients, but now derived from the same
    // typed gate model rather than independently maintained prose.
    evidenceStillExternal: externalEvidenceGateIds(),
  };
}
