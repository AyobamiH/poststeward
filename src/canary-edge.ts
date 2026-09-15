import edge, { Workspace as BaseWorkspace } from "./edge.ts";
import { advancedRolloutDecision } from "./advanced-rollout.ts";
import {
  initialiseCapacityTelemetry,
  recordAlarmCycle,
  recordWorkspaceRequest,
  sweepWorkspaceCapacityObservations,
  workspaceCapacitySnapshot,
} from "./capacity-observation.ts";
import { byName } from "./operations/catalog.ts";
import {
  enqueueOperationalAlert,
  flushOperationalAlerts,
  sweepOperationalConditions,
} from "./operational-alerts.ts";
import {
  advancedCanaryTelemetryContext,
  pruneAdvancedSloTelemetry,
  recordAdvancedSloEvent,
} from "./slo-telemetry.ts";
import type { Campaign, Delivery, Env, Profile } from "./types.ts";

function storedWorkspace(ctx: DurableObjectState) {
  try {
    const row = ctx.storage.sql
      .exec<{ value: string }>(
        "SELECT value FROM records WHERE key='workspace' LIMIT 1",
      )
      .toArray()[0];
    if (!row) return undefined;
    const value = JSON.parse(row.value);
    return typeof value === "string" ? value : undefined;
  } catch {
    return undefined;
  }
}

function storedJson<T>(ctx: DurableObjectState, key: string): T | undefined {
  try {
    const row = ctx.storage.sql
      .exec<{ value: string }>(
        "SELECT value FROM records WHERE key=? LIMIT 1",
        key,
      )
      .toArray()[0];
    return row ? (JSON.parse(row.value) as T) : undefined;
  } catch {
    return undefined;
  }
}

function storedRows<T>(ctx: DurableObjectState, prefix: string, limit = 500) {
  try {
    return ctx.storage.sql
      .exec<{ key: string; value: string }>(
        "SELECT key,value FROM records WHERE key LIKE ? ORDER BY key LIMIT ?",
        `${prefix}%`,
        limit,
      )
      .toArray()
      .flatMap((row) => {
        try {
          return [{ key: row.key, value: JSON.parse(row.value) as T }];
        } catch {
          return [];
        }
      });
  } catch {
    return [];
  }
}

function oauthRows(ctx: DurableObjectState) {
  return storedRows<any>(ctx, "oauth:", 100);
}

async function observeAdvancedProductState(
  ctx: DurableObjectState,
  env: Env,
  workspace: string,
) {
  if (!advancedCanaryTelemetryContext(env, workspace)) return;

  for (const row of storedRows<Campaign>(ctx, "campaign:")) {
    const campaign = row.value;
    if (!campaign.source || !Number.isFinite(campaign.createdAt)) continue;
    const key = campaign.id || row.key;
    await recordAdvancedSloEvent(
      env,
      workspace,
      "source_change",
      { dedupeKey: key },
      campaign.createdAt,
    );
    await recordAdvancedSloEvent(
      env,
      workspace,
      "inventory_snapshot",
      { dedupeKey: key },
      campaign.createdAt,
    );
    await recordAdvancedSloEvent(
      env,
      workspace,
      "advanced_operation",
      { dedupeKey: `source:${key}` },
      campaign.createdAt,
    );
  }

  const automatic = storedRows<Delivery>(ctx, "delivery:")
    .map((row) => row.value)
    .filter((delivery) => delivery.automatic);
  const effects = await env.IDENTITY.prepare(
    `SELECT delivery_id,status,post_id,created_at,updated_at
       FROM external_effects WHERE workspace=? ORDER BY created_at LIMIT 1000`,
  )
    .bind(workspace)
    .all<{
      delivery_id: string;
      status: string;
      post_id: string | null;
      created_at: number;
      updated_at: number;
    }>();
  const effectByDelivery = new Map(
    (effects.results || []).map((row) => [row.delivery_id, row]),
  );

  for (const delivery of automatic) {
    if (!Number.isFinite(delivery.createdAt)) continue;
    await recordAdvancedSloEvent(
      env,
      workspace,
      "spaced_allocation",
      { dedupeKey: delivery.id },
      delivery.createdAt,
    );
    await recordAdvancedSloEvent(
      env,
      workspace,
      "advanced_operation",
      { dedupeKey: `allocation:${delivery.id}` },
      delivery.createdAt,
    );

    const effect = effectByDelivery.get(delivery.id);
    if (!effect?.post_id) continue;
    const publishAt = Number(effect.created_at);
    const reconcileAt = Number(effect.updated_at);
    await recordAdvancedSloEvent(
      env,
      workspace,
      "publication_eligible",
      { dedupeKey: delivery.id },
      publishAt,
    );
    await recordAdvancedSloEvent(
      env,
      workspace,
      "schedule_observation",
      {
        dedupeKey: delivery.id,
        durationMs: Math.max(0, publishAt - Number(delivery.dueAt || publishAt)),
      },
      publishAt,
    );
    if (["verified", "unverified"].includes(effect.status)) {
      await recordAdvancedSloEvent(
        env,
        workspace,
        "provider_reconciliation",
        {
          dedupeKey: delivery.id,
          durationMs: Math.max(0, reconcileAt - publishAt),
        },
        reconcileAt,
      );
    }
    if (effect.status === "verified")
      await recordAdvancedSloEvent(
        env,
        workspace,
        "verified_readback",
        { dedupeKey: delivery.id },
        reconcileAt,
      );
  }

  for (const row of storedRows<Profile>(ctx, "profile:", 100)) {
    const profile = row.value;
    if (!Number.isFinite(profile.lastMetricsSuccess)) continue;
    const at = Number(profile.lastMetricsSuccess);
    await recordAdvancedSloEvent(
      env,
      workspace,
      "scheduled_metrics_capture",
      { dedupeKey: `${profile.id}:${at}` },
      at,
    );
    await recordAdvancedSloEvent(
      env,
      workspace,
      "advanced_operation",
      { dedupeKey: `metrics:${profile.id}:${at}` },
      at,
    );
  }
}

