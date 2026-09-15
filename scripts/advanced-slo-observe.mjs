import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function rows(value) {
  const batches = Array.isArray(value?.result) ? value.result : [];
  return batches.flatMap((batch) =>
    Array.isArray(batch?.results) ? batch.results : [],
  );
}

async function cloudflareJson(url, token, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    redirect: "error",
    signal: AbortSignal.timeout(20_000),
  });
  const value = await response.json().catch(() => ({}));
  demand(
    response.ok && value?.success !== false,
    `Cloudflare SLO query failed with HTTP ${response.status}.`,
  );
  return value;
}

async function queryD1(accountId, databaseId, token, sql, params = []) {
  return rows(
    await cloudflareJson(
      `https://api.cloudflare.com/client/v4/accounts/${accountId}/d1/database/${databaseId}/query`,
      token,
      { sql, params },
    ),
  );
}

function integer(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) && number >= 0 ? Math.floor(number) : 0;
}

export function percentile95(values) {
  const sorted = values
    .map(Number)
    .filter((value) => Number.isFinite(value) && value >= 0)
    .sort((a, b) => a - b);
  if (!sorted.length) return 0;
  return sorted[Math.max(0, Math.ceil(sorted.length * 0.95) - 1)];
}

export function buildAdvancedSloObservation({
  run,
  observedAt,
  eventCounts,
  workspaceCount,
  scheduleDurations,
  providerReconciliationDurations,
  webhookDurations,
  duplicateExternalEffects,
}) {
  demand(run && /^[a-f0-9]{40}$/.test(run.release || ""), "Canary run release is invalid.");
  const startedAt = Number(run.started_at);
  const stoppedAt = run.stopped_at == null ? null : Number(run.stopped_at);
  const end = stoppedAt == null ? Number(observedAt) : Math.min(Number(observedAt), stoppedAt);
  demand(Number.isFinite(startedAt) && Number.isFinite(end) && end >= startedAt, "Canary run window is invalid.");
  const count = (type) => integer(eventCounts[type]);
  const eligible = count("publication_eligible");
  const verified = count("verified_readback");
  const scheduleDue = scheduleDurations.length;
  const scheduleGood = scheduleDurations.filter((value) => Number(value) <= 5 * 60_000).length;
  const oauthAttempts = count("oauth_refresh_attempt");
  const oauthSucceeded = Math.min(oauthAttempts, count("oauth_refresh_success"));
  const observation = {
    schemaVersion: 1,
    evidenceClass: "hosted_observation",
    environment: "staging",
    release: run.release,
    cohort: {
      mode: "canary",
      bps: integer(run.canary_bps),
      startedAt,
      observedAt: end,
      workspaceCount: integer(workspaceCount),
      advancedOperations: count("advanced_operation"),
    },
    invariants: {
      duplicateExternalEffects: integer(duplicateExternalEffects),
    },
    publication: { verified, eligible },
    schedule: { withinFiveMinutes: scheduleGood, due: scheduleDue },
    oauthRefresh: { succeeded: oauthSucceeded, attempts: oauthAttempts },
    providerReconciliation: {
      samples: providerReconciliationDurations.length,
      p95Ms: percentile95(providerReconciliationDurations),
    },
    webhookReconciliation: {
      samples: webhookDurations.length,
      p95Ms: percentile95(webhookDurations),
    },
    advanced: {
      productPath: {
        sourceChanges: count("source_change"),
        inventorySnapshots: count("inventory_snapshot"),
        spacedAllocations: count("spaced_allocation"),
        providerEffects: eligible,
        verifiedReadbacks: verified,
        scheduledMetricsCaptures: count("scheduled_metrics_capture"),
      },
    },
    recovery: { samples: 0, p95RtoMs: 0, maxRpoMs: 0 },
  };
  demand(
    observation.publication.verified <= observation.publication.eligible,
    "Verified publication count exceeds eligible publications.",
  );
  demand(
    observation.oauthRefresh.succeeded <= observation.oauthRefresh.attempts,
    "OAuth refresh success count exceeds attempts.",
  );
  return observation;
}

async function hostedReadiness(origin) {
  const response = await fetch(new URL("/readiness.json", origin), {
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(20_000),
  });
  demand(response.ok, `Hosted readiness returned HTTP ${response.status}.`);
  return response.json();
}

