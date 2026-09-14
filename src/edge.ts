import { z } from "zod";
import { cutoverWorkspace, rootCutoverRoute } from "./root-cutover.ts";
import { exportRetention, commitRetention } from "./retention.ts";
import { Billing } from "./billing.ts";
import { SQLiteStore } from "./store.ts";
import base, { Workspace as BaseWorkspace } from "./worker.ts";
import { authenticate } from "./auth.ts";
import { errorResponse, Fault, json, requireValue } from "./common.ts";
import {
  assertWorkspaceNotDeleted,
  beginWorkspaceDeletion,
  completeWorkspaceDeletion,
  workspaceDeletion,
} from "./lifecycle.ts";
import {
  setWorkspaceQuarantine,
  workspaceQuarantined,
} from "./effects.ts";
import { githubSourceRoute, isGitHubSourcePath } from "./github-source-routes.ts";
import { ownerAuthority, demandFreshOwner } from "./owner-proof.ts";
import {
  armRecoveryPlan,
  assertRecoveryCanResume,
  prepareRecoveryPlan,
  recoveryStatus,
  requireRecoveryPlan,
} from "./recovery.ts";
import {
  captureWorkspaceRecoveryCheckpoint,
  listRecoveryCheckpoints,
  requireRecoveryCheckpoint,
} from "./recovery-checkpoints.ts";
import {
  captureCurrentRecoveryBookmark,
  type RecoveryPitrStorage,
} from "./recovery-pitr.ts";
import { boundedBody, limitEdge } from "./security.ts";
import type { Actor, Env } from "./types.ts";

const deletionInput = z.strictObject({
  delete: z.literal(true),
  confirmation: z.string().min(1).max(200),
});
const exactRecoveryPrepareInput = z.strictObject({
  checkpoint: z.uuid(),
  reason: z.string().min(3).max(240),
});
const checkpointCaptureInput = z.strictObject({
  capture: z.literal(true),
});
const automaticCheckpointMs = 6 * 60 * 60 * 1000;
const checkpointRetryMs = 15 * 60 * 1000;

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

async function workspaceInvoke(
  env: Env,
  actor: Actor,
  path: string,
  input: unknown,
) {
  return env.WORKSPACES.get(env.WORKSPACES.idFromName(actor.workspace)).fetch(
    "https://workspace.internal" + path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace: actor.workspace,
        actor,
        name: "",
        input,
      }),
    },
  );
}

