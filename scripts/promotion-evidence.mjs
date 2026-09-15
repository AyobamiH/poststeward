import { isDeepStrictEqual } from "node:util";

export const OBSERVATION_MAX_AGE_MS = 24 * 60 * 60 * 1000;
export const OBSERVATION_CLOCK_SKEW_MS = 5 * 60 * 1000;
const shaPattern = /^[a-f0-9]{40}$/;
const states = new Set([
  "live_verified", "deployed", "implemented", "blocked_external",
  "unavailable_capability", "external_setup_required", "disabled_policy", "production_ready",
]);
const scopes = new Set(["restricted_staging", "advanced", "provider_optional", "production", "public_launch"]);
// Minimal stage contracts are not inferred from an incomplete caller-supplied ledger.
const requiredScopes = Object.freeze({
  owner_google_signin: "restricted_staging",
  threads_publication_readback: "restricted_staging",
  inspect_http_remote_mcp: "restricted_staging",
  stripe_sandbox_lifecycle: "restricted_staging",
  protected_root_cutover: "restricted_staging",
  private_github_authority: "restricted_staging",
  exact_recovery_checkpoints: "restricted_staging",
  advanced_rollout: "advanced",
  github_main_ruleset: "production",
  production_edge: "production",
  operational_alert_delivery: "production",
  capacity_cost_calibration: "production",
  hosted_cross_tenant: "production",
  public_signup: "public_launch",
});

export function exactOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.origin !== value || url.username || url.password)
    throw new Error("PostSteward origin must be an exact HTTPS origin without credentials.");
  return url.origin;
}

export function validateReviewedLedger(ledger) {
  if (ledger?.schemaVersion !== 1 || !Array.isArray(ledger.gates) || !ledger.gates.length)
    throw new Error("Reviewed release gate ledger is invalid or empty.");
  const result = new Map();
  for (const gate of ledger.gates) {
    if (!gate || !/^[a-z][a-z0-9_]{0,79}$/.test(gate.id || "") || result.has(gate.id) ||
        !states.has(gate.state) || !scopes.has(gate.scope) || typeof gate.blocking !== "boolean" ||
        !Array.isArray(gate.evidence) || gate.evidence.some((ref) => typeof ref !== "string" || !ref.trim()))
      throw new Error("Reviewed release gate metadata is malformed or duplicated.");
    result.set(gate.id, gate);
  }
  for (const [id, scope] of Object.entries(requiredScopes))
    if (result.get(id)?.scope !== scope)
      throw new Error(`Reviewed stage contract is missing or mis-scopes ${id}.`);
  return result;
}

export function assessReleaseBinding({ ledger, readiness, expectedRelease, target }) {
  const reviewed = validateReviewedLedger(ledger);
  const blockers = [];
  if (!shaPattern.test(expectedRelease || "")) blockers.push("expected_release_missing");
  if (!shaPattern.test(readiness?.release || "")) blockers.push("hosted_release_invalid");
  else if (readiness.release !== expectedRelease) blockers.push("hosted_release_mismatch");
  if (!Number.isInteger(readiness?.schemaVersion) || readiness.schemaVersion < 2)
    blockers.push("hosted_readiness_schema_invalid");
  try { exactOrigin(readiness?.origin); }
  catch { blockers.push("hosted_origin_invalid"); }
  const environment = ["production", "public_launch"].includes(target) ? "production" : "staging";
  if (readiness?.environment !== environment) blockers.push("target_environment_mismatch");
  if (environment === "production" && readiness?.origin) {
    try {
      if (new URL(readiness.origin).hostname.endsWith(".workers.dev"))
        blockers.push("production_origin_required");
    } catch { /* already classified above */ }
  }
  const hosted = readiness?.gates;
  if (!hosted || typeof hosted !== "object" || Array.isArray(hosted) ||
      !isDeepStrictEqual(hosted, Object.fromEntries(reviewed)))
    blockers.push("hosted_gate_ledger_mismatch");
  return { ready: blockers.length === 0, expectedRelease: expectedRelease || null, blockers };
}

export function observationTime(value) {
  if (typeof value === "number") return Number.isFinite(value) && value >= 0 ? value : NaN;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return NaN;
  return Date.parse(value);
}

// Evaluator outputs deliberately have different shapes. Normalise only the
// known verdict, never a ready flag supplied by an unevaluated input file.
export function evaluatedObservation(gate, raw, evaluation) {
  return {
    ...evaluation,
    evidenceClass: raw?.evidenceClass,
    release: raw?.release,
    environment: raw?.environment,
    ...(raw?.origin !== undefined ? { origin: raw.origin } : {}),
    observedAt: raw?.observedAt ?? raw?.cohort?.observedAt,
    ready: gate === "advanced_rollout" ? evaluation?.promotion?.ready === true : evaluation?.ready === true,
  };
}

export function assessObservation(evidence, readiness, now = Date.now()) {
  if (!Number.isFinite(now) || now < 0) throw new Error("Observation clock is invalid.");
  if (!evidence) return { ready: false, blockers: ["evidence_missing"] };
  const blockers = [];
  if (evidence.evidenceClass !== "hosted_observation") blockers.push("evidence_class_invalid");
  if (evidence.release !== readiness.release || !shaPattern.test(evidence.release || ""))
    blockers.push("evidence_release_mismatch");
  if (evidence.environment !== readiness.environment) blockers.push("evidence_environment_mismatch");
  if (evidence.origin !== undefined && evidence.origin !== readiness.origin)
    blockers.push("evidence_origin_mismatch");
  const observedAt = observationTime(evidence.observedAt);
  if (!Number.isFinite(observedAt)) blockers.push("evidence_timestamp_missing");
  else if (observedAt > now + OBSERVATION_CLOCK_SKEW_MS) blockers.push("evidence_from_future");
  else if (now - observedAt > OBSERVATION_MAX_AGE_MS) blockers.push("evidence_stale");
  if (evidence.ready !== true) blockers.push("evidence_checks_incomplete");
  return { ready: blockers.length === 0, blockers };
}

export function promotionExitCode(report, mode = "report") {
  if (!["report", "enforce"].includes(mode)) throw new Error("Promotion mode must be report or enforce.");
  if (!report.runtimePolicy.healthy) return 2;
  return mode === "enforce" && !report.promotion.ready ? 2 : 0;
}
