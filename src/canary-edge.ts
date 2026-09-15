import edge, { Workspace as BaseWorkspace } from "./edge.ts";
import { advancedRolloutDecision } from "./advanced-rollout.ts";
import {
  enqueueOperationalAlert,
  flushOperationalAlerts,
  sweepOperationalConditions,
} from "./operational-alerts.ts";
import type { Env } from "./types.ts";

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

function oauthRows(ctx: DurableObjectState) {
  try {
    return ctx.storage.sql
      .exec<{ key: string; value: string }>(
        "SELECT key,value FROM records WHERE key LIKE 'oauth:%' ORDER BY key LIMIT 100",
      )
      .toArray()
      .flatMap((row) => {
        try {
          return [{ key: row.key, value: JSON.parse(row.value) as any }];
        } catch {
          return [];
        }
      });
  } catch {
    return [];
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

/**
 * Keep ADVANCED_ENABLED as a global emergency kill switch while presenting it
 * as enabled only to a deterministically selected workspace. The underlying
 * environment object is never mutated or shared with another tenant.
 */
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
  private alertCtx: DurableObjectState;
  private alertEnv: Env;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, workspaceEnvironment(ctx, env));
    this.alertCtx = ctx;
    this.alertEnv = env;
  }

  async alarm() {
    const result = await super.alarm();
    try {
      await observeWorkspaceOperationalState(this.alertCtx, this.alertEnv);
    } catch (error) {
      console.error(
        JSON.stringify({
          event: "workspace_operational_alert_observation_failed",
          release: this.alertEnv.RELEASE_SHA,
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
    ctx: ExecutionContext,
  ) {
    // Identity expiry remains on its established hourly cadence. The five-minute
    // trigger only sweeps and delivers the durable alert outbox.
    if (controller.cron === "17 * * * *")
      await edge.scheduled(controller, env, ctx);

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