async function internalValue(response: Response) {
  const value: any = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Fault(
      value.error?.code || "RECOVERY_INTERNAL_FAILED",
      value.error?.message || "Recovery coordination did not complete.",
      response.status,
    );
  return value;
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

  private checkpointStorage(requireRestore = false) {
    const storage = this.lifecycleCtx.storage as DurableObjectStorage &
      RecoveryPitrStorage & {
        onNextSessionRestoreBookmark?: (bookmark: string) => Promise<string>;
      };
    requireValue(
      typeof storage.getCurrentBookmark === "function" &&
        (!requireRestore ||
          typeof storage.onNextSessionRestoreBookmark === "function"),
      "RECOVERY_PITR_UNAVAILABLE",
      "Exact point-in-time recovery is unavailable in this Durable Object runtime.",
      501,
    );
    return storage;
  }

  private ownerEnvelope(
    workspace: string,
    actor: Actor | undefined,
    message = "Workspace recovery is restricted to the signed-in owner.",
  ) {
    requireValue(
      workspace &&
        actor?.workspace === workspace &&
        !actor.grant &&
        actor.scopes?.includes("admin"),
      "OWNER_SESSION_REQUIRED",
      message,
      403,
    );
  }

  private async maybeCaptureCheckpoint(workspace: string) {
    const store = new SQLiteStore(this.lifecycleCtx.storage);
    if (store.get<string>("workspace") !== workspace) return;
    const now = Date.now();
    const next = store.get<number>("recovery:checkpoint:next") || 0;
    if (next > now) return;
    const control = await workspaceQuarantined(this.lifecycleEnv.IDENTITY, workspace);
    if (control.quarantined) {
      store.put("recovery:checkpoint:next", now + checkpointRetryMs);
      return;
    }
    try {
      const previousRelease = store.get<string>("recovery:checkpoint:release");
      await captureWorkspaceRecoveryCheckpoint(
        this.lifecycleEnv.IDENTITY,
        store,
        this.checkpointStorage(),
        this.lifecycleEnv,
        workspace,
        previousRelease === this.lifecycleEnv.RELEASE_SHA ? "automatic" : "release",
        now,
      );
      store.put("recovery:checkpoint:release", this.lifecycleEnv.RELEASE_SHA);
      store.put("recovery:checkpoint:next", now + automaticCheckpointMs);
    } catch (error) {
      store.put("recovery:checkpoint:next", now + checkpointRetryMs);
      console.warn(
        JSON.stringify({
          event: "workspace_recovery_checkpoint_deferred",
          workspace,
          code: error instanceof Fault ? error.code : "CHECKPOINT_CAPTURE_FAILED",
          at: now,
        }),
      );
    }
  }

  async fetch(request: Request): Promise<Response> {
    try {
      const path = new URL(request.url).pathname;
      const clone = request.clone();
      let envelope: { workspace?: unknown; actor?: Actor; input?: any; release?: string } = {};
      try {
        envelope = (await clone.json()) as typeof envelope;
      } catch {
        return super.fetch(request);
      }
      const workspace =
        typeof envelope.workspace === "string" ? envelope.workspace : "";
      if (path === "/maintenance/root-cutover") {
        requireValue(workspace && envelope.release === this.lifecycleEnv.RELEASE_SHA &&
          ["inspect", "migrate"].includes(envelope.input?.action),
          "ROOT_RELEASE_MISMATCH", "Root cutover requires the exact deployed workspace release.", 409);
        return this.lifecycleCtx.blockConcurrencyWhile(async () => {
          try {
            return json(await cutoverWorkspace(new SQLiteStore(this.lifecycleCtx.storage), this.lifecycleEnv, workspace, envelope.input));
          } catch (error) { return errorResponse(error); }
        });
      }
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
        const billing = new Billing(new SQLiteStore(this.lifecycleCtx.storage), this.lifecycleEnv, workspace);
        await billing.clearForDeletion();
        await this.lifecycleCtx.storage.deleteAlarm();
        this.lifecycleCtx.storage.sql.exec("DELETE FROM records");
        await this.lifecycleCtx.storage.deleteAll();
        return json({ cleared: true });
      }
      if (workspace)
        await assertWorkspaceNotDeleted(this.lifecycleEnv.IDENTITY, workspace);
      if (path === "/lifecycle/retention/export" || path === "/lifecycle/retention/prune") {
        requireValue(workspace && envelope.actor?.workspace === workspace &&
          !envelope.actor.grant && envelope.actor.scopes?.includes("admin"),
          "OWNER_SESSION_REQUIRED", "Retention requires the signed-in owner.", 403);
        const store = new SQLiteStore(this.lifecycleCtx.storage);
        requireValue(store.get("workspace") === workspace, "WORKSPACE_MISMATCH", "Open your workspace first.", 409);
        return json(path.endsWith("/export") ? await exportRetention(store) :
          await commitRetention(store, envelope.input));
      }
      if (path === "/recovery/checkpoint") {
        this.ownerEnvelope(workspace, envelope.actor);
        const store = new SQLiteStore(this.lifecycleCtx.storage);
        requireValue(
          store.get<string>("workspace") === workspace,
          "RECOVERY_WORKSPACE_UNINITIALIZED",
          "Recovery requires an existing initialized workspace.",
          409,
        );
        const control = await workspaceQuarantined(this.lifecycleEnv.IDENTITY, workspace);
        requireValue(
          !control.quarantined,
          "RECOVERY_QUARANTINED",
          "Finish or cancel active recovery before capturing another checkpoint.",
          409,
        );
        return json(
          await captureWorkspaceRecoveryCheckpoint(
            this.lifecycleEnv.IDENTITY,
            store,
            this.checkpointStorage(),
            this.lifecycleEnv,
            workspace,
            "owner",
          ),
        );
      }
      if (path === "/recovery/current-bookmark") {
        this.ownerEnvelope(workspace, envelope.actor);
        const store = new SQLiteStore(this.lifecycleCtx.storage);
        requireValue(
          store.get<string>("workspace") === workspace,
          "RECOVERY_WORKSPACE_UNINITIALIZED",
          "Recovery requires an existing initialized workspace.",
          409,
        );
        const control = await workspaceQuarantined(this.lifecycleEnv.IDENTITY, workspace);
        requireValue(
          control.quarantined,
          "RECOVERY_NOT_QUARANTINED",
          "Quarantine the workspace before preparing an exact restore.",
          409,
        );
        return json({
          currentBookmark: await captureCurrentRecoveryBookmark(
            this.checkpointStorage(),
          ),
        });
      }
      if (path === "/recovery/restore") {
        const input = envelope.input as { id?: string; digest?: string };
        if (workspace && envelope.actor && input?.id && input?.digest) {
          const plan = await requireRecoveryPlan(this.lifecycleEnv.IDENTITY, {
            id: input.id,
            digest: input.digest,
            workspace,
            actor: envelope.actor.id,
            states: ["prepared"],
          });
          if ((plan.target_mode || "approximate_time") === "exact_checkpoint") {
            this.ownerEnvelope(workspace, envelope.actor);
            const control = await workspaceQuarantined(this.lifecycleEnv.IDENTITY, workspace);
            requireValue(
              control.quarantined,
              "RECOVERY_NOT_QUARANTINED",
              "Recovery quarantine changed before restore.",
              409,
            );
            const storage = this.checkpointStorage(true);
            const undoBookmark = await storage.onNextSessionRestoreBookmark!(
              plan.target_bookmark,
            );
            await armRecoveryPlan(this.lifecycleEnv.IDENTITY, plan, undoBookmark);
            this.lifecycleCtx.abort("Workspace exact checkpoint recovery armed", {
              retryAlarm: false,
            });
          }
        }
      }
      const response = await super.fetch(request);
      if (
        workspace &&
        response.ok &&
        ["/operation", "/connect", "/oauth/connect", "/payment", "/billing/reconcile"].includes(path)
      )
        await this.maybeCaptureCheckpoint(workspace);
      return response;
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
    const result = await super.alarm();
    if (workspace) await this.maybeCaptureCheckpoint(workspace);
    return result;
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

  if ((path === "/api/lifecycle/retention/export" || path === "/api/lifecycle/retention/prune") && request.method === "POST") {
    const authority = await ownerAuthority(request, env, auth);
    demandFreshOwner(authority, Date.now());
    await assertWorkspaceNotDeleted(env.IDENTITY, auth.actor.workspace);
    const control = await workspaceQuarantined(env.IDENTITY, auth.actor.workspace);
    requireValue(!control.quarantined, "RETENTION_FENCED", "Finish recovery before retention cleanup.", 409);
    const input = path.endsWith("/prune") ? z.strictObject({
      cutoff: z.number().finite(), digest: z.string().regex(/^[a-f0-9]{64}$/),
      confirmation: z.string().max(200),
    }).parse(await request.json()) : {};
    return env.WORKSPACES.get(env.WORKSPACES.idFromName(auth.actor.workspace)).fetch(
      "https://workspace.internal" + path.slice(4), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: auth.actor.workspace, actor: auth.actor, input }),
      });
  }

  requireValue(
    path === "/api/lifecycle/delete" && request.method === "POST",
    "NOT_FOUND",
    "Unknown lifecycle route or HTTP method.",
    404,
  );
  const authority = await ownerAuthority(request, env, auth);
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