async function observeWorkspaceOperationalState(
  ctx: DurableObjectState,
  env: Env,
) {
  const workspace = storedWorkspace(ctx);
  if (!workspace) return;
  const now = Date.now();

  for (const row of oauthRows(ctx)) {
    const meta = row.value || {};
    if (Number.isFinite(meta.lastRefreshAt)) {
      const at = Number(meta.lastRefreshAt);
      await recordAdvancedSloEvent(
        env,
        workspace,
        "oauth_refresh_attempt",
        { dedupeKey: `${row.key}:${at}` },
        at,
      );
      await recordAdvancedSloEvent(
        env,
        workspace,
        "oauth_refresh_success",
        { dedupeKey: `${row.key}:${at}` },
        at,
      );
    } else if (
      meta.status === "refresh_failed" &&
      Number.isFinite(meta.nextRefreshAt)
    ) {
      await recordAdvancedSloEvent(
        env,
        workspace,
        "oauth_refresh_attempt",
        { dedupeKey: `${row.key}:failed:${meta.nextRefreshAt}` },
        now,
      );
    }

    if (
      !["refresh_failed", "reauthorization_required", "identity_drift", "expired"].includes(
        meta.status,
      )
    )
      continue;
    const code =
      meta.status === "identity_drift"
        ? "OAUTH_IDENTITY_DRIFT"
        : meta.status === "expired"
          ? "OAUTH_EXPIRED"
          : meta.status === "reauthorization_required"
            ? "OAUTH_REAUTHORIZE_REQUIRED"
            : /^[A-Z0-9_.:-]{1,80}$/.test(meta.lastError || "")
              ? meta.lastError
              : "OAUTH_REFRESH_FAILED";
    await enqueueOperationalAlert(
      env,
      {
        class: "provider_or_oauth_failure",
        severity:
          meta.status === "refresh_failed" || meta.status === "reauthorization_required"
            ? "warning"
            : "critical",
        code,
        subject: `${workspace}:${meta.provider || "unknown"}:${meta.alias || row.key}`,
        dedupe: `${workspace}:${row.key}:${meta.status}:${code}`,
        windowMs: 60 * 60_000,
      },
      now,
    );
  }

  await observeAdvancedProductState(ctx, env, workspace);

  const attempt = storedJson<{
    quote?: string;
    startedAt?: number;
    status?: string;
  }>(ctx, "billing:attempt");
  if (
    attempt?.status === "pending" &&
    Number.isFinite(attempt.startedAt) &&
    now - Number(attempt.startedAt) >= 15 * 60_000
  )
    await enqueueOperationalAlert(
      env,
      {
        class: "stripe_reconciliation",
        severity: "warning",
        code: "BILLING_RECONCILIATION_STALE",
        subject: `${workspace}:${attempt.quote || "pending"}`,
        dedupe: `${workspace}:${attempt.quote || "pending"}`,
        windowMs: 60 * 60_000,
      },
      now,
    );
}

