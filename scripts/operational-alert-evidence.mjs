import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const REQUIRED_ALERT_CLASSES = Object.freeze([
  "worker_5xx",
  "rate_limit_pressure",
  "owner_auth_failure",
  "provider_or_oauth_failure",
  "durable_object_or_d1_failure",
  "recovery_state",
  "stripe_reconciliation",
  "capacity_threshold",
]);

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function timestamp(value, name) {
  const parsed = Date.parse(value);
  demand(Number.isFinite(parsed), `${name} must be an ISO timestamp.`);
  return parsed;
}

function boundedLabel(value, name) {
  demand(
    typeof value === "string" &&
      value.length >= 1 &&
      value.length <= 100 &&
      !/[\r\n]/.test(value),
    `${name} must be a bounded label.`,
  );
  return value;
}

export function evaluateAlertEvidence(observation) {
  demand(
    observation && typeof observation === "object" && !Array.isArray(observation),
    "Alert evidence must be an object.",
  );
  demand(observation.schemaVersion === 1, "Unsupported alert evidence schema.");
  demand(
    observation.evidenceClass === "hosted_observation",
    "Alert evidence must come from a hosted observation.",
  );
  demand(
    ["staging", "production"].includes(observation.environment),
    "Alert evidence environment must be staging or production.",
  );
  demand(
    /^[a-f0-9]{40}$/.test(observation.release || ""),
    "Alert evidence must identify an exact release SHA.",
  );
  demand(Array.isArray(observation.deliveries), "deliveries must be an array.");

  const seen = new Map();
  const paths = new Set();
  for (const [index, delivery] of observation.deliveries.entries()) {
    const alertClass = boundedLabel(delivery?.alertClass, `deliveries[${index}].alertClass`);
    demand(
      REQUIRED_ALERT_CLASSES.includes(alertClass),
      `deliveries[${index}].alertClass is not a reviewed alert class.`,
    );
    const path = boundedLabel(delivery?.path, `deliveries[${index}].path`);
    const testedAt = timestamp(delivery?.testedAt, `deliveries[${index}].testedAt`);
    const receivedAt = timestamp(delivery?.receivedAt, `deliveries[${index}].receivedAt`);
    demand(receivedAt >= testedAt, `deliveries[${index}] was received before it was tested.`);
    demand(delivery?.acknowledged === true, `deliveries[${index}] must be acknowledged.`);
    boundedLabel(delivery?.result || "received", `deliveries[${index}].result`);
    paths.add(path);
    if (!seen.has(alertClass)) seen.set(alertClass, new Set());
    seen.get(alertClass).add(path);
  }

  const missingClasses = REQUIRED_ALERT_CLASSES.filter((name) => !seen.has(name));
  const failedPath = observation.failedPath || {};
  const failedPathLabel = boundedLabel(failedPath.path, "failedPath.path");
  timestamp(failedPath.testedAt, "failedPath.testedAt");
  demand(
    failedPath.escalationObserved === true,
    "A deliberately failed notification path must demonstrate escalation.",
  );
  const fallbackPath = boundedLabel(failedPath.fallbackPath, "failedPath.fallbackPath");
  demand(
    fallbackPath !== failedPathLabel,
    "Escalation fallback must be a different delivery path.",
  );

  const configuredPaths = Array.isArray(observation.configuredPaths)
    ? observation.configuredPaths.map((value, index) =>
        boundedLabel(value, `configuredPaths[${index}]`),
      )
    : [...paths];
  const untestedPaths = configuredPaths.filter((path) => !paths.has(path));

  return {
    schemaVersion: 1,
    release: observation.release,
    environment: observation.environment,
    observedAt: observation.observedAt || null,
    requiredClasses: REQUIRED_ALERT_CLASSES,
    deliveredClasses: [...seen.keys()].sort(),
    configuredPaths: [...new Set(configuredPaths)].sort(),
    missingClasses,
    untestedPaths,
    failedPathEscalation: {
      path: failedPathLabel,
      fallbackPath,
      observed: true,
    },
    ready: missingClasses.length === 0 && untestedPaths.length === 0,
  };
}

export function readAlertEvidence(path) {
  demand(path, "Pass one alert evidence JSON file.");
  return JSON.parse(readFileSync(path, "utf8"));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const report = evaluateAlertEvidence(readAlertEvidence(process.argv[2]));
    console.log("POSTSTEWARD_ALERT_EVIDENCE " + JSON.stringify(report));
    if (!report.ready) process.exitCode = 2;
  } catch (error) {
    console.error(
      "POSTSTEWARD_ALERT_EVIDENCE_INVALID " +
        JSON.stringify({
          message: error instanceof Error ? error.message : "Invalid alert evidence.",
        }),
    );
    process.exitCode = 1;
  }
}
