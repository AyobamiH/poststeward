import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const SLO_POLICY = Object.freeze({
  schemaVersion: 1,
  minimumCanaryWindowMs: 24 * 60 * 60 * 1000,
  minimumAdvancedOperations: 20,
  maximumCanaryBps: 1000,
  invariants: {
    duplicateExternalEffects: 0,
  },
  ratios: {
    verifiedPublication: { target: 0.99, minimumSamples: 20 },
    scheduleWithinFiveMinutes: { target: 0.99, minimumSamples: 20 },
    oauthRefreshSuccess: { target: 0.99, minimumSamples: 1 },
  },
  latency: {
    providerReconciliationP95Ms: { maximum: 5 * 60 * 1000, minimumSamples: 1 },
    webhookReconciliationP95Ms: { maximum: 2 * 60 * 1000, minimumSamples: 1 },
  },
  monitoredRecovery: {
    p95RtoMs: 5 * 60 * 1000,
    maxRpoMs: 6 * 60 * 60 * 1000,
  },
});

function demand(condition, message) {
  if (!condition) throw new Error(message);
}
function finiteNonNegative(value, name) {
  demand(Number.isFinite(value) && value >= 0, `${name} must be a finite non-negative number.`);
  return Number(value);
}
function integerNonNegative(value, name) {
  demand(Number.isInteger(value) && value >= 0, `${name} must be a non-negative integer.`);
  return Number(value);
}
function ratioCheck(name, good, total, policy) {
  good = integerNonNegative(good, `${name}.good`);
  total = integerNonNegative(total, `${name}.total`);
  demand(good <= total, `${name}.good cannot exceed total.`);
  const sufficient = total >= policy.minimumSamples;
  const ratio = total ? good / total : null;
  const bad = total - good;
  const allowedBad = total * (1 - policy.target);
  const budgetConsumed = total
    ? allowedBad === 0
      ? bad === 0
        ? 0
        : Infinity
      : bad / allowedBad
    : null;
  return {
    name,
    kind: "ratio",
    target: policy.target,
    minimumSamples: policy.minimumSamples,
    good,
    total,
    ratio,
    bad,
    allowedBad,
    budgetConsumed,
    sufficient,
    passed: sufficient && ratio >= policy.target,
  };
}
function latencyCheck(name, samples, p95Ms, policy) {
  samples = integerNonNegative(samples, `${name}.samples`);
  p95Ms = finiteNonNegative(p95Ms, `${name}.p95Ms`);
  const sufficient = samples >= policy.minimumSamples;
  return {
    name,
    kind: "latency",
    maximumMs: policy.maximum,
    minimumSamples: policy.minimumSamples,
    samples,
    p95Ms,
    sufficient,
    passed: sufficient && p95Ms <= policy.maximum,
  };
}
function positivePath(value, name) {
  value = integerNonNegative(value, name);
  return { value, passed: value > 0 };
}

