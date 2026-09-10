import { z } from "zod";
import base, { Workspace as BaseWorkspace } from "./worker.ts";
import { authenticate } from "./auth.ts";
import { errorResponse, json, requireValue } from "./common.ts";
import {
  assertWorkspaceNotDeleted,
  beginWorkspaceDeletion,
  completeWorkspaceDeletion,
  workspaceDeletion,
} from "./lifecycle.ts";
import { workspaceQuarantined } from "./effects.ts";
import { ownerAuthority, demandFreshOwner } from "./owner-proof.ts";
import { assertRecoveryCanResume, recoveryStatus } from "./recovery.ts";
import { boundedBody, limitEdge } from "./security.ts";
import type { Actor, Env } from "./types.ts";

const deletionInput = z.strictObject({
  delete: z.literal(true),
  confirmation: z.string().min(1).max(200),
});

function clearSessionCookie() {
  return "__Host-session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0";
}

function secure(response: Response, requestId: string) {
  const headers = new Headers(response.headers);
  headers.set("X-Request-ID", requestId);
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Strict-Transport-Security", "max-age=31536000");
  headers.set("X-Frame-Options", "DENY");
  headers.set("Referrer-Policy", "no-referrer");
  headers.set("Origin-Agent-Cluster", "?1");
  headers.set("Permissions-Policy", "tools=(self)");
  headers.set(
    "Content-Security-Policy",
    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  );
  if (response.status === 401)
    headers.set("WWW-Authenticate", 'Bearer realm="poststeward"');
  return new Response(response.body, { status: response.status, headers });
}

export class Workspace extends BaseWorkspace {
  private lifecycleCtx: DurableObjectState;
  private lifecycleEnv: Env;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.lifecycleCtx = ctx;
    this.lifecycleEnv = env;
  }

  private storedWorkspace() {
    const row = this.lifecycleCtx.storage.sql
      .exec<{ value: string }>(
        "SELECT value FROM records WHERE key='workspace' LIMIT 1",
      )
      .toArray()[0];
    if (!row) return undefined;
    try {
      const value = JSON.parse(row.value);
      return typeof value === "string" ? value : undefined;
    } catch {
      return undefined;
    }
  }

  async fetch(request: Request): Promise<Response> {
    try {
      const path = new URL(request.url).pathname;
      const clone = request.clone();
      let envelope: { workspace?: unknown; actor?: Actor } = {};
      try {
        envelope = (await clone.json()) as typeof envelope;
      } catch {
        return super.fetch(request);
      }
      const workspace =
        typeof envelope.workspace === "string" ? envelope.workspace : "";
      if (path === "/lifecycle/delete") {
        requireValue(
          workspace &&
            envelope.actor?.workspace === workspace &&
            !envelope.actor.grant &&
            envelope.actor.scopes?.includes("admin"),
          "OWNER_SESSION_REQUIRED",
          "Workspace deletion requires the signed-in owner.",
          403,
        );
        const deletion = await workspaceDeletion(
          this.lifecycleEnv.IDENTITY,
          workspace,
        );
        requireValue(
          deletion?.state === "pending",
          "WORKSPACE_DELETE_NOT_PENDING",
          "Workspace deletion has not been durably started.",
          409,
        );
        await this.lifecycleCtx.storage.deleteAlarm();
        this.lifecycleCtx.storage.sql.exec("DELETE FROM records");
        await this.lifecycleCtx.storage.deleteAll();
        return json({ cleared: true });
      }
      if (workspace)
        await assertWorkspaceNotDeleted(this.lifecycleEnv.IDENTITY, workspace);
      return super.fetch(request);
    } catch (error) {
      return errorResponse(error);
    }
  }

  async alarm() {
    const workspace = this.storedWorkspace();
    if (
      workspace &&
      (await workspaceDeletion(this.lifecycleEnv.IDENTITY, workspace))
    ) {
      await this.lifecycleCtx.storage.deleteAlarm();
      console.warn(
        JSON.stringify({
          event: "workspace_alarm_suppressed_for_deletion",
          workspace,
        }),
      );
      return;
    }
    return super.alarm();
  }
}