export async function observeAdvancedSlo(env = process.env, query = queryD1) {
  const accountId = env.CLOUDFLARE_ACCOUNT_ID || "";
  const databaseId = env.D1_ID || "";
  const token = env.CLOUDFLARE_API_TOKEN || "";
  demand(/^[a-f0-9]{32}$/.test(accountId), "CLOUDFLARE_ACCOUNT_ID is required.");
  demand(/^[a-f0-9-]{36}$/.test(databaseId), "D1_ID is required.");
  demand(token.length >= 20, "Read-authorised CLOUDFLARE_API_TOKEN is required.");

  const runs = await query(
    accountId,
    databaseId,
    token,
    `SELECT id,release,canary_bps,seed_hash,started_at,stopped_at
       FROM advanced_canary_runs ORDER BY started_at DESC LIMIT 1`,
  );
  demand(runs.length === 1, "No hosted Advanced canary run boundary exists yet.");
  const run = runs[0];
  demand(/^[a-f0-9]{40}$/.test(run.release || ""), "Latest canary run has no exact release.");
  demand(integer(run.canary_bps) >= 1 && integer(run.canary_bps) <= 1000, "Latest canary run has invalid basis points.");
  const startedAt = Number(run.started_at);
  const observedAt = run.stopped_at == null ? Date.now() : Number(run.stopped_at);
  demand(Number.isFinite(startedAt) && Number.isFinite(observedAt) && observedAt >= startedAt, "Latest canary run window is invalid.");

  if (run.stopped_at == null && env.POSTSTEWARD_ORIGIN) {
    const readiness = await hostedReadiness(env.POSTSTEWARD_ORIGIN);
    demand(readiness?.release === run.release, "Active canary release differs from hosted staging.");
    demand(
      readiness?.runtimeCapabilities?.policies?.advancedEnabled === true &&
        readiness?.runtimeCapabilities?.policies?.advancedRolloutMode === "canary" &&
        Number(readiness?.runtimeCapabilities?.policies?.advancedCanaryBps) === Number(run.canary_bps),
      "Active canary boundary differs from hosted rollout policy.",
    );
  }

  const eventRows = await query(
    accountId,
    databaseId,
    token,
    `SELECT event_type,count(*) AS count
       FROM advanced_slo_events
      WHERE release=? AND observed_at>=? AND observed_at<=?
      GROUP BY event_type`,
    [run.release, startedAt, observedAt],
  );
  const eventCounts = Object.fromEntries(
    eventRows.map((row) => [row.event_type, integer(row.count)]),
  );
  const workspaceRows = await query(
    accountId,
    databaseId,
    token,
    `SELECT count(DISTINCT workspace) AS count
       FROM advanced_slo_events
      WHERE release=? AND observed_at>=? AND observed_at<=?`,
    [run.release, startedAt, observedAt],
  );
  const durationRows = await query(
    accountId,
    databaseId,
    token,
    `SELECT event_type,duration_ms
       FROM advanced_slo_events
      WHERE release=? AND observed_at>=? AND observed_at<=? AND duration_ms IS NOT NULL
      ORDER BY observed_at LIMIT 5000`,
    [run.release, startedAt, observedAt],
  );
  demand(durationRows.length < 5000, "Advanced SLO duration evidence exceeded the bounded query window.");
  const durations = (type) =>
    durationRows
      .filter((row) => row.event_type === type)
      .map((row) => Number(row.duration_ms))
      .filter((value) => Number.isFinite(value) && value >= 0);

  const canaryWorkspaces = `SELECT DISTINCT workspace FROM advanced_slo_events
    WHERE release=? AND observed_at>=? AND observed_at<=?`;
  const webhookRows = await query(
    accountId,
    databaseId,
    token,
    `SELECT received_at,completed_at FROM stripe_events
      WHERE completed_at IS NOT NULL
        AND received_at>=? AND received_at<=?
        AND workspace IN (${canaryWorkspaces})
      ORDER BY received_at LIMIT 1000`,
    [startedAt, observedAt, run.release, startedAt, observedAt],
  );
  demand(webhookRows.length < 1000, "Webhook SLO evidence exceeded the bounded query window.");
  const webhookDurations = webhookRows
    .map((row) => Number(row.completed_at) - Number(row.received_at))
    .filter((value) => Number.isFinite(value) && value >= 0);

  const duplicateRows = await query(
    accountId,
    databaseId,
    token,
    `SELECT count(*) AS count FROM (
       SELECT workspace,fingerprint,count(*) AS effects
         FROM external_effects
        WHERE workspace IN (${canaryWorkspaces})
        GROUP BY workspace,fingerprint HAVING effects>1
     )`,
    [run.release, startedAt, observedAt],
  );

  return buildAdvancedSloObservation({
    run,
    observedAt,
    eventCounts,
    workspaceCount: integer(workspaceRows[0]?.count),
    scheduleDurations: durations("schedule_observation"),
    providerReconciliationDurations: durations("provider_reconciliation"),
    webhookDurations,
    duplicateExternalEffects: integer(duplicateRows[0]?.count),
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  observeAdvancedSlo()
    .then((observation) => {
      const serialized = JSON.stringify(observation);
      if (process.env.POSTSTEWARD_SLO_OUTPUT)
        writeFileSync(process.env.POSTSTEWARD_SLO_OUTPUT, serialized + "\n", "utf8");
      console.log("POSTSTEWARD_ADVANCED_SLO_OBSERVATION " + serialized);
    })
    .catch((error) => {
      console.error(
        "POSTSTEWARD_ADVANCED_SLO_OBSERVATION_FAILED " +
          JSON.stringify({ message: error instanceof Error ? error.message : "Unknown failure." }),
      );
      process.exitCode = 1;
    });
}
