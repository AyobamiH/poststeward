import type { Env } from "./types.ts";
import baseEdge, { Workspace as BaseWorkspace } from "./edge.ts";
import {
  rewrapWorkspaceCredentials,
  rootRotationMaintenance,
  rootRotationMode,
  runRootRotation,
  verifyWorkspaceCredentials,
} from "./root-rotation-runtime.ts";
import { Fault, json, requireValue } from "./common.ts";

function internalState(instance: BaseWorkspace) {
  const value = instance as any;
  return {
    env: value.env as Env,
    ctx: value.ctx as DurableObjectState,
    store: value.store,
  };
}

function maintenanceResponse(env: Env) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=31536000",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
  });
  return new Response(
    JSON.stringify({
      error: {
        code: "ROOT_ROTATION_IN_PROGRESS",
        message:
          "Protected credential maintenance is in progress. Retry after the reviewed cutover completes.",
      },
      release: env.RELEASE_SHA,
    }),
    { status: 503, headers },
  );
}

function publicRequestBlockedDuringRotation(path: string) {
  return (
    path.startsWith("/api/") ||
    path === "/mcp" ||
    path.startsWith("/payments/") ||
    path.startsWith("/connections/") ||
    path.startsWith("/sources/github/") ||
    path === "/auth/login" ||
    path === "/auth/callback"
  );
}

/**
 * Preserve the lifecycle-aware Durable Object class identity while adding two
 * internal-only root-rotation operations. No public Worker route maps to these
 * paths. The same wrapper also fences every normal workspace operation while
 * protected credentials are between roots.
 */
export class Workspace extends BaseWorkspace {
  async fetch(request: Request): Promise<Response> {
    const state = internalState(this);
    const path = new URL(request.url).pathname;
    if (path === "/root-rotation/rewrap" || path === "/root-rotation/verify") {
      try {
        requireValue(
          request.method === "POST",
          "METHOD_NOT_ALLOWED",
          "Root rotation internal operations require POST.",
          405,
        );
        const data = (await request.json()) as {
          workspace?: unknown;
          input?: { rotationId?: unknown };
        };
        requireValue(
          typeof data.workspace === "string" &&
            data.input?.rotationId === state.env.ROOT_ROTATION_ID,
          "ROOT_ROTATION_INTERNAL_INVALID",
          "Root rotation internal request does not match the reviewed run.",
          403,
        );
        const known = state.store.get("workspace") as string | undefined;
        requireValue(
          !known || known === data.workspace,
          "WORKSPACE_MISMATCH",
          "Root rotation object identity mismatch.",
          403,
        );
        const result =
          path === "/root-rotation/rewrap"
            ? await rewrapWorkspaceCredentials(
                state.store,
                state.env,
                data.workspace,
              )
            : await verifyWorkspaceCredentials(
                state.store,
                state.env,
                data.workspace,
              );
        return json(result);
      } catch (error) {
        if (error instanceof Fault)
          return json(
            { error: { code: error.code, message: error.message } },
            error.status,
          );
        return json(
          {
            error: {
              code: "ROOT_ROTATION_INTERNAL_FAILED",
              message: "Root rotation workspace operation failed closed.",
            },
          },
          503,
        );
      }
    }
    if (rootRotationMaintenance(state.env)) return maintenanceResponse(state.env);
    return super.fetch(request);
  }

  async alarm() {
    const state = internalState(this);
    if (rootRotationMaintenance(state.env)) {
      await state.ctx.storage.setAlarm(Date.now() + 60_000);
      console.warn(
        JSON.stringify({
          event: "workspace_alarm_root_rotation_fenced",
          mode: rootRotationMode(state.env),
          release: state.env.RELEASE_SHA,
        }),
      );
      return;
    }
    return super.alarm();
  }
}

export default {
  async scheduled(controller: ScheduledController, env: Env, _ctx: ExecutionContext) {
    if (rootRotationMaintenance(env)) {
      try {
        const report = await runRootRotation(env);
        console.warn(
          JSON.stringify({
            event: "root_rotation_progress",
            release: env.RELEASE_SHA,
            rotationId: report.rotationId,
            mode: report.mode,
            status: report.status,
            batch: report.batch,
            totals: report.totals,
          }),
        );
      } catch (error) {
        console.error(
          JSON.stringify({
            event: "root_rotation_failed",
            release: env.RELEASE_SHA,
            code: error instanceof Fault ? error.code : "ROOT_ROTATION_FAILED",
          }),
        );
        throw error;
      }
    }
    return baseEdge.scheduled(controller, env);
  },

  async fetch(request: Request, env: Env, ctx: ExecutionContext) {
    const path = new URL(request.url).pathname;
    if (
      rootRotationMaintenance(env) &&
      publicRequestBlockedDuringRotation(path)
    )
      return maintenanceResponse(env);
    return baseEdge.fetch(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;
