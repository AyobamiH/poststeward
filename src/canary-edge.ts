import edge, { Workspace as BaseWorkspace } from "./edge.ts";
import { advancedRolloutDecision } from "./advanced-rollout.ts";
import {
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
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, workspaceEnvironment(ctx, env));
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
