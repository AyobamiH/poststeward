import type { Env } from "./types.ts";

export type ReleaseGateState =
  | "live_verified"
  | "deployed"
  | "implemented"
  | "blocked_external"
  | "unavailable_capability"
  | "external_setup_required"
  | "disabled_policy"
  | "production_ready";

export type ReleaseGateScope =
  | "restricted_staging"
  | "advanced"
  | "provider_optional"
  | "production"
  | "public_launch";

export interface ReleaseGateDefinition {
  id: string;
  state: ReleaseGateState;
  scope: ReleaseGateScope;
  blocking: boolean;
  summary: string;
  evidence: string[];
  ownerAction?: string;
}

// This is the reviewed evidence ledger, not a deduction from code/configuration.
// A gate may move forward only through a reviewed change that cites the real
// external evidence. Runtime configuration is reported separately below.
export const releaseGateDefinitions: readonly ReleaseGateDefinition[] = [
  {
    id: "owner_google_signin",
    state: "live_verified",
    scope: "restricted_staging",
    blocking: false,
    summary: "Real owner Google sign-in accepted in the owner browser.",
    evidence: ["docs/p0-live-evidence-2026-09-12.md"],
  },
  {
    id: "threads_publication_readback",
    state: "live_verified",
    scope: "restricted_staging",
    blocking: false,
    summary: "Controlled Threads publication and independent provider readback accepted.",
    evidence: ["docs/p0-live-evidence-2026-09-12.md"],
  },
  {
    id: "inspect_http_remote_mcp",
    state: "live_verified",
    scope: "restricted_staging",
    blocking: false,
    summary: "Inspect-only HTTP and remote MCP accepted before revoke and denied after revoke.",
    evidence: ["docs/p0-live-evidence-2026-09-12.md"],
  },
  {
    id: "stripe_sandbox_lifecycle",
    state: "live_verified",
    scope: "restricted_staging",
    blocking: false,
    summary: "Sandbox Checkout, paid state, refund/revocation, cancellation and webhook ledger accepted.",
    evidence: ["docs/live-external-gates-2026-09-13.md"],
  },
  {
    id: "protected_root_cutover",
    state: "live_verified",
    scope: "restricted_staging",
    blocking: false,
    summary: "Protected next-root writer and complete readback traversal accepted; legacy root intentionally retained.",
    evidence: ["docs/protected-root-cutover.md", "docs/live-external-gates-2026-09-13.md"],
  },
  {
    id: "private_github_authority",
    state: "live_verified",
    scope: "restricted_staging",
    blocking: false,
    summary: "Selected private repository read and provider-side revoke/fail-closed proof accepted.",
    evidence: ["docs/live-external-gates-2026-09-13.md"],
  },
  {
    id: "exact_recovery_checkpoints",
    state: "live_verified",
    scope: "restricted_staging",
    blocking: false,
    summary: "Exact checkpoint capture, real restore, reconciliation, resume and disposable erasure are accepted on restricted staging.",
    evidence: [
      "docs/recovery.md",
      "docs/exact-recovery-live-evidence-2026-09-14.md",
      "actions/34890295276",
    ],
  },
  {
    id: "approximate_timestamp_pitr",
    state: "blocked_external",
    scope: "restricted_staging",
    blocking: false,
    summary: "Cloudflare hosted timestamp-to-bookmark resolution is failing; exact checkpoints do not depend on it.",
    evidence: ["docs/live-external-gates-2026-09-13.md"],
  },
  {
    id: "threads_oauth_callback",
    state: "blocked_external",
    scope: "restricted_staging",
    blocking: false,
    summary: "Meta currently rejects persistence of the exact Threads callback allowlist.",
    evidence: ["docs/live-external-gates-2026-09-13.md"],
  },
  {
    id: "native_webmcp",
    state: "unavailable_capability",
    scope: "restricted_staging",
    blocking: false,
    summary: "PostSteward native WebMCP is deployed; the owner's ordinary browser does not expose the native API.",
    evidence: ["docs/live-external-gates-2026-09-13.md"],
    ownerAction: "Use one authenticated supporting browser for the read-only workspace_status proof when available.",
  },
  {
    id: "x_oauth",
    state: "external_setup_required",
    scope: "provider_optional",
    blocking: false,
    summary: "X OAuth implementation is present; the real application/client authority is not configured in staging.",
    evidence: ["docs/live-external-gates-2026-09-13.md"],
  },
  {
    id: "linkedin_poststeward_page_identity",
    state: "live_verified",
    scope: "provider_optional",
    blocking: false,
    summary: "PostSteward LinkedIn Page identity is independently verified as urn:li:organization:146607525; OAuth authority remains separate.",
    evidence: ["docs/live-external-gates-2026-09-13.md"],
  },
  {
    id: "linkedin_oauth",
    state: "external_setup_required",
    scope: "provider_optional",
    blocking: false,
    summary: "LinkedIn OAuth implementation is present; the real application/client authority is not configured in staging.",
    evidence: ["docs/live-external-gates-2026-09-13.md"],
  },
  {
    id: "linkedin_member_readback",
    state: "external_setup_required",
    scope: "provider_optional",
    blocking: false,
    summary: "Independent member-post readback remains unavailable until the actual LinkedIn application receives the restricted permission.",
    evidence: ["docs/live-external-gates-2026-09-13.md"],
  },
  {
    id: "advanced_rollout",
    state: "disabled_policy",
    scope: "advanced",
    blocking: false,
    summary: "Advanced execution remains deliberately disabled until canary product acceptance and SLO gates pass.",
    evidence: ["docs/production-readiness-acceptance.md"],
  },
  {
    id: "mpp",
    state: "disabled_policy",
    scope: "advanced",
    blocking: false,
    summary: "MPP is a separate optional settlement stream and remains disabled.",
    evidence: ["docs/production-readiness-acceptance.md"],
  },
  {
    id: "public_signup",
    state: "disabled_policy",
    scope: "public_launch",
    blocking: true,
    summary: "Public signup remains restricted until production/support/abuse controls are accepted.",
    evidence: ["docs/production-readiness-acceptance.md"],
  },
  {
    id: "production_edge",
    state: "external_setup_required",
    scope: "production",
    blocking: true,
    summary: "Custom production origin, DNS/TLS, WAF and rate-policy evidence remain open.",
    evidence: ["docs/production-readiness-acceptance.md"],
  },
  {
    id: "github_main_ruleset",
    state: "live_verified",
    scope: "production",
    blocking: false,
    summary: "Server-side main protection is active and independently read back from GitHub with the reviewed solo-maintainer policy.",
    evidence: [
      "docs/github-main-ruleset-live-evidence-2026-09-15.md",
      "ruleset/23461973",
    ],
  },
  {
    id: "operational_alert_delivery",
    state: "external_setup_required",
    scope: "production",
    blocking: true,
    summary: "Production alert delivery and escalation evidence remain open.",
    evidence: ["docs/production-readiness-acceptance.md"],
  },
  {
    id: "capacity_cost_calibration",
    state: "external_setup_required",
    scope: "production",
    blocking: true,
    summary: "Representative capacity/cost observations with required headroom remain open.",
    evidence: ["docs/production-readiness-acceptance.md"],
  },
  {
    id: "hosted_cross_tenant",
    state: "live_verified",
    scope: "production",
    blocking: false,
    summary: "Hosted two-workspace isolation, hostile-Origin denial and ephemeral grant cleanup are accepted on the exact staged release.",
    evidence: [
      "docs/hosted-cross-tenant-live-evidence-2026-09-15.md",
      "actions/35029954430",
    ],
  },
] as const;