function workspaceEnvironment(ctx: DurableObjectState, env: Env): Env {
  return new Proxy(env, {
    get(target, property, receiver) {
      if (property !== "ADVANCED_ENABLED")
        return Reflect.get(target, property, receiver);
      if (target.ADVANCED_ENABLED !== "true") return "false";
      const workspace = storedWorkspace(ctx);
      if (!workspace) return "false";
      return advancedRolloutDecision(target, workspace).eligible
        ? "true"
        : "false";
    },
  });
}

export class Workspace extends BaseWorkspace {
  private operationalCtx: DurableObjectState;
  private operationalEnv: Env;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, workspaceEnvironment(ctx, env));
    this.operationalCtx = ctx;
    this.operationalEnv = env;
    initialiseCapacityTelemetry(ctx);
  }

  async fetch(request: Request) {
    const path = new URL(request.url).pathname;
    if (path === "/capacity/snapshot") {
      let input: { workspace?: unknown } = {};
      try {
        input = (await request.json()) as { workspace?: unknown };
      } catch {
        return Response.json({ error: "invalid_request" }, { status: 400 });
      }
      const workspace = storedWorkspace(this.operationalCtx);
      if (!workspace || input.workspace !== workspace)
        return Response.json({ error: "workspace_mismatch" }, { status: 403 });
      return Response.json(
        workspaceCapacitySnapshot(
          this.operationalCtx,
          this.operationalEnv.RELEASE_SHA,
        ),
      );
    }

    let advancedOperation:
      | { workspace: string; name: string; dedupeKey?: string }
      | undefined;
    if (path === "/operation") {
      try {
        const body = (await request.clone().json()) as any;
        const operation = byName.get(body?.name);
        if (operation?.tier === "advanced" && typeof body?.workspace === "string")
          advancedOperation = {
            workspace: body.workspace,
            name: body.name,
            ...(body?.input?.idempotencyKey
              ? {
                  dedupeKey: `${body.name}:${body.actor?.id || "actor"}:${body.input.idempotencyKey}`,
                }
              : {}),
          };
      } catch {
        // The base worker remains the authority for request parsing.
      }
    }

    recordWorkspaceRequest(this.operationalCtx);
    const response = await super.fetch(request);
    if (response.ok && advancedOperation)
      await recordAdvancedSloEvent(
        this.operationalEnv,
        advancedOperation.workspace,
        "advanced_operation",
        advancedOperation.dedupeKey
          ? { dedupeKey: advancedOperation.dedupeKey }
          : {},
      );
    return response;
  }

  async alarm() {
    recordAlarmCycle(this.operationalCtx);
    const result = await super.alarm();
    try {
      await observeWorkspaceOperationalState(
        this.operationalCtx,
        this.operationalEnv,
      );
    } catch (error) {
      console.error(
        JSON.stringify({
          event: "workspace_operational_alert_observation_failed",
          release: this.operationalEnv.RELEASE_SHA,
          code: error instanceof Error ? error.name : "UnknownError",
        }),
      );
    }
    return result;
  }
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext) {
    return edge.fetch(request, env, ctx);
  },
  async scheduled(
    controller: ScheduledController,
    env: Env,
    _ctx: ExecutionContext,
  ) {
    if (controller.cron === "17 * * * *") {
      await edge.scheduled(controller, env);
      try {
        const result = await sweepWorkspaceCapacityObservations(env);
        if (!result.complete || result.failed > 0)
          console.warn(
            JSON.stringify({
              event: "workspace_capacity_observation_incomplete",
              release: env.RELEASE_SHA,
              totalRegistered: result.totalRegistered,
              considered: result.considered,
              observed: result.observed,
              failed: result.failed,
            }),
          );
      } catch (error) {
        console.error(
          JSON.stringify({
            event: "workspace_capacity_observation_failed",
            release: env.RELEASE_SHA,
            code: error instanceof Error ? error.name : "UnknownError",
          }),
        );
      }
      await pruneAdvancedSloTelemetry(env);
    }

    try {
      await sweepOperationalConditions(env);
    } catch (error) {
      console.error(
        JSON.stringify({
          event: "operational_alert_sweep_failed",
          release: env.RELEASE_SHA,
          code: error instanceof Error ? error.name : "UnknownError",
        }),
      );
    }
    try {
      const result = await flushOperationalAlerts(env);
      if (result.dead > 0)
        console.error(
          JSON.stringify({
            event: "operational_alert_delivery_dead",
            release: env.RELEASE_SHA,
            count: result.dead,
          }),
        );
    } catch (error) {
      console.error(
        JSON.stringify({
          event: "operational_alert_flush_failed",
          release: env.RELEASE_SHA,
          code: error instanceof Error ? error.name : "UnknownError",
        }),
      );
    }
  },
} satisfies ExportedHandler<Env>;
