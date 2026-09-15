import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";
import { evaluateCapacity } from "./capacity-calibration.mjs";
import { exactOrigin } from "./promotion-evidence.mjs";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function boundedInteger(value, name) {
  const number = Number(value);
  demand(Number.isInteger(number) && number >= 0, `${name} must be a non-negative integer.`);
  return number;
}

function d1Rows(response) {
  const batches = Array.isArray(response?.result) ? response.result : [];
  return batches.flatMap((batch) => (Array.isArray(batch?.results) ? batch.results : []));
}

async function cloudflareJson(url, token, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
    redirect: "error",
    signal: AbortSignal.timeout(20_000),
  });
  let value = {};
  try {
    value = await response.json();
  } catch {
    value = {};
  }
  demand(response.ok && value.success !== false, `Cloudflare observation failed with HTTP ${response.status}.`);
  return value;
}

async function queryD1(accountId, databaseId, token, sql, params = []) {
  const value = await cloudflareJson(
    `https://api.cloudflare.com/client/v4/accounts/${accountId}/d1/database/${databaseId}/query`,
    token,
    { method: "POST", body: JSON.stringify({ sql, params }) },
  );
  return d1Rows(value);
}

async function queryGraphql(accountId, scriptName, databaseId, token, start, end) {
  const query = `query Capacity($accountTag: string, $scriptName: string, $databaseId: string, $start: string, $end: string) {
    viewer {
      accounts(filter: { accountTag: $accountTag }) {
        workersInvocationsAdaptive(limit: 10000, filter: { scriptName: $scriptName, datetime_geq: $start, datetime_leq: $end }) {
          sum { requests subrequests errors }
          quantiles { cpuTimeP50 cpuTimeP99 }
        }
        d1AnalyticsAdaptiveGroups(limit: 10000, filter: { databaseId: $databaseId, datetime_geq: $start, datetime_leq: $end }) {
          sum { readQueries writeQueries rowsRead rowsWritten queryBatchResponseBytes queryBatchTimeMs }
        }
      }
    }
  }`;
  const value = await cloudflareJson(
    "https://api.cloudflare.com/client/v4/graphql",
    token,
    {
      method: "POST",
      body: JSON.stringify({
        query,
        variables: { accountTag: accountId, scriptName, databaseId, start, end },
      }),
    },
  );
  demand(!value.errors?.length, "Cloudflare GraphQL capacity query returned errors.");
  const account = value?.data?.viewer?.accounts?.[0];
  demand(account, "Cloudflare GraphQL returned no matching account analytics.");
  return account;
}

function sumGroups(groups, fields) {
  return Object.fromEntries(fields.map((field) => [field,
    (groups || []).reduce((sum, group) => sum + Number(group?.sum?.[field] || 0), 0),
  ]));
}
function maxGroups(groups, fields) {
  return Object.fromEntries(fields.map((field) => [field,
    Math.max(0, ...(groups || []).map((group) => Number(group?.quantiles?.[field] || 0))),
  ]));
}