export function evaluateSloObservation(observation) {
  demand(observation && typeof observation === "object" && !Array.isArray(observation), "Observation must be an object.");
  demand(observation.schemaVersion === 1, "Unsupported SLO observation schema.");
  demand(observation.evidenceClass === "hosted_observation", "Promotion evidence must be a hosted observation, not a fixture or estimate.");
  demand(observation.environment === "staging", "Advanced canary promotion evidence is currently staging-only.");
  demand(/^[a-f0-9]{40}$/.test(observation.release || ""), "Observation must identify the exact 40-character release SHA.");

  const cohort = observation.cohort || {};
  demand(cohort.mode === "canary", "Observation must come from an Advanced canary cohort.");
  const canaryBps = integerNonNegative(cohort.bps, "cohort.bps");
  demand(canaryBps >= 1 && canaryBps <= SLO_POLICY.maximumCanaryBps, "Canary cohort must remain between 1 and 1000 basis points.");
  const startedAt = finiteNonNegative(cohort.startedAt, "cohort.startedAt");
  const observedAt = finiteNonNegative(cohort.observedAt, "cohort.observedAt");
  demand(observedAt >= startedAt, "Canary observation window is inverted.");
  const windowMs = observedAt - startedAt;
  const workspaceCount = integerNonNegative(cohort.workspaceCount, "cohort.workspaceCount");
  const advancedOperations = integerNonNegative(cohort.advancedOperations, "cohort.advancedOperations");
  const cohortEvidence = {
    windowMs,
    workspaceCount,
    advancedOperations,
    minimumWindowMs: SLO_POLICY.minimumCanaryWindowMs,
    minimumAdvancedOperations: SLO_POLICY.minimumAdvancedOperations,
    passed:
      windowMs >= SLO_POLICY.minimumCanaryWindowMs &&
      workspaceCount >= 1 &&
      advancedOperations >= SLO_POLICY.minimumAdvancedOperations,
  };

  const duplicateExternalEffects = integerNonNegative(
    observation.invariants?.duplicateExternalEffects,
    "invariants.duplicateExternalEffects",
  );
  const invariant = {
    name: "duplicate_external_effects",
    expected: SLO_POLICY.invariants.duplicateExternalEffects,
    observed: duplicateExternalEffects,
    passed: duplicateExternalEffects === 0,
    severity: duplicateExternalEffects === 0 ? "none" : "page",
  };

  const ratios = [
    ratioCheck(
      "verified_publication",
      observation.publication?.verified,
      observation.publication?.eligible,
      SLO_POLICY.ratios.verifiedPublication,
    ),
    ratioCheck(
      "schedule_within_five_minutes",
      observation.schedule?.withinFiveMinutes,
      observation.schedule?.due,
      SLO_POLICY.ratios.scheduleWithinFiveMinutes,
    ),
    ratioCheck(
      "oauth_refresh_success",
      observation.oauthRefresh?.succeeded,
      observation.oauthRefresh?.attempts,
      SLO_POLICY.ratios.oauthRefreshSuccess,
    ),
  ];

  const latency = [
    latencyCheck(
      "provider_reconciliation_p95",
      observation.providerReconciliation?.samples,
      observation.providerReconciliation?.p95Ms,
      SLO_POLICY.latency.providerReconciliationP95Ms,
    ),
    latencyCheck(
      "webhook_reconciliation_p95",
      observation.webhookReconciliation?.samples,
      observation.webhookReconciliation?.p95Ms,
      SLO_POLICY.latency.webhookReconciliationP95Ms,
    ),
  ];

  const productPath = Object.fromEntries(
    [
      ["sourceChanges", "advanced.productPath.sourceChanges"],
      ["inventorySnapshots", "advanced.productPath.inventorySnapshots"],
      ["spacedAllocations", "advanced.productPath.spacedAllocations"],
      ["providerEffects", "advanced.productPath.providerEffects"],
      ["verifiedReadbacks", "advanced.productPath.verifiedReadbacks"],
      ["scheduledMetricsCaptures", "advanced.productPath.scheduledMetricsCaptures"],
    ].map(([field, name]) => [
      field,
      positivePath(observation.advanced?.productPath?.[field], name),
    ]),
  );
  const productPathPassed = Object.values(productPath).every((entry) => entry.passed);

  let recovery = { observed: false, passed: null };
  if ((observation.recovery?.samples || 0) > 0) {
    const samples = integerNonNegative(observation.recovery.samples, "recovery.samples");
    const p95RtoMs = finiteNonNegative(observation.recovery.p95RtoMs, "recovery.p95RtoMs");
    const maxRpoMs = finiteNonNegative(observation.recovery.maxRpoMs, "recovery.maxRpoMs");
    recovery = {
      observed: true,
      samples,
      p95RtoMs,
      maxRpoMs,
      maximumP95RtoMs: SLO_POLICY.monitoredRecovery.p95RtoMs,
      maximumRpoMs: SLO_POLICY.monitoredRecovery.maxRpoMs,
      passed:
        p95RtoMs <= SLO_POLICY.monitoredRecovery.p95RtoMs &&
        maxRpoMs <= SLO_POLICY.monitoredRecovery.maxRpoMs,
    };
  }

  const blockers = [];
  if (!cohortEvidence.passed) blockers.push("insufficient_canary_exposure");
  if (!invariant.passed) blockers.push("duplicate_external_effect_invariant");
  for (const check of ratios)
    if (!check.sufficient) blockers.push(`${check.name}_insufficient_samples`);
    else if (!check.passed) blockers.push(`${check.name}_error_budget_exhausted`);
  for (const check of latency)
    if (!check.sufficient) blockers.push(`${check.name}_insufficient_samples`);
    else if (!check.passed) blockers.push(`${check.name}_latency_budget_exceeded`);
  if (!productPathPassed) blockers.push("advanced_product_path_incomplete");

  return {
    schemaVersion: SLO_POLICY.schemaVersion,
    release: observation.release,
    environment: observation.environment,
    observedAt,
    cohort: { ...cohortEvidence, bps: canaryBps },
    invariant,
    ratios,
    latency,
    advancedProductPath: { ...productPath, passed: productPathPassed },
    recovery,
    promotion: {
      ready: blockers.length === 0,
      blockers,
      note:
        "This report is necessary evidence for promotion, not authority to enable global rollout. A reviewed release-gate change is still required.",
    },
  };
}

export function readObservation(path) {
  demand(path, "Pass one SLO observation JSON file.");
  return JSON.parse(readFileSync(path, "utf8"));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const report = evaluateSloObservation(readObservation(process.argv[2]));
    console.log("POSTSTEWARD_SLO_REPORT " + JSON.stringify(report));
    if (!report.promotion.ready) process.exitCode = 2;
  } catch (error) {
    console.error(
      "POSTSTEWARD_SLO_INVALID " +
        JSON.stringify({ message: error instanceof Error ? error.message : "Invalid observation." }),
    );
    process.exitCode = 1;
  }
}