export function releaseGateMap() {
  return Object.fromEntries(
    releaseGateDefinitions.map((gate) => [gate.id, { ...gate }]),
  );
}

function rolloutMode(env: Env) {
  return ["disabled", "canary", "global"].includes(
    env.ADVANCED_ROLLOUT_MODE || "disabled",
  )
    ? (env.ADVANCED_ROLLOUT_MODE || "disabled")
    : "invalid";
}

function rolloutBps(env: Env) {
  const value = Number(env.ADVANCED_CANARY_BPS || "0");
  return Number.isInteger(value) && value >= 0 && value <= 10000 ? value : -1;
}

function reviewedGateAccepted(id: string) {
  const state = releaseGateDefinitions.find((gate) => gate.id === id)?.state;
  return state === "live_verified" || state === "production_ready";
}

export function runtimeCapabilitySnapshot(env: Env) {
  return {
    providerApplications: {
      x: Boolean(env.X_OAUTH_CLIENT_ID && env.X_OAUTH_CLIENT_SECRET),
      threads: Boolean(env.THREADS_OAUTH_CLIENT_ID && env.THREADS_OAUTH_CLIENT_SECRET),
      linkedin: Boolean(env.LINKEDIN_OAUTH_CLIENT_ID && env.LINKEDIN_OAUTH_CLIENT_SECRET),
      linkedinMemberReadback: env.LINKEDIN_MEMBER_READBACK === "true",
    },
    privateGitHubConfigured: Boolean(
      env.GITHUB_APP_CLIENT_ID && env.GITHUB_APP_CLIENT_SECRET && env.GITHUB_APP_SLUG,
    ),
    stripeSandboxConfigured: Boolean(
      env.STRIPE_SANDBOX_ENABLED === "true" &&
        env.STRIPE_SECRET_KEY &&
        env.STRIPE_PRICE_ID &&
        env.STRIPE_WEBHOOK_SECRET,
    ),
    operationalAlertWebhookConfigured: Boolean(env.OPERATIONAL_ALERT_WEBHOOK_URL),
    policies: {
      signupMode: env.SIGNUP_MODE,
      advancedEnabled: env.ADVANCED_ENABLED === "true",
      advancedRolloutMode: rolloutMode(env),
      advancedCanaryBps: rolloutBps(env),
      advancedCanarySeedConfigured: Boolean(
        env.ADVANCED_CANARY_SEED && env.ADVANCED_CANARY_SEED.length >= 8,
      ),
      mppEnabled: env.MPP_ENABLED === "true",
      encryptionRootWrite: env.ENCRYPTION_ROOT_WRITE === "next" ? "next" : "legacy",
    },
  };
}

