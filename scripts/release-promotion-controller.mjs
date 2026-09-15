import { existsSync, readFileSync, appendFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { evaluateCapacity } from "./capacity-calibration.mjs";
import { evaluateSloObservation } from "./slo-evaluate.mjs";
import { evaluateAlertEvidence } from "./operational-alert-evidence.mjs";
import { inspectProductionEdge } from "./production-edge-check.mjs";
import { checkCrossTenantIsolation } from "./hosted-acceptance.mjs";

const acceptedStates = new Set(["live_verified", "production_ready"]);
const targets = new Set([
  "restricted_staging",
  "advanced_canary",
  "production",
  "public_launch",
]);

const actionCatalog = Object.freeze({
  threads_oauth_callback: {
    class: "external_wait",
    action: "wait_for_meta_callback_persistence",
  },
  native_webmcp: {
    class: "owner_browser",
    action: "run_authenticated_native_webmcp_when_supported",
  },
  x_oauth: {
    class: "owner_provider",
    action: "configure_or_authorize_x",
  },
  linkedin_oauth: {
    class: "owner_provider",
    action: "configure_or_authorize_linkedin",
  },
  linkedin_member_readback: {
    class: "external_permission",
    action: "obtain_linkedin_member_readback_permission_if_available",
  },
  advanced_rollout: {
    class: "controlled_rollout",
    action: "start_or_observe_bounded_advanced_canary",
  },
  production_edge: {
    class: "production_infrastructure",
    action: "configure_or_verify_production_edge",
  },
  operational_alert_delivery: {
    class: "operations",
    action: "configure_and_exercise_alert_delivery",
  },
  capacity_cost_calibration: {
    class: "operations",
    action: "collect_capacity_and_cost_observation",
  },
  hosted_cross_tenant: {
    class: "security_acceptance",
    action: "run_two_workspace_hosted_isolation",
  },
  public_signup: {
    class: "launch_policy",
    action: "approve_public_admission_only_after_production_ready",
  },
});

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function readJson(path, label) {
  demand(path && existsSync(path), `${label} file does not exist.`);
  return JSON.parse(readFileSync(path, "utf8"));
}

function exactOrigin(value) {
  const parsed = new URL(value);
  demand(
    parsed.protocol === "https:" &&
      parsed.origin === value &&
      !parsed.username &&
      !parsed.password,
    "PostSteward origin must be an exact HTTPS origin without credentials.",
  );
  return parsed.origin;
}

async function fetchReadiness(origin, send = fetch) {
  const response = await send(`${origin}/readiness.json`, {
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(20_000),
  });
  demand(response.status === 200, `Hosted readiness returned HTTP ${response.status}.`);
  const body = await response.json();
  demand(
    body?.schemaVersion >= 2 && /^[a-f0-9]{40}$/.test(body?.release || ""),
    "Hosted readiness did not identify a reviewed release.",
  );
  demand(
    body?.policy && typeof body.policy.healthy === "boolean",
    "Hosted readiness omitted release policy health.",
  );
  return body;
}

function reviewedGateMap(ledger) {
  demand(
    ledger?.schemaVersion === 1 && Array.isArray(ledger.gates),
    "Reviewed release gate ledger is invalid.",
  );
  const map = new Map();
  for (const gate of ledger.gates) {
    demand(gate?.id && !map.has(gate.id), "Release gate ids must be unique.");
    map.set(gate.id, gate);
  }
  return map;
}

function requiredForTarget(gate, target) {
  if (acceptedStates.has(gate.state)) return false;
  if (target === "restricted_staging")
    return gate.scope === "restricted_staging" && gate.blocking === true;
  if (target === "advanced_canary")
    return gate.id === "advanced_rollout";
  if (target === "production")
    return gate.scope === "production" && gate.blocking === true;
  return (
    (gate.scope === "production" && gate.blocking === true) ||
    gate.scope === "public_launch"
  );
}

function providerAction(gateId, readiness) {
  if (gateId === "x_oauth")
    return readiness?.providers?.x?.oauth
      ? "complete_owner_x_oauth_grant_and_record_identity"
      : "register_x_application_and_store_protected_client_authority";
  if (gateId === "linkedin_oauth")
    return readiness?.providers?.linkedin?.oauth
      ? "complete_owner_linkedin_oauth_grant_and_record_identity"
      : "register_linkedin_application_and_store_protected_client_authority";
  return undefined;
}

function evidenceForGate(gateId, observations) {
  return observations[gateId] || null;
}

function nextActionForGate(gate, readiness, observations) {
  if (acceptedStates.has(gate.state)) return null;
  const evidence = evidenceForGate(gate.id, observations);
  if (evidence?.ready === true)
    return {
      gate: gate.id,
      class: "review_evidence",
      action: "review_and_advance_gate_from_live_evidence",
      evidenceReady: true,
    };

  const provider = providerAction(gate.id, readiness);
  const configured = actionCatalog[gate.id] || {
    class: "review",
    action: "review_gate_evidence",
  };
  return {
    gate: gate.id,
    class: configured.class,
    action: provider || configured.action,
    evidenceReady: false,
    reviewedState: gate.state,
  };
}

function capacityObservation(observation) {
  const result = evaluateCapacity(observation);
  const costs = observation.costEvidence || {};
  const costEvidence = {
    cloudflareObserved: costs.cloudflareObserved === true,
    providerQuotaObserved: costs.providerQuotaObserved === true,
    alarmWebhookVolumeObserved: costs.alarmWebhookVolumeObserved === true,
    estimateRecorded:
      Number.isFinite(costs.monthlyEstimate) && costs.monthlyEstimate >= 0,
  };
  return {
    ...result,
    costEvidence,
    ready:
      result.verdict === "calibrated_with_30pct_headroom" &&
      Object.values(costEvidence).every(Boolean),
  };
}

export function evaluatePromotion({
  target,
  ledger,
  readiness,
  observations = {},
}) {
  demand(targets.has(target), `Unknown promotion target ${target}.`);
  const gates = reviewedGateMap(ledger);
  const reviewed = [...gates.values()];
  const policyHealthy = readiness?.policy?.healthy === true;
  const policyViolations = Array.isArray(readiness?.policy?.violations)
    ? readiness.policy.violations
    : [];

  const requiredOpen = reviewed.filter((gate) => requiredForTarget(gate, target));
  const requiredWithEvidence = requiredOpen.map((gate) => ({
    gate: gate.id,
    state: gate.state,
    evidenceReady: evidenceForGate(gate.id, observations)?.ready === true,
  }));
  const unresolvedRequired = requiredWithEvidence.filter(
    (entry) => !entry.evidenceReady,
  );

  const nextActions = reviewed
    .map((gate) => nextActionForGate(gate, readiness, observations))
    .filter(Boolean);

  const providerContracts = Object.fromEntries(
    ["x", "threads", "linkedin"].map((provider) => [
      provider,
      {
        configured: readiness?.providers?.[provider]?.oauth === true,
        callback: `${readiness?.origin || ""}/connections/oauth/${provider}/callback`,
        requiredScopes: readiness?.providers?.[provider]?.requiredScopes || [],
        optionalScopes: readiness?.providers?.[provider]?.optionalScopes || [],
        capabilities: readiness?.providers?.[provider]?.capabilities || null,
      },
    ]),
  );

  return {
    schemaVersion: 1,
    target,
    hostedRelease: readiness.release,
    environment: readiness.environment,
    runtimePolicy: {
      healthy: policyHealthy,
      violations: policyViolations,
      advanced: readiness?.runtimeCapabilities?.policies || null,
    },
    required: requiredWithEvidence,
    observations,
    providerContracts,
    nextActions,
    nextAction: nextActions[0] || null,
    promotion: {
      ready: policyHealthy && unresolvedRequired.length === 0,
      blockers: [
        ...(!policyHealthy ? policyViolations : []),
        ...unresolvedRequired.map((entry) => entry.gate),
      ],
      note:
        "A ready report is evidence for review, not authority to perform provider consent, public admission, global rollout or other external effects.",
    },
  };
}

function summary(report) {
  const lines = [
    `# PostSteward promotion controller`,
    ``,
    `- Target: \`${report.target}\``,
    `- Hosted release: \`${report.hostedRelease}\``,
    `- Runtime policy healthy: **${report.runtimePolicy.healthy ? "yes" : "no"}**`,
    `- Promotion evidence ready: **${report.promotion.ready ? "yes" : "no"}**`,
    ``,
    `## Blocking gates`,
    report.promotion.blockers.length
      ? report.promotion.blockers.map((item) => `- \`${item}\``).join("\n")
      : "- none",
    ``,
    `## Next actions`,
    report.nextActions.length
      ? report.nextActions
          .slice(0, 12)
          .map((item) => `- \`${item.gate}\`: ${item.action}`)
          .join("\n")
      : "- none",
    ``,
  ];
  return lines.join("\n");
}

async function collectObservations(env) {
  const observations = {};

  if (env.POSTSTEWARD_SLO_OBSERVATION)
    observations.advanced_rollout = evaluateSloObservation(
      readJson(env.POSTSTEWARD_SLO_OBSERVATION, "SLO observation"),
    );

  if (env.POSTSTEWARD_CAPACITY_OBSERVATION)
    observations.capacity_cost_calibration = capacityObservation(
      readJson(env.POSTSTEWARD_CAPACITY_OBSERVATION, "capacity observation"),
    );

  if (env.POSTSTEWARD_ALERT_EVIDENCE)
    observations.operational_alert_delivery = evaluateAlertEvidence(
      readJson(env.POSTSTEWARD_ALERT_EVIDENCE, "alert evidence"),
    );

  if (
    env.POSTSTEWARD_PRODUCTION_ORIGIN &&
    env.CLOUDFLARE_ZONE_NAME &&
    env.CLOUDFLARE_API_TOKEN
  )
    observations.production_edge = await inspectProductionEdge({
      origin: env.POSTSTEWARD_PRODUCTION_ORIGIN,
      zoneName: env.CLOUDFLARE_ZONE_NAME,
      cloudflareToken: env.CLOUDFLARE_API_TOKEN,
    });

  if (
    env.POSTSTEWARD_AGENT_TOKEN_A &&
    env.POSTSTEWARD_AGENT_TOKEN_B &&
    env.POSTSTEWARD_FOREIGN_DELIVERY_ID
  ) {
    const crossTenant = await checkCrossTenantIsolation(
      env.POSTSTEWARD_ORIGIN,
      env.POSTSTEWARD_AGENT_TOKEN_A,
      env.POSTSTEWARD_AGENT_TOKEN_B,
      env.POSTSTEWARD_FOREIGN_DELIVERY_ID,
    );
    observations.hosted_cross_tenant = { ...crossTenant, ready: true };
  }

  return observations;
}

async function main() {
  const env = process.env;
  const target = env.POSTSTEWARD_PROMOTION_TARGET || "production";
  const origin = exactOrigin(
    env.POSTSTEWARD_ORIGIN || "https://poststeward-staging.woeinvests.workers.dev",
  );
  const readiness = await fetchReadiness(origin);
  readiness.origin = origin;
  const ledger = readJson("public/release-gates.json", "reviewed gate ledger");
  const observations = await collectObservations(env);
  const report = evaluatePromotion({ target, ledger, readiness, observations });

  const serialized = JSON.stringify(report);
  for (const secret of [
    env.CLOUDFLARE_API_TOKEN,
    env.POSTSTEWARD_AGENT_TOKEN_A,
    env.POSTSTEWARD_AGENT_TOKEN_B,
  ].filter(Boolean))
    demand(!serialized.includes(secret), "Promotion report attempted to emit protected material.");

  console.log("POSTSTEWARD_PROMOTION_REPORT " + serialized);
  if (env.GITHUB_STEP_SUMMARY)
    appendFileSync(env.GITHUB_STEP_SUMMARY, summary(report), "utf8");
  if (!report.runtimePolicy.healthy) process.exitCode = 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch((error) => {
    console.error(
      "POSTSTEWARD_PROMOTION_CONTROLLER_FAILED " +
        JSON.stringify({ message: error instanceof Error ? error.message : "Unknown failure." }),
    );
    process.exitCode = 1;
  });