async function exactRecoveryRoute(request: Request, env: Env) {
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
    "Workspace recovery is available only in the signed-in owner browser.",
    403,
  );
  const owner = await ownerAuthority(request, env, auth);
  const path = url.pathname;

  if (path === "/api/recovery/checkpoints" && request.method === "GET")
    return json({
      checkpoints: await listRecoveryCheckpoints(env.IDENTITY, auth.actor.workspace),
    });

  if (path === "/api/recovery/checkpoints" && request.method === "POST") {
    demandFreshOwner(owner, Date.now());
    const parsed = checkpointCaptureInput.safeParse(await request.json());
    requireValue(
      parsed.success,
      "INVALID_INPUT",
      "Checkpoint capture requires explicit confirmation.",
      400,
    );
    return workspaceInvoke(
      env,
      auth.actor,
      "/recovery/checkpoint",
      parsed.data,
    );
  }

  requireValue(
    path === "/api/recovery/prepare" && request.method === "POST",
    "NOT_FOUND",
    "Unknown exact recovery route or HTTP method.",
    404,
  );
  demandFreshOwner(owner, Date.now());
  const parsed = exactRecoveryPrepareInput.safeParse(await request.json());
  requireValue(
    parsed.success,
    "INVALID_INPUT",
    "Choose one exact recovery checkpoint and a recovery reason.",
    400,
  );
  const checkpoint = await requireRecoveryCheckpoint(
    env.IDENTITY,
    auth.actor.workspace,
    parsed.data.checkpoint,
  );
  await setWorkspaceQuarantine(
    env.IDENTITY,
    auth.actor.workspace,
    true,
    parsed.data.reason,
  );
  try {
    const current = await internalValue(
      await workspaceInvoke(
        env,
        auth.actor,
        "/recovery/current-bookmark",
        {},
      ),
    );
    const plan = await prepareRecoveryPlan(env.IDENTITY, {
      workspace: auth.actor.workspace,
      actor: auth.actor.id,
      targetTime: checkpoint.captured_at,
      targetBookmark: checkpoint.bookmark,
      preRestoreBookmark: current.currentBookmark,
      reason: parsed.data.reason,
      targetMode: "exact_checkpoint",
      checkpointId: checkpoint.id,
    });
    return json({
      plan,
      status: await recoveryStatus(env.IDENTITY, auth.actor.workspace),
    });
  } catch (error) {
    try {
      await assertRecoveryCanResume(env.IDENTITY, auth.actor.workspace);
      const status = await recoveryStatus(env.IDENTITY, auth.actor.workspace);
      if (!status.plan || !["prepared", "armed"].includes(status.plan.state))
        await setWorkspaceQuarantine(
          env.IDENTITY,
          auth.actor.workspace,
          false,
          "Exact recovery preparation failed before restore was armed.",
        );
    } catch {
      // Fail closed: preserve quarantine if safety cannot be proven.
    }
    throw error;
  }
}

