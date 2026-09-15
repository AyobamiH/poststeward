import { advancedRolloutDecision } from "./advanced-rollout.ts";
import type { Env } from "./types.ts";

export type AdvancedSloEventType =
  | "advanced_operation"
  | "source_change"
  | "inventory_snapshot"
  | "spaced_allocation"
  | "publication_eligible"
  | "verified_readback"
  | "schedule_observation"
  | "provider_reconciliation"
  | "oauth_refresh_attempt"
  | "oauth_refresh_success"
  | "scheduled_metrics_capture"
  | "webhook_reconciliation";

const allowed = new Set<AdvancedSloEventType>([
  "advanced_operation",
  "source_change",
  "inventory_snapshot",
  "spaced_allocation",
  "publication_eligible",
  "verified_readback",
  "schedule_observation",
  "provider_reconciliation",
  "oauth_refresh_attempt",
  "oauth_refresh_success",
  "scheduled_metrics_capture",
  "webhook_reconciliation",
]);

async function sha256(value: string) {
  const bytes = new Uint8Array(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
  );
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function advancedCanaryTelemetryContext(env: Env, workspace: string) {
  if (
    env.DEPLOY_ENV !== "staging" ||
    !workspace ||
    !/^[a-f0-9]{40}$/.test(env.RELEASE_SHA || "")
  )
    return undefined;
  const decision = advancedRolloutDecision(env, workspace);
  if (
    !decision.masterEnabled ||
    decision.mode !== "canary" ||
    !decision.eligible ||
    decision.canaryBps < 1 ||
    decision.canaryBps > 1000
  )
    return undefined;
  return {
    release: env.RELEASE_SHA,
    canaryBps: decision.canaryBps,
  };
}

/**
 * SLO telemetry is evidence only. It must never make a product operation fail.
 * Stable dedupe keys are hashed with the workspace and event type before they
 * reach D1; raw provider IDs, access tokens, content and seed values are never
 * stored in this ledger.
 */
export async function recordAdvancedSloEvent(
  env: Env,
  workspace: string,
  eventType: AdvancedSloEventType,
  options: {
    value?: number;
    durationMs?: number;
    dedupeKey?: string;
  } = {},
  now = Date.now(),
) {
  try {
    if (!allowed.has(eventType)) return false;
    const context = advancedCanaryTelemetryContext(env, workspace);
    if (!context) return false;
    const value = Number.isInteger(options.value) && Number(options.value) >= 0
      ? Number(options.value)
      : 1;
    const durationMs = options.durationMs === undefined
      ? null
      : Number.isFinite(options.durationMs) && Number(options.durationMs) >= 0
        ? Math.round(Number(options.durationMs))
        : null;
    const id = options.dedupeKey
      ? await sha256(
          `${workspace}\0${eventType}\0${context.release}\0${options.dedupeKey}`,
        )
      : crypto.randomUUID();
    await env.IDENTITY.prepare(
      `INSERT INTO advanced_slo_events(
        id,workspace,event_type,observed_at,value,duration_ms,release,canary_bps
      ) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING`,
    )
      .bind(
        id,
        workspace,
        eventType,
        now,
        value,
        durationMs,
        context.release,
        context.canaryBps,
      )
      .run();
    return true;
  } catch (error) {
    console.error(
      JSON.stringify({
        event: "advanced_slo_telemetry_failed",
        eventType,
        release: env.RELEASE_SHA,
        code: error instanceof Error ? error.name : "UnknownError",
      }),
    );
    return false;
  }
}

export async function pruneAdvancedSloTelemetry(
  env: Env,
  now = Date.now(),
  retainedDays = 31,
) {
  try {
    const cutoff = now - retainedDays * 86400000;
    await env.IDENTITY.prepare(
      "DELETE FROM advanced_slo_events WHERE observed_at < ?",
    )
      .bind(cutoff)
      .run();
    return true;
  } catch (error) {
    console.error(
      JSON.stringify({
        event: "advanced_slo_telemetry_prune_failed",
        release: env.RELEASE_SHA,
        code: error instanceof Error ? error.name : "UnknownError",
      }),
    );
    return false;
  }
}