export function buildCapacityObservation({ highWater, workerAnalytics, d1Analytics,
  providerQuota, pricing, windowDays }) {
  const observation = {
    workspaces: boundedInteger(highWater.workspaces, "workspaces"),
    peakRecordsPerWorkspace: boundedInteger(highWater.peakRecordsPerWorkspace, "peakRecordsPerWorkspace"),
    peakBytesPerWorkspace: boundedInteger(highWater.peakBytesPerWorkspace, "peakBytesPerWorkspace"),
    maxValueBytes: boundedInteger(highWater.maxValueBytes, "maxValueBytes"),
    peakDailyDeliveries: boundedInteger(highWater.peakDailyDeliveries, "peakDailyDeliveries"),
    peakActiveSchedules: boundedInteger(highWater.peakActiveSchedules, "peakActiveSchedules"),
    peakProfiles: boundedInteger(highWater.peakProfiles, "peakProfiles"),
    requestsPerWorkspaceDay: boundedInteger(highWater.requestsPerWorkspaceDay, "requestsPerWorkspaceDay"),
    providerPollsPerWorkspaceDay: boundedInteger(highWater.alarmCyclesPerWorkspaceDay, "alarmCyclesPerWorkspaceDay"),
  };
  const calibrated = evaluateCapacity(observation);
  const providerQuotaObserved =
    providerQuota?.evidenceClass === "provider_observation" &&
    Number.isFinite(providerQuota?.observedAt) &&
    Array.isArray(providerQuota?.providers) && providerQuota.providers.length > 0;
  const pricingObserved = pricing?.evidenceClass === "reviewed_pricing" &&
    Number.isFinite(pricing?.monthlyEstimate) && pricing.monthlyEstimate >= 0;
  const costEvidence = {
    cloudflareObserved: true, providerQuotaObserved, alarmWebhookVolumeObserved: true,
    estimateRecorded: pricingObserved,
    monthlyEstimate: pricingObserved ? pricing.monthlyEstimate : null,
  };
  return {
    schemaVersion: 1, evidenceClass: "hosted_observation", windowDays, ...observation,
    providerPollObservation: {
      method: "workspace_alarm_cycles_upper_bound",
      note: "Alarm cycles are a conservative upper-bound proxy for provider polling cycles; they never understate scheduler wake frequency.",
    },
    calibration: calibrated,
    cloudflare: { worker: workerAnalytics, d1: d1Analytics },
    providerQuota: providerQuotaObserved ? providerQuota : null,
    pricing: pricingObserved ? pricing : null,
    costEvidence,
    ready: calibrated.verdict === "calibrated_with_30pct_headroom" &&
      costEvidence.cloudflareObserved && costEvidence.providerQuotaObserved &&
      costEvidence.alarmWebhookVolumeObserved && costEvidence.estimateRecorded,
  };
}

export async function readCapacityRuntime(origin, expectedRelease, send = fetch) {
  exactOrigin(origin);
  demand(/^[a-f0-9]{40}$/.test(expectedRelease || ""), "Capacity observation requires the expected release SHA.");
  const response = await send(`${origin}/readiness.json`, {
    redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(20_000),
  });
  demand(response.status === 200, "Capacity runtime readiness is unavailable.");
  const value = await response.json();
  demand(value?.release === expectedRelease && ["staging", "production"].includes(value?.environment) &&
    Number.isInteger(value?.schemaVersion) && value.schemaVersion >= 2 &&
    value?.policy?.healthy === true && Array.isArray(value?.policy?.violations) && value.policy.violations.length === 0,
    "Capacity runtime does not match the exact healthy release.");
  return { release: value.release, environment: value.environment, origin,
    policy: value.policy, gates: value.gates, runtimeCapabilities: value.runtimeCapabilities };
}

export function capacityWorkerName(environment) {
  demand(["staging", "production"].includes(environment), "Capacity environment is invalid.");
  // Keep the established deployment-config.mjs names, not an invented suffix.
  return environment === "staging" ? "poststeward-staging" : "poststeward";
}

export function bindCapacityObservation(report, context, highWater, collectedAt = Date.now()) {
  const first = Number(highWater.firstObservedAt);
  const last = Number(highWater.lastObservedAt);
  demand(Number.isFinite(first) && first > 0 && Number.isFinite(last) && last >= first &&
    Number.isFinite(collectedAt) && collectedAt >= last,
    "Capacity sample timestamps are missing or invalid.");
  return {
    ...report, release: context.release, environment: context.environment, origin: context.origin,
    // Freshness follows the durable SAMPLE time, not the time an old dataset is fetched.
    observedAt: last, collectedAt,
    sampleWindow: { firstObservedAt: first, lastObservedAt: last },
    provenanceBoundary: "High-water rows last observed on the named release; daily maxima and Cloudflare analytics may include earlier revisions within the reported window. This is not an isolated per-release load test.",
  };
}

function optionalJson(path, label) {
  if (!path) return null;
  const value = JSON.parse(readFileSync(path, "utf8"));
  demand(value && typeof value === "object", `${label} evidence must be JSON object.`);
  return value;
}