function authenticatedProductPath(path: string) {
  return (
    path.startsWith("/api/") ||
    path === "/mcp" ||
    path.startsWith("/payments/") ||
    path === "/auth/logout" ||
    path === "/sources/github/setup" ||
    path === "/sources/github/callback" ||
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
    auth = await authenticate(request.clone() as unknown as Request, env);
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
    if (path === "/internal/root-cutover") {
      const requestId = crypto.randomUUID();
      try {
        await limitEdge(request, env);
        const bounded = await boundedBody(request.clone() as unknown as Request, 2048);
        return secure(await rootCutoverRoute(bounded, env), requestId);
      } catch (error) { return secure(errorResponse(error), requestId); }
    }
    if (!path.startsWith("/api/lifecycle/")) {
      const fenced = await fenceDeletedWorkspaceRequest(request, env);
      if (fenced) return fenced;
      if (isGitHubSourcePath(path)) {
        const requestId = crypto.randomUUID();
        try {
          return secure(await githubSourceRoute(request, env), requestId);
        } catch (error) {
          return secure(errorResponse(error), requestId);
        }
      }
      if (path === "/api/recovery/checkpoints") {
        const requestId = crypto.randomUUID();
        try {
          return secure(await exactRecoveryRoute(request, env), requestId);
        } catch (error) {
          return secure(errorResponse(error), requestId);
        }
      }
      if (path === "/api/recovery/prepare" && request.method === "POST") {
        const probe = request.clone();
        let value: any;
        try {
          value = await probe.json();
        } catch {
          value = undefined;
        }
        if (value && typeof value.checkpoint === "string") {
          const requestId = crypto.randomUUID();
          try {
            return secure(await exactRecoveryRoute(request, env), requestId);
          } catch (error) {
            return secure(errorResponse(error), requestId);
          }
        }
      }
      return base.fetch(request, env, ctx);
    }
    const requestId = crypto.randomUUID();
    try {
      return secure(await lifecycleRoute(request, env), requestId);
    } catch (error) {
      return secure(errorResponse(error), requestId);
    }
  },
} satisfies ExportedHandler<Env>;
