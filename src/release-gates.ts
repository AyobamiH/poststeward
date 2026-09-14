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
  | "production";

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
    scope: "production",
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
    state: "external_setup_required",
    scope: "production",
    blocking: true,
    summary: "Required server-side main ruleset is not yet evidenced; merged-PR provenance is not a substitute.",
    evidence: ["docs/production-readiness-acceptance.md"],
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
    state: "external_setup_required",
    scope: "production",
    blocking: true,
    summary: "Two-workspace hosted isolation acceptance remains open.",
    evidence: ["docs/production-readiness-acceptance.md"],
  },
] as const;

export function releaseGateMap() {
  return Object.fromEntries(
    releaseGateDefinitions.map((gate) => [gate.id, { ...gate }]),
  );
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
    policies: {
      signupMode: env.SIGNUP_MODE,
      advancedEnabled: env.ADVANCED_ENABLED === "true",
      mppEnabled: env.MPP_ENABLED === "true",
      encryptionRootWrite: env.ENCRYPTION_ROOT_WRITE === "next" ? "next" : "legacy",
    },
  };
}

export function releasePolicyViolations(env: Env) {
  const runtime = runtimeCapabilitySnapshot(env);
  const violations: string[] = [];
  if (runtime.policies.signupMode === "public")
    violations.push("public_signup_enabled_before_production_gate");
  if (runtime.policies.advancedEnabled)
    violations.push("advanced_globally_enabled_before_canary_gate");
  if (runtime.policies.mppEnabled)
    violations.push("mpp_enabled_before_settlement_gate");
  if (
    runtime.providerApplications.linkedinMemberReadback &&
    releaseGateDefinitions.find((gate) => gate.id === "linkedin_member_readback")?.state !==
      "live_verified"
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