async function main() {
  const accountId = process.env.CLOUDFLARE_ACCOUNT_ID || "";
  const databaseId = process.env.D1_ID || "";
  const scriptName = process.env.POSTSTEWARD_WORKER_NAME || "poststeward-staging";
  const token = process.env.CLOUDFLARE_API_TOKEN || "";
  const windowDays = Number(process.env.POSTSTEWARD_CAPACITY_WINDOW_DAYS || "7");
  demand(Number.isInteger(windowDays) && windowDays >= 1 && windowDays <= 30, "Capacity window must be 1 to 30 whole days.");
  demand(/^[a-f0-9]{32}$/.test(accountId), "CLOUDFLARE_ACCOUNT_ID is required.");
  demand(/^[a-f0-9-]{36}$/.test(databaseId), "D1_ID is required.");
  demand(token.length >= 20, "Read-authorised CLOUDFLARE_API_TOKEN is required.");
  demand(/^[a-z0-9-]{1,100}$/.test(scriptName), "POSTSTEWARD_WORKER_NAME is invalid.");
  const origin = process.env.POSTSTEWARD_ORIGIN || "https://poststeward-staging.woeinvests.workers.dev";
  const expectedRelease = process.env.POSTSTEWARD_EXPECTED_RELEASE ?? process.env.GITHUB_SHA;
  const context = await readCapacityRuntime(origin, expectedRelease);
  demand(scriptName === capacityWorkerName(context.environment), "Capacity Worker name and runtime environment differ.");

  const end = new Date();
  const start = new Date(end.getTime() - windowDays * 86400000);
  const cutoff = start.toISOString().slice(0, 10);
  const highRows = await queryD1(accountId, databaseId, token,
    `SELECT count(DISTINCT workspace_fingerprint) AS workspaces,
       min(observed_at) AS firstObservedAt, max(observed_at) AS lastObservedAt,
       max(records) AS peakRecordsPerWorkspace,
       max(bytes) AS peakBytesPerWorkspace,
       max(max_value_bytes) AS maxValueBytes,
       max(daily_deliveries) AS peakDailyDeliveries,
       max(active_schedules) AS peakActiveSchedules,
       max(source_profiles) AS peakProfiles,
       max(workspace_requests) AS requestsPerWorkspaceDay,
       max(alarm_cycles) AS alarmCyclesPerWorkspaceDay
     FROM workspace_capacity_observations
     WHERE observation_date >= ? AND release = ?`,
    [cutoff, context.release]);
  const highWater = highRows[0] || {};
  demand(Number(highWater.workspaces || 0) > 0, "No hosted workspace capacity observations exist for this release in the requested window yet.");

  const graphql = await queryGraphql(accountId, scriptName, databaseId, token, start.toISOString(), end.toISOString());
  const worker = {
    ...sumGroups(graphql.workersInvocationsAdaptive, ["requests", "subrequests", "errors"]),
    ...maxGroups(graphql.workersInvocationsAdaptive, ["cpuTimeP50", "cpuTimeP99"]),
  };
  const d1 = sumGroups(graphql.d1AnalyticsAdaptiveGroups, [
    "readQueries", "writeQueries", "rowsRead", "rowsWritten", "queryBatchResponseBytes", "queryBatchTimeMs",
  ]);
  const alertRows = await queryD1(accountId, databaseId, token,
    `SELECT count(*) AS total,
      sum(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent,
      sum(CASE WHEN status='dead' THEN 1 ELSE 0 END) AS dead
     FROM operational_alerts WHERE created_at >= ?`, [start.getTime()]);
  worker.alertVolume = alertRows[0] || { total: 0, sent: 0, dead: 0 };

  const evaluated = buildCapacityObservation({ highWater, workerAnalytics: worker, d1Analytics: d1,
    providerQuota: optionalJson(process.env.POSTSTEWARD_PROVIDER_QUOTA_EVIDENCE, "provider quota"),
    pricing: optionalJson(process.env.POSTSTEWARD_PRICING_EVIDENCE, "pricing"), windowDays });
  demand(isDeepStrictEqual(context, await readCapacityRuntime(origin, expectedRelease)),
    "Capacity runtime changed while collecting evidence.");
  const report = bindCapacityObservation(evaluated, context, highWater);
  const serialized = JSON.stringify(report);
  demand(!serialized.includes(token), "Capacity observation attempted to emit protected material.");
  if (process.env.POSTSTEWARD_CAPACITY_OUTPUT)
    writeFileSync(process.env.POSTSTEWARD_CAPACITY_OUTPUT, serialized + "\n", "utf8");
  console.log("POSTSTEWARD_CAPACITY_OBSERVATION " + serialized);
  if (!report.ready) process.exitCode = 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch(() => {
    console.error("POSTSTEWARD_CAPACITY_OBSERVATION_FAILED invalid_input_or_observation");
    process.exitCode = 1;
  });
