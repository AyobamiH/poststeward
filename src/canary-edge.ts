import edge, { Workspace as BaseWorkspace } from "./edge.ts";
import { advancedRolloutDecision } from "./advanced-rollout.ts";
import {
  capacityObservationDue,
  initialiseCapacityTelemetry,
  recordAlarmCycle,
  recordWorkspaceRequest,
  sweepWorkspaceCapacityObservations,
  workspaceCapacitySnapshot,
} from "./capacity-observation.ts";
import { byName } from "./operations/catalog.ts";
import {
  classifyAutomaticPublication,
  parseCompleteSloRecords,
} from "./slo-producer-evidence.ts";
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

function completeSloRows<T>(
  ctx: DurableObjectState,
  prefix: string,
  limit = 501,
) {
  try {
    const raw = ctx.storage.sql
      .exec<{ key: string; value: string }>(
        "SELECT key,value FROM records WHERE key LIKE ? ORDER BY key LIMIT ?",
        `${prefix}%`,
        limit,
      )
      .toArray();
    return parseCompleteSloRecords<T>(raw, limit);
  } catch {
    return undefined;
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
  const now = Date.now();
  // Prove all source denominators are complete BEFORE writing any evidence.
  // Never silently skip a corrupt record or the 501st row.
  const campaigns = completeSloRows<Campaign>(ctx, "campaign:");
  const allDeliveries = completeSloRows<Delivery>(ctx, "delivery:");
  const profiles = completeSloRows<Profile>(ctx, "profile:");
  if (!campaigns || !allDeliveries || !profiles) return;
  const effects = await env.IDENTITY.prepare(
    `SELECT delivery_id,status,post_id,published_at,updated_at
       FROM external_effects WHERE workspace=? ORDER BY created_at LIMIT 1000`,
  )
    .bind(workspace)
    .all<{
      delivery_id: string;
      status: string;
      post_id: string | null;
      published_at: number | null;
      updated_at: number;
    }>();
  if (!Array.isArray(effects.results) || effects.results.length >= 1000) return;
  const effectByDelivery = new Map(
    effects.results.map((row) => [row.delivery_id, row] as const),
  );
  // Two fingerprints for the same delivery are ambiguous and must never
  // produce a single favourable publication or provider-readback receipt.
  if (
    effectByDelivery.size !== effects.results.length ||
    effects.results.some((row) => !row.delivery_id)
  )
    return;

  // Retrospective state observations never count completed Advanced commands.
  for (const row of campaigns) {
    const campaign = row.value;
    if (!campaign.source || !Number.isFinite(campaign.createdAt)) continue;
    const key = campaign.id || row.key;
    for (const event of ["source_change", "inventory_snapshot"] as const)
      await recordAdvancedSloEvent(
        env,
        workspace,
        event,
        {
          dedupeKey: key,
          evidenceOrigin: "observed_campaign",
          evidenceOutcome: "observed",
        },
        campaign.createdAt,
      );
  }
  const automatic = allDeliveries
    .map((row) => row.value)
    .filter((delivery) => delivery.automatic);
  for (const delivery of automatic) {
    if (!Number.isFinite(delivery.createdAt)) continue;
    await recordAdvancedSloEvent(
      env,
      workspace,
      "spaced_allocation",
      {
        dedupeKey: delivery.id,
        evidenceOrigin: "observed_allocation",
        evidenceOutcome: "observed",
      },
      delivery.createdAt,
    );

    const receipt = classifyAutomaticPublication(
      delivery,
      effectByDelivery.get(delivery.id),
      now,
    );
    if (!receipt.eligible) continue;
    // Denominator is defined by every due automatic delivery, even when
    // no provider post ID exists or the write is uncertain.
    await recordAdvancedSloEvent(
      env,
      workspace,
      "publication_eligible",
      {
        dedupeKey: delivery.id,
        evidenceOrigin: "due_delivery",
        evidenceOutcome: "eligible",
      },
      receipt.dueAt!,
    );
    if (!receipt.published) continue;
    await recordAdvancedSloEvent(
      env,
      workspace,
      "schedule_observation",
      {
        dedupeKey: delivery.id,
        durationMs: receipt.publishedAt! - receipt.dueAt!,
        evidenceOrigin: "provider_write_completed",
        evidenceOutcome: "succeeded",
      },
      receipt.publishedAt!,
    );
    if (!receipt.verified) continue;
    await recordAdvancedSloEvent(
      env,
      workspace,
      "provider_reconciliation",
      {
        dedupeKey: delivery.id,
        durationMs: receipt.verifiedAt! - receipt.publishedAt!,
        evidenceOrigin: "provider_verified",
        evidenceOutcome: "verified",
      },
      receipt.verifiedAt!,
    );
    await recordAdvancedSloEvent(
      env,
      workspace,
      "verified_readback",
      {
        dedupeKey: delivery.id,
        evidenceOrigin: "provider_verified",
        evidenceOutcome: "verified",
      },
      receipt.verifiedAt!,
    );
  }

  for (const row of profiles) {
    const profile = row.value;
    if (!Number.isFinite(profile.lastMetricsSuccess)) continue;
    const at = Number(profile.lastMetricsSuccess);
    await recordAdvancedSloEvent(
      env,
      workspace,
      "scheduled_metrics_capture",
      {
        dedupeKey: `${profile.id}:${at}`,
        evidenceOrigin: "observed_metrics",
        evidenceOutcome: "observed",
      },
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
    // OAuth outcome telemetry is recorded at actual refresh attempt/commit
    // in provider-oauth.ts. Reconstructing it from mutable lastRefreshAt
    // misses later failures and can fabricate a 100% success ratio.
    if (
      ![
        "refresh_failed",
        "reauthorization_required",
        "identity_drift",
        "expired",
      ].includes(meta.status)
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
          meta.status === "refresh_failed" ||
          meta.status === "reauthorization_required"
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
      const requested =
        typeof input.workspace === "string" &&
        input.workspace.length >= 1 &&
        input.workspace.length <= 128 &&
        !/[\s\x00-\x1f]/.test(input.workspace)
          ? input.workspace
          : "";
      if (!requested)
        return Response.json({ error: "invalid_workspace" }, { status: 400 });
      const workspace = storedWorkspace(this.operationalCtx);
      // Admission creates the authoritative D1 registry row before a customer
      // necessarily invokes the workspace Durable Object. The sweep addresses
      // this object by idFromName(requested), so an uninitialised object may be
      // observed without manufacturing business state. Once local identity
      // exists, mismatch remains fail-closed.
      if (workspace && requested !== workspace)
        return Response.json({ error: "workspace_mismatch" }, { status: 403 });
      return Response.json(
        workspaceCapacitySnapshot(
          this.operationalCtx,
          this.operationalEnv.RELEASE_SHA,
        ),
      );
    }

    let advancedOperation:
      { workspace: string; name: string; dedupeKey?: string } | undefined;
    if (path === "/operation") {
      try {
        const body = (await request.clone().json()) as any;
        const operation = byName.get(body?.name);
        if (
          operation?.tier === "advanced" &&
          typeof body?.workspace === "string"
        )
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
    // The accepted operation must belong to this Durable Object, not merely
    // to a workspace string supplied by the HTTP request body.
    if (
      response.ok &&
      advancedOperation &&
      storedWorkspace(this.operationalCtx) === advancedOperation.workspace
    )
      await recordAdvancedSloEvent(
        this.operationalEnv,
        advancedOperation.workspace,
        "advanced_operation",
        advancedOperation.dedupeKey
          ? {
              dedupeKey: advancedOperation.dedupeKey,
              evidenceOrigin: "completed_operation",
              evidenceOutcome: "succeeded",
            }
          : {
              evidenceOrigin: "unidentified_operation",
              evidenceOutcome: "unverified",
            },
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
    const hourly = controller.cron === "17 * * * *";
    if (hourly) {
      try {
        await edge.scheduled(controller, env);
      } catch (error) {
        console.error(
          JSON.stringify({
            event: "hourly_identity_maintenance_failed",
            release: env.RELEASE_SHA,
            code: error instanceof Error ? error.name : "UnknownError",
          }),
        );
      }
    }

    let observeCapacity = hourly;
    if (!observeCapacity && controller.cron === "*/5 * * * *") {
      try {
        observeCapacity = await capacityObservationDue(env);
      } catch (error) {
        console.error(
          JSON.stringify({
            event: "workspace_capacity_due_check_failed",
            release: env.RELEASE_SHA,
            code: error instanceof Error ? error.name : "UnknownError",
          }),
        );
      }
    }
    if (observeCapacity) {
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
    }
    if (hourly) await pruneAdvancedSloTelemetry(env);

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