export function releasePolicyViolations(env: Env) {
  const runtime = runtimeCapabilitySnapshot(env);
  const violations: string[] = [];
  if (
    runtime.policies.signupMode === "public" &&
    !reviewedGateAccepted("public_signup")
  )
    violations.push("public_signup_enabled_before_public_launch_gate");

  const advanced = runtime.policies.advancedEnabled;
  const mode = runtime.policies.advancedRolloutMode;
  const bps = runtime.policies.advancedCanaryBps;
  if (!advanced) {
    if (mode !== "disabled" || bps !== 0)
      violations.push("advanced_disabled_policy_drift");
  } else if (mode === "global") {
    if (!reviewedGateAccepted("advanced_rollout"))
      violations.push("advanced_globally_enabled_before_canary_gate");
  } else if (
    env.DEPLOY_ENV !== "staging" ||
    mode !== "canary" ||
    bps < 1 ||
    bps > 1000 ||
    !runtime.policies.advancedCanarySeedConfigured
  ) {
    violations.push("advanced_canary_policy_invalid");
  }

  if (runtime.policies.mppEnabled && !reviewedGateAccepted("mpp"))
    violations.push("mpp_enabled_before_settlement_gate");
  if (
    runtime.providerApplications.linkedinMemberReadback &&
    !reviewedGateAccepted("linkedin_member_readback")
  )
    violations.push("linkedin_readback_enabled_without_live_permission_evidence");
  return violations;
}

export function externalEvidenceGateIds() {
  return releaseGateDefinitions
    .filter((gate) =>
      [
        "blocked_external",
        "unavailable_capability",
        "external_setup_required",
        "disabled_policy",
      ].includes(gate.state),
    )
    .map((gate) => gate.id);
}