async function lifecycleRoute(request: Request, env: Env) {
  const url = new URL(request.url);
  requireValue(
    url.origin === env.PUBLIC_ORIGIN && !env.PUBLIC_ORIGIN.includes(".invalid"),
    "HOST_REJECTED",
    "This hostname is not configured.",
    403,
  );
  if (request.headers.has("origin"))
    requireValue(
      request.headers.get("origin") === env.PUBLIC_ORIGIN,
      "ORIGIN_REJECTED",
      "Cross-origin requests are not allowed.",
      403,
    );
  await limitEdge(request, env);
  if (request.body)
    requireValue(
      /^application\/json(?:\s*;|$)/i.test(
        request.headers.get("content-type") || "",
      ),
      "JSON_REQUIRED",
      "Use application/json.",
      415,
    );
  request = await boundedBody(request, 32768);
  const auth = await authenticate(request, env);
  requireValue(
    auth.browser && !auth.actor.grant && auth.actor.scopes.includes("admin"),
    "OWNER_SESSION_REQUIRED",
    "Workspace lifecycle controls are available only to the signed-in owner browser.",
    403,
  );
  const authority = await ownerAuthority(request, env, auth);
  const path = url.pathname;

  if (path === "/api/lifecycle/status" && request.method === "GET") {
    const deletion = await workspaceDeletion(env.IDENTITY, auth.actor.workspace);
    return json({
      workspace: auth.actor.workspace,
      deletion: deletion
        ? {
            state: deletion.state,
            requestedAt: deletion.requested_at,
            completedAt: deletion.completed_at || null,
          }
        : null,
    });
  }

  requireValue(
    path === "/api/lifecycle/delete" && request.method === "POST",
    "NOT_FOUND",
    "Unknown lifecycle route or HTTP method.",
    404,
  );
  demandFreshOwner(authority, Date.now());
  const parsed = deletionInput.safeParse(await request.json());
  requireValue(
    parsed.success,
    "INVALID_INPUT",
    "Workspace deletion requires explicit confirmation.",
    400,
  );
  requireValue(
    parsed.data.confirmation === `DELETE ${auth.actor.workspace}`,
    "DELETE_CONFIRMATION_MISMATCH",
    "Type the exact workspace deletion phrase shown by the application.",
    409,
  );

  const existingDeletion = await workspaceDeletion(
    env.IDENTITY,
    auth.actor.workspace,
  );
  requireValue(
    existingDeletion?.state !== "completed",
    "WORKSPACE_DELETED",
    "This workspace has already been deleted.",
    410,
  );
  if (!existingDeletion) {
    const control = await workspaceQuarantined(
      env.IDENTITY,
      auth.actor.workspace,
    );
    const recovery = await recoveryStatus(env.IDENTITY, auth.actor.workspace);
    requireValue(
      !control.quarantined &&
        !["prepared", "armed"].includes(recovery.plan?.state || ""),
      "RECOVERY_ACTIVE",
      "Cancel or finish active workspace recovery before erasing the workspace.",
      409,
    );
    await beginWorkspaceDeletion(env.IDENTITY, auth.actor.workspace);
  }

  await assertRecoveryCanResume(env.IDENTITY, auth.actor.workspace);

  const stub = env.WORKSPACES.get(
    env.WORKSPACES.idFromName(auth.actor.workspace),
  );
  const cleared = await stub.fetch("https://workspace.internal/lifecycle/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace: auth.actor.workspace,
      actor: auth.actor,
      input: {},
      name: "",
    }),
  });
  requireValue(
    cleared.ok,
    "WORKSPACE_DELETE_RETRY_REQUIRED",
    "Workspace deletion is fenced but local state was not fully erased. Retry the same deletion request; no other workspace action is permitted.",
    503,
  );
  await completeWorkspaceDeletion(env.IDENTITY, auth.actor.workspace);
  return json(
    {
      deleted: true,
      workspace: auth.actor.workspace,
      durableObjectCleared: true,
      identityStateCleared: true,
      tombstoneRetained: true,
      note:
        "Provider-side grants may remain until revoked at the provider; PostSteward no longer stores or can use the deleted credentials.",
    },
    200,
    { "Set-Cookie": clearSessionCookie() },
  );
}

function authenticatedProductPath(path: string) {
  return (
    path.startsWith("/api/") ||
    path === "/mcp" ||
    path.startsWith("/payments/") ||
    path === "/auth/logout" ||
    /^\/connections\/oauth\/(x|threads|linkedin)\/callback$/.test(path)
  );
}

async function fenceDeletedWorkspaceRequest(
  request: Request<any, any>,
  env: Env,
) {
  const path = new URL(request.url).pathname;
  if (path.startsWith("/api/lifecycle/") || !authenticatedProductPath(path))
    return;

  let auth: Awaited<ReturnType<typeof authenticate>>;
  try {
    auth = await authenticate(request.clone(), env);
  } catch {
    return;
  }

  const deletion = await workspaceDeletion(env.IDENTITY, auth.actor.workspace);
  if (!deletion) return;
  if (
    auth.browser &&
    (path === "/api/session" || path === "/auth/logout")
  )
    return;

  try {
    await assertWorkspaceNotDeleted(env.IDENTITY, auth.actor.workspace);
  } catch (error) {
    return errorResponse(error);
  }
}

export default {
  async scheduled(controller: ScheduledController, env: Env) {
    return base.scheduled(controller, env);
  },
  async fetch(request: Request, env: Env, ctx: ExecutionContext) {
    const path = new URL(request.url).pathname;
    if (!path.startsWith("/api/lifecycle/")) {
      const fenced = await fenceDeletedWorkspaceRequest(request, env);
      return fenced || base.fetch(request, env, ctx);
    }
    const requestId = crypto.randomUUID();
    try {
      return secure(await lifecycleRoute(request, env), requestId);
    } catch (error) {
      return secure(errorResponse(error), requestId);
    }
  },
} satisfies ExportedHandler<Env>;
