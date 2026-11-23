import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { fetchMonitorReadiness } from "./monitor-readiness.mjs";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

export function parseD1Rows(value) {
  demand(value?.success === true, "D1 response did not confirm success.");
  demand(Array.isArray(value.result) && value.result.length === 1, "D1 query has missing or ambiguous result batches.");
  const batch = value.result[0];
  demand(batch?.success === true, "D1 query batch reported failure.");
  demand(Array.isArray(batch.results), "D1 query batch lacks a complete results array.");
  return batch.results;
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
  return parseD1Rows(
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
  duplicateEvidenceMethod = "unverified",
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
  const oauthSucceeded = count("oauth_refresh_success");
  demand(oauthSucceeded <= oauthAttempts, "OAuth success events exceed genuine refresh attempts.");
  const observation = {
    schemaVersion: 1,
    evidenceClass: "hosted_observation",
    environment: "staging",
    release: run.release,
    cohort: {
      mode: "canary",
      runId: run.id || null,
      seedHash: run.seed_hash || null,
      bps: integer(run.canary_bps),
      startedAt,
      observedAt: end,
      workspaceCount: integer(workspaceCount),
      advancedOperations: count("advanced_operation"),
    },
    invariants: {
      duplicateExternalEffects: duplicateExternalEffects == null ? null : integer(duplicateExternalEffects),
      evidenceMethod: duplicateEvidenceMethod,
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
    // Aggregate event receipts do not prove complete provider, scheduling,
    // OAuth, webhook or recovery denominators. Do not promote unsupported
    // metrics until each producer supplies independently joined provenance.
    evidenceCoverage: {
      publication: false,
      schedule: false,
      oauth: false,
      providerReconciliation: false,
      webhook: false,
      cardinality: false,
      recovery: false,
    },
    recovery: { samples: 0, p95RtoMs: 0, maxRpoMs: 0, evidenceMethod: "unverified_no_recovery_event_source" },
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
  const response = await fetchMonitorReadiness(origin);
  demand(response.ok, `Hosted readiness returned HTTP ${response.status}.`);
  return response.json();
}

export async function observeAdvancedSlo(env = process.env, query = queryD1, fetchReadiness = hostedReadiness) {
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
       FROM advanced_canary_runs
      WHERE stopped_at IS NULL
      ORDER BY started_at DESC LIMIT 2`,
  );
  demand(runs.length > 0, "No active Advanced canary run boundary exists yet.");
  demand(runs.length === 1, "Multiple active Advanced canary run boundaries exist.");
  const run = runs[0];
  demand(run.stopped_at == null, "No active Advanced canary run boundary exists yet.");
  demand(typeof run.id === "string" && run.id.length >= 8, "Active canary run identifier is missing.");
  demand(/^[a-f0-9]{64}$/.test(run.seed_hash || ""), "Active canary reviewed seed hash is missing.");
  demand(/^[a-f0-9]{40}$/.test(run.release || ""), "Latest canary run has no exact release.");
  demand(integer(run.canary_bps) >= 1 && integer(run.canary_bps) <= 1000, "Latest canary run has invalid basis points.");
  const startedAt = Number(run.started_at);
  const observedAt = Date.now();
  demand(Number.isFinite(startedAt) && Number.isFinite(observedAt) && observedAt >= startedAt, "Active canary run window is invalid.");

  const stagingOrigin = "https://poststeward-staging.woeinvests.workers.dev";
  demand(env.DEPLOY_ENV === "staging", "SLO evidence requires the staging deployment environment.");
  demand(env.APP_ORIGIN === stagingOrigin, "APP_ORIGIN does not identify the canonical staging application.");
  demand(env.POSTSTEWARD_ORIGIN === env.APP_ORIGIN, "SLO observation origin must equal the reviewed APP_ORIGIN.");
  {
    const readiness = await fetchReadiness(env.POSTSTEWARD_ORIGIN);
    demand(readiness?.environment === "staging", "Active canary readiness is not the staging environment.");
    demand(readiness?.release === run.release, "Active canary release differs from hosted staging.");
    demand(
      readiness?.runtimeCapabilities?.policies?.advancedEnabled === true &&
        readiness?.runtimeCapabilities?.policies?.advancedRolloutMode === "canary" &&
        Number(readiness?.runtimeCapabilities?.policies?.advancedCanaryBps) === Number(run.canary_bps),
      "Active canary boundary differs from hosted rollout policy.",
    );
  }

  // Bind every accepted event to the same exact active run, release and
  // basis points. Trust only producer-defined origin/outcome combinations,
  // hashed stable references and non-empty workspaces.
  const trustedPairs = [
    ["advanced_operation", "completed_operation", "succeeded"],
    ["source_change", "observed_campaign", "observed"],
    ["inventory_snapshot", "observed_campaign", "observed"],
    ["spaced_allocation", "observed_allocation", "observed"],
    ["publication_eligible", "due_delivery", "eligible"],
    ["verified_readback", "provider_verified", "verified"],
    ["schedule_observation", "provider_write_completed", "succeeded"],
    ["provider_reconciliation", "provider_verified", "verified"],
    ["oauth_refresh_attempt", "oauth_attempt", "attempted"],
    ["oauth_refresh_success", "oauth_success", "succeeded"],
    ["scheduled_metrics_capture", "observed_metrics", "observed"],
    ["webhook_reconciliation", "webhook_confirmed", "verified"],
  ];
  const trustedPredicate = trustedPairs
    .map(([type, origin, outcome]) =>
      `(event_type='${type}' AND evidence_origin='${origin}' AND evidence_outcome='${outcome}')`)
    .join(" OR ");
  const scopedWhere = `canary_run_id=? AND release=? AND canary_bps=?
    AND observed_at>=? AND observed_at<=?
    AND workspace IS NOT NULL AND workspace<>''
    AND reference_hash IS NOT NULL AND length(reference_hash)=64
    AND (${trustedPredicate})`;
  const scopeParams = [run.id, run.release, integer(run.canary_bps), startedAt, observedAt];
  const groupedRows = await query(
    accountId, databaseId, token,
    `SELECT workspace,event_type,count(*) AS count FROM advanced_slo_events
      WHERE ${scopedWhere} GROUP BY workspace,event_type
      ORDER BY workspace,event_type LIMIT 5000`,
    scopeParams,
  );
  demand(groupedRows.length < 5000, "Advanced SLO workspace evidence exceeded the bounded query window.");
  const workspaceTypes = new Map();
  for (const row of groupedRows) {
    demand(typeof row.workspace === "string" && row.workspace.length > 0, "Invalid workspace evidence.");
    const count = Number(row.count);
    demand(Number.isSafeInteger(count) && count >= 0, "Invalid trusted event count.");
    if (!workspaceTypes.has(row.workspace)) workspaceTypes.set(row.workspace, {});
    workspaceTypes.get(row.workspace)[row.event_type] = count;
  }
  // A second bounded count detects truncated/incomplete workspace grouping.
  // count(DISTINCT workspace) is only a cardinality cross-check: it must
  // NEVER be used as proof that one workspace completed the entire journey.
  const workspaceCardinalityRows = await query(
    accountId, databaseId, token,
    `SELECT count(DISTINCT workspace) AS count FROM advanced_slo_events
      WHERE ${scopedWhere}`,
    scopeParams,
  );
  demand(workspaceCardinalityRows.length === 1 &&
    Number.isSafeInteger(Number(workspaceCardinalityRows[0].count)) &&
    Number(workspaceCardinalityRows[0].count) === workspaceTypes.size,
    "Trusted workspace cardinality evidence is incomplete.");
  // The minimum 20 operations AND every journey milestone must belong to
  // one workspace, not seven unrelated workspaces pooled into one journey.
  const milestones = [
    "source_change", "inventory_snapshot", "spaced_allocation",
    "publication_eligible", "verified_readback", "scheduled_metrics_capture",
  ];
  const completeWorkspaces = [...workspaceTypes.entries()]
    .filter(([, counts]) =>
      (counts.advanced_operation || 0) >= 20 &&
      milestones.every((type) => (counts[type] || 0) >= 1));
  const completeIds = new Set(completeWorkspaces.map(([workspace]) => workspace));
  const eventCounts = {};
  for (const [, counts] of completeWorkspaces) {
    for (const [type, count] of Object.entries(counts))
      eventCounts[type] = (eventCounts[type] || 0) + count;
  }
  const durationRows = await query(
    accountId, databaseId, token,
    `SELECT workspace,event_type,duration_ms FROM advanced_slo_events
      WHERE ${scopedWhere} ORDER BY observed_at LIMIT 5000`,
    scopeParams,
  );
  demand(durationRows.length < 5000, "Advanced SLO duration evidence exceeded the bounded query window.");
  const durations = (type) => {
    const selected = durationRows.filter((row) =>
      completeIds.has(row.workspace) && row.event_type === type);
    demand(selected.length === integer(eventCounts[type]),
      `Incomplete ${type} latency denominator.`);
    return selected.map((row) => {
      demand(row.duration_ms !== null && row.duration_ms !== undefined &&
        Number.isFinite(Number(row.duration_ms)) && Number(row.duration_ms) >= 0,
        `Missing or invalid ${type} duration provenance.`);
      return Number(row.duration_ms);
    });
  };
  // Only the same fully eligible workspace(s) can contribute webhook
  // observations. Never borrow webhook evidence from another workspace.
  const webhookRows = completeIds.size
    ? await query(
      accountId,
      databaseId,
      token,
      `SELECT received_at,completed_at FROM stripe_events
        WHERE received_at>=? AND received_at<=?
          AND workspace IN (${[...completeIds].map(() => "?").join(",")})
        ORDER BY received_at LIMIT 1000`,
      [startedAt, observedAt, ...completeIds],
    )
    : [];
  demand(webhookRows.length < 1000, "Webhook SLO evidence exceeded the bounded query window.");
  const webhookDurations = webhookRows.map((row) => {
    demand(row.completed_at !== null && row.completed_at !== undefined &&
      row.received_at !== null && row.received_at !== undefined,
      "Incomplete webhook completion denominator.");
    const duration = Number(row.completed_at) - Number(row.received_at);
    demand(Number.isFinite(duration) && duration >= 0,
      "Invalid webhook completion duration.");
    return duration;
  });

  // external_effects has PRIMARY KEY(workspace, fingerprint). Grouping by
  // those columns can never count more than one row and cannot establish
  // whether a provider published twice after an uncertain network effect.
  // Until independent provider readback exists, expose unknown evidence,
  // never a fabricated zero for the release invariant.
  return buildAdvancedSloObservation({
    run,
    observedAt,
    eventCounts,
    workspaceCount: completeWorkspaces.length,
    scheduleDurations: durations("schedule_observation"),
    providerReconciliationDurations: durations("provider_reconciliation"),
    webhookDurations,
    duplicateExternalEffects: null,
    duplicateEvidenceMethod: "workspace_fingerprint_primary_key",
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
      const message = error instanceof Error ? error.message : "Unknown failure.";
      if (
        process.env.POSTSTEWARD_SLO_OPTIONAL === "true" &&
        message === "No active Advanced canary run boundary exists yet."
      ) {
        console.log(
          "POSTSTEWARD_ADVANCED_SLO_IDLE " +
            JSON.stringify({ state: "no_active_canary", releaseAligned: false, promotionReady: false }),
        );
        return;
      }
      console.error(
        "POSTSTEWARD_ADVANCED_SLO_OBSERVATION_FAILED " +
          JSON.stringify({ message }),
      );
      process.exitCode = 1;
    });
}
