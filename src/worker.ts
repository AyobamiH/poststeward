import { DurableObject } from "cloudflare:workers";
import { z } from "zod";
import {
  authenticate,
  callback,
  grants,
  isAuthorized,
  login,
  logout,
} from "./auth.ts";
import { Billing, stripeWebhook } from "./billing.ts";
import {
  errorResponse,
  explicitTime,
  Fault,
  json,
  requireValue,
} from "./common.ts";
import { help, openapi } from "./discovery.ts";
import { EffectLedgerProviders, setWorkspaceQuarantine, workspaceQuarantined } from "./effects.ts";
import { Engine } from "./engine.ts";
import { byName } from "./operations/catalog.ts";
import { SocialProviders } from "./providers.ts";
import {
  completeProviderOAuth,
  oauthConfiguration,
  ProviderOAuthConnections,
  providerOAuthSuccess,
  startProviderOAuth,
} from "./provider-oauth.ts";
import { demandFreshOwner, ownerAuthority } from "./owner-proof.ts";
import { Pilot } from "./pilot.ts";
import { invalidateRestoredAuthority } from "./recovery-local.ts";
import {
  armRecoveryPlan,
  assertRecoveryCanResume,
  prepareRecoveryPlan,
  rearmRecoveryPlanForUndo,
  reconcileRecoveryPlan,
  recoveryStatus,
  requireRecoveryPlan,
} from "./recovery.ts";
import { mcp } from "./mcp.ts";
import { SQLiteStore } from "./store.ts";
import {
  boundedBody,
  expireIdentity,
  limitEdge,
  limitWorkspace,
} from "./security.ts";
import type { Actor, Env, Profile } from "./types.ts";

const connectionSchema = z.strictObject({
  alias: z.string().regex(/^[a-zA-Z0-9_-]{1,100}$/),
  provider: z.enum(["x", "threads", "linkedin"]),
  accessToken: z.string().min(10).max(4096),
  expiresAt: z.number().optional(),
  funding: z.literal("customer_app").optional(),
});
const recoveryPrepareSchema = z.strictObject({
  at: z.string().max(80),
  reason: z.string().min(3).max(240),
});
const recoveryActionBase = {
  id: z.uuid(),
  digest: z.string().regex(/^[a-f0-9]{64}$/),
};
const recoveryExecuteSchema = z.strictObject({
  ...recoveryActionBase,
  execute: z.literal(true),
});
const recoveryReconcileSchema = z.strictObject({
  ...recoveryActionBase,
  reconcile: z.literal(true),
});
const recoveryResumeSchema = z.strictObject({
  ...recoveryActionBase,
  resume: z.literal(true),
});
const recoveryUndoSchema = z.strictObject({
  ...recoveryActionBase,
  undo: z.literal(true),
});
const recoveryCancelSchema = z.strictObject({
  ...recoveryActionBase,
  cancel: z.literal(true),
});
const riskReducingOperations = new Set([
  "account_disconnect",
  "schedule_cancel",
  "publishing_pause",
  "automation_pause",
]);

type PitrStorage = DurableObjectStorage & {
  getCurrentBookmark?: () => Promise<string>;
  getBookmarkForTime?: (timestamp: number | Date) => Promise<string>;
  onNextSessionRestoreBookmark?: (bookmark: string) => Promise<string>;
};

function parse<T>(schema: z.ZodType<T>, value: unknown): T {
  const result = schema.safeParse(value);
  requireValue(result.success, "INVALID_INPUT", "Inputs do not match the documented recovery form.");
  return result.data;
}

async function source(profile: Profile) {
  // The hostname is fixed. Repository strings are validated by the canonical operation schema.
  const url = new URL(
    `https://api.github.com/repos/${profile.repository}/commits`,
  );
  url.search = new URLSearchParams({
    sha: profile.branch,
    path: profile.path,
    per_page: "1",
  }).toString();
  const response = await fetch(url, {
    headers: {
      Accept: "application/vnd.github+json",
      "User-Agent": "poststeward",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    redirect: "manual",
    signal: AbortSignal.timeout(10000),
  });
  requireValue(
    response.ok,
    "SOURCE_UNAVAILABLE",
    `Repository source returned HTTP ${response.status}.`,
    502,
  );
  const data: any = await response.json();
  requireValue(
    Array.isArray(data) && /^[a-f0-9]{40}$/.test(data[0]?.sha),
    "SOURCE_INVALID",
    "No valid source snapshot was returned.",
    502,
  );
  return { sha: data[0].sha as string };
}

export class Workspace extends DurableObject<Env> {
  private store: SQLiteStore;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.store = new SQLiteStore(ctx.storage);
  }
  private async wake(at: number) {
    const current = await this.ctx.storage.getAlarm();
    if (current === null || at < current) await this.ctx.storage.setAlarm(at);
  }
  private pitr(requireRestore = false) {
    const storage = this.ctx.storage as PitrStorage;
    requireValue(
      typeof storage.getCurrentBookmark === "function" &&
        typeof storage.getBookmarkForTime === "function" &&
        (!requireRestore || typeof storage.onNextSessionRestoreBookmark === "function"),
      "RECOVERY_PITR_UNAVAILABLE",
      "Point-in-time recovery is unavailable in this Durable Object runtime.",
      501,
    );
    return storage;
  }
  private services(workspace: string) {
    const known = this.store.get<string>("workspace");
    requireValue(
      !known || known === workspace,
      "WORKSPACE_MISMATCH",
      "Object identity mismatch.",
      403,
    );
    if (!known) this.store.put("workspace", workspace);
    const billing = new Billing(this.store, this.env, workspace);
    const baseProviders = new SocialProviders(fetch, this.env.LINKEDIN_VERSION);
    const providers = new EffectLedgerProviders(
      baseProviders,
      this.env.IDENTITY,
      workspace,
    );
    const authorized = (actor: Actor) => isAuthorized(actor, this.env);
    const engine = new Engine(
      this.store,
      this.env,
      providers,
      {
        wake: (at) => this.wake(at),
        authorized,
        source,
        billing,
      },
    );
    const pilot = new Pilot(this.store, this.env, engine, providers, authorized);
    const oauth = new ProviderOAuthConnections(
      this.store,
      this.env,
      baseProviders,
    );
    return { billing, engine, pilot, oauth };
  }
  private async schedule(engine: Engine, oauth: ProviderOAuthConnections) {
    await engine.scheduleNext();
    const nextOAuth = oauth.nextWake();
    if (nextOAuth !== undefined)
      await this.wake(Math.max(Date.now() + 1000, nextOAuth));
  }
  private ownerOnly(actor: Actor) {
    requireValue(
      !actor.grant && actor.scopes.includes("admin"),
      "OWNER_SESSION_REQUIRED",
      "Workspace recovery is restricted to the signed-in owner.",
      403,
    );
  }
  async fetch(request: Request): Promise<Response> {
    try {
      const data = (await request.json()) as {
        workspace: string;
        actor: Actor;
        name: string;
        input: unknown;
        payment?: { url: string; headers: Record<string, string> };
      };
      requireValue(
        typeof data.workspace === "string",
        "WORKSPACE_REQUIRED",
        "Workspace is required.",
        400,
      );
      const path = new URL(request.url).pathname;
      // Stripe reaches this only through the Worker's verified webhook route.
      // It intentionally has no user Actor envelope, so preserve the original
      // actorless internal reconciliation contract. A quarantined event stays
      // incomplete in D1 and receives non-2xx so Stripe can retry it later.
      if (path === "/billing/reconcile") {
        const control = await workspaceQuarantined(
          this.env.IDENTITY,
          data.workspace,
        );
        const { engine, billing, oauth } = this.services(data.workspace);
        if (control.quarantined)
          return json(
            {
              error: {
                code: "RECOVERY_QUARANTINED",
                message: "Billing reconciliation waits until recovery quarantine clears.",
              },
            },
            503,
          );
        await billing.reconcile();
        await this.schedule(engine, oauth);
        return json({ reconciled: true });
      }
      requireValue(
        data.actor?.workspace === data.workspace,
        "WORKSPACE_MISMATCH",
        "Authenticated workspace is required.",
        403,
      );
      const known = this.store.get<string>("workspace");
      limitWorkspace(this.store, this.env.WORKSPACE_REQUEST_LIMIT);

      if (path === "/recovery/bookmarks") {
        this.ownerOnly(data.actor);
        requireValue(
          known === data.workspace,
          "RECOVERY_WORKSPACE_UNINITIALIZED",
          "Recovery requires an existing initialized workspace.",
          409,
        );
        const control = await workspaceQuarantined(this.env.IDENTITY, data.workspace);
        requireValue(
          control.quarantined,
          "RECOVERY_NOT_QUARANTINED",
          "Quarantine the workspace before reading recovery bookmarks.",
          409,
        );
        const targetTime = (data.input as { targetTime?: unknown })?.targetTime;
        requireValue(
          typeof targetTime === "number" && Number.isFinite(targetTime),
          "RECOVERY_TARGET_INVALID",
          "Recovery target is invalid.",
        );
        const storage = this.pitr();
        const [preRestoreBookmark, targetBookmark] = await Promise.all([
          storage.getCurrentBookmark!(),
          storage.getBookmarkForTime!(targetTime),
        ]);
        return json({ preRestoreBookmark, targetBookmark });
      }
      if (path === "/recovery/restore") {
        this.ownerOnly(data.actor);
        requireValue(
          known === data.workspace,
          "RECOVERY_WORKSPACE_UNINITIALIZED",
          "Recovery requires an existing initialized workspace.",
          409,
        );
        const input = data.input as { id?: string; digest?: string };
        const plan = await requireRecoveryPlan(this.env.IDENTITY, {
          id: input.id || "",
          digest: input.digest || "",
          workspace: data.workspace,
          actor: data.actor.id,
          states: ["prepared"],
        });
        const control = await workspaceQuarantined(this.env.IDENTITY, data.workspace);
        requireValue(
          control.quarantined,
          "RECOVERY_NOT_QUARANTINED",
          "Recovery quarantine changed before restore.",
          409,
        );
        const storage = this.pitr(true);
        const undoBookmark = await storage.onNextSessionRestoreBookmark!(
          plan.target_bookmark,
        );
        await armRecoveryPlan(this.env.IDENTITY, plan, undoBookmark);
        this.ctx.abort("Workspace point-in-time recovery armed", {
          retryAlarm: false,
        });
      }
      if (path === "/recovery/undo") {
        this.ownerOnly(data.actor);
        requireValue(
          known === data.workspace,
          "RECOVERY_WORKSPACE_UNINITIALIZED",
          "Recovery undo requires an initialized workspace.",
          409,
        );
        const input = data.input as { id?: string; digest?: string };
        const plan = await requireRecoveryPlan(this.env.IDENTITY, {
          id: input.id || "",
          digest: input.digest || "",
          workspace: data.workspace,
          actor: data.actor.id,
          states: ["reconciled"],
        });
        requireValue(
          plan.undo_bookmark,
          "RECOVERY_UNDO_UNAVAILABLE",
          "This recovery plan has no undo bookmark.",
          409,
        );
        const control = await workspaceQuarantined(this.env.IDENTITY, data.workspace);
        requireValue(
          control.quarantined,
          "RECOVERY_NOT_QUARANTINED",
          "Recovery undo requires quarantine.",
          409,
        );
        const storage = this.pitr(true);
        const redoBookmark = await storage.onNextSessionRestoreBookmark!(
          plan.undo_bookmark,
        );
        await rearmRecoveryPlanForUndo(this.env.IDENTITY, plan, redoBookmark);
        this.ctx.abort("Workspace point-in-time recovery undo armed", {
          retryAlarm: false,
        });
      }
      if (path === "/recovery/probe") {
        this.ownerOnly(data.actor);
        requireValue(
          known === data.workspace,
          "RECOVERY_RESTORE_NOT_READY",
          "Recovered workspace marker is not present.",
          409,
        );
        const storage = this.pitr();
        const invalidated = await invalidateRestoredAuthority(
          this.store,
          this.env,
          data.workspace,
        );
        return json({
          workspacePresent: true,
          currentBookmark: await storage.getCurrentBookmark!(),
          authorityInvalidated: {
            accounts: invalidated.accounts.length,
            profiles: invalidated.profiles.length,
            deliveries: invalidated.deliveries.length,
            billingReset: invalidated.billingReset,
          },
        });
      }
      if (path === "/recovery/resume") {
        this.ownerOnly(data.actor);
        requireValue(
          known === data.workspace,
          "RECOVERY_RESTORE_NOT_READY",
          "Recovered workspace is not ready to resume.",
          409,
        );
        const { engine, oauth } = this.services(data.workspace);
        await this.schedule(engine, oauth);
        return json({ resumed: true });
      }

      const control = await workspaceQuarantined(this.env.IDENTITY, data.workspace);
      const { engine, billing, pilot, oauth } = this.services(data.workspace);
      let result: unknown;
      if (path.startsWith("/pilot/")) {
        const action = path.slice("/pilot/".length);
        requireValue(
          !control.quarantined || ["status", "cancel", "recheck"].includes(action),
          "RECOVERY_QUARANTINED",
          "Recovery quarantine blocks new owner publication authority.",
          409,
        );
        // This envelope is constructed by the authenticated Worker, never by
        // a public JSON body. The Durable Object has no public internet route.
        const envelope = data.input as {
          input: unknown;
          owner: Awaited<ReturnType<typeof ownerAuthority>>;
        };
        result = await pilot.run(action, envelope.input, data.actor, envelope.owner);
      } else if (path === "/connect") {
        requireValue(
          !control.quarantined,
          "RECOVERY_QUARANTINED",
          "Recovery quarantine blocks connection mutation.",
          409,
        );
        const parsed = connectionSchema.safeParse(data.input);
        requireValue(
          parsed.success,
          "INVALID_CONNECTION",
          "Invalid connection inputs.",
        );
        result = await engine.connect(data.actor, parsed.data);
      } else if (path === "/oauth/connect") {
        requireValue(
          !control.quarantined,
          "RECOVERY_QUARANTINED",
          "Recovery quarantine blocks connection mutation.",
          409,
        );
        const input = data.input as Parameters<ProviderOAuthConnections["connect"]>[1];
        result = await oauth.connect(data.actor, input);
      } else if (path === "/oauth/status") {
        requireValue(
          !data.actor.grant && data.actor.scopes.includes("admin"),
          "OWNER_CONNECTION_REQUIRED",
          "Provider connection status is restricted to the signed-in owner.",
          403,
        );
        result = oauth.status();
      } else if (path === "/payment") {
        requireValue(
          !control.quarantined,
          "RECOVERY_QUARANTINED",
          "Recovery quarantine blocks financial mutations until state is reconciled.",
          409,
        );
        requireValue(
          data.payment,
          "INVALID_PAYMENT",
          "Payment request is required.",
        );
        return billing.machinePayment(
          new Request(data.payment.url, {
            method: "POST",
            headers: data.payment.headers,
            body: "{}",
          }),
          data.actor,
          data.name,
        );
      } else {
        if (control.quarantined) {
          const operation = byName.get(data.name);
          const readOnly = operation?.effects.every((effect) => effect === "READ_ONLY");
          requireValue(
            readOnly || riskReducingOperations.has(data.name),
            "RECOVERY_QUARANTINED",
            "Recovery quarantine permits only inspection and risk-reducing operations.",
            409,
          );
        }
        result = await engine.run(data.name, data.input, data.actor);
        if (data.name === "account_disconnect") {
          const alias = (data.input as { alias?: unknown })?.alias;
          if (typeof alias === "string") await oauth.afterDisconnect(alias);
        }
        if (data.name === "schedule_cancel") {
          const cancellation = result as {
            delivery?: import("./types.ts").Delivery;
          };
          if (cancellation.delivery)
            result = {
              ...cancellation,
              delivery: engine.publicDelivery(cancellation.delivery),
            };
        }
      }
      await this.schedule(engine, oauth);
      return json(result);
    } catch (e) {
      return errorResponse(e);
    }
  }
  async alarm() {
    const workspace = this.store.get<string>("workspace");
    if (!workspace) return;
    const control = await workspaceQuarantined(this.env.IDENTITY, workspace);
    if (control.quarantined) {
      await this.ctx.storage.deleteAlarm();
      console.warn(
        JSON.stringify({
          event: "workspace_alarm_suppressed_for_recovery",
          workspace,
        }),
      );
      return;
    }
    const { engine, billing, oauth } = this.services(workspace);
    // A watchdog survives interruption during provider I/O and reclaims only according to receipt phase.
    await this.ctx.storage.setAlarm(Date.now() + 60000);
    try {
      // Provider token rotation happens in the durable background path, not on
      // unrelated customer reads. Manual publications are reserved first and
      // therefore reach this alarm before their provider write.
      await oauth.refreshDue();
      const next = this.store.get<number>("billing:next") || 0;
      if (next <= Date.now()) {
        try {
          await billing.reconcile();
          this.store.put("billing:next", Date.now() + 3600000);
        } catch {
          // Stripe availability must not block a customer's Free/manual jobs.
          this.store.put("billing:next", Date.now() + 60000);
        }
      }
      await engine.tick();
    } finally {
      await this.ctx.storage.deleteAlarm();
      await this.schedule(engine, oauth);
      if (this.store.get("billing:attempt"))
        await this.wake(
          this.store.get<number>("billing:next") || Date.now() + 60000,
        );
    }
  }
}

async function invoke(
  env: Env,
  actor: Actor,
  name: string,
  input: unknown,
  path = "/operation",
  payment?: any,
) {
  return env.WORKSPACES.get(env.WORKSPACES.idFromName(actor.workspace)).fetch(
    "https://workspace.internal" + path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace: actor.workspace,
        actor,
        name,
        input,
        payment,
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

async function route(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
): Promise<Response> {
  const url = new URL(request.url),
    path = url.pathname;
  if (request.headers.has("origin"))
    requireValue(
      request.headers.get("origin") === env.PUBLIC_ORIGIN,
      "ORIGIN_REJECTED",
      "Cross-origin requests are not allowed.",
      403,
    );
  requireValue(
    url.origin === env.PUBLIC_ORIGIN && !env.PUBLIC_ORIGIN.includes(".invalid"),
    "HOST_REJECTED",
    "This hostname is not configured.",
    403,
  );
  await limitEdge(request, env);
  if (
    request.body &&
    (path.startsWith("/api/") ||
      path === "/mcp" ||
      path.startsWith("/payments/"))
  )
    requireValue(
      /^application\/json(?:\s*;|$)/i.test(
        request.headers.get("content-type") || "",
      ),
      "JSON_REQUIRED",
      "Use application/json.",
      415,
    );
  request = await boundedBody(
    request,
    path === "/webhooks/stripe" ? 262144 : 32768,
  );
  if (path === "/health" && request.method === "GET")
    return json({
      status: "ok",
      release: env.RELEASE_SHA,
      advancedEnabled: env.ADVANCED_ENABLED === "true",
      providerOAuth: Object.fromEntries(
        Object.entries(oauthConfiguration(env)).map(([provider, value]) => [
          provider,
          (value as { available: boolean }).available,
        ]),
      ),
    });
  if (path === "/help.json" && request.method === "GET")
    return json(help(env, url.searchParams.get("scope") || undefined), 200, {
      "Cache-Control": "public, max-age=60",
    });
  if (path === "/openapi.json" && request.method === "GET")
    return json(openapi(), 200, { "Cache-Control": "public, max-age=300" });
  if (path === "/plans.json" && request.method === "GET")
    return json(help(env).plans);
  if (path === "/sitemap.xml" && request.method === "GET")
    return new Response(
      `<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>${env.PUBLIC_ORIGIN}/</loc></url><url><loc>${env.PUBLIC_ORIGIN}/docs/agent-guide.md</loc></url></urlset>`,
      { headers: { "Content-Type": "application/xml" } },
    );
  if (path === "/auth/login" && request.method === "GET")
    return login(request, env);
  if (path === "/auth/callback" && request.method === "GET")
    return callback(request, env);
  if (path === "/webhooks/stripe" && request.method === "POST")
    return stripeWebhook(request, env);
  const providerCallback = /^\/connections\/oauth\/(x|threads|linkedin)\/callback$/.exec(path);
  if (providerCallback && request.method === "GET") {
    const provider = providerCallback[1] as "x" | "threads" | "linkedin";
    const auth = await authenticate(request, env);
    // The state was created by an owner-proof-bearing session. Recheck the
    // proof/allowlist before accepting provider credentials.
    await ownerAuthority(request, env, auth);
    const completed = await completeProviderOAuth(request, env, auth, provider);
    if (completed.response) return completed.response;
    const response = await invoke(
      env,
      auth.actor,
      "",
      { alias: completed.alias, token: completed.token },
      "/oauth/connect",
    );
    if (!response.ok) {
      const value: any = await response.json();
      return json(
        {
          error: {
            code: value.error?.code || "OAUTH_CONNECTION_FAILED",
            message:
              value.error?.message ||
              "Provider connection could not be completed.",
          },
        },
        response.status,
        {
          "Set-Cookie":
            "__Host-provider-oauth=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0",
        },
      );
    }
    return providerOAuthSuccess(provider);
  }
  if (
    path.startsWith("/api/") ||
    path === "/mcp" ||
    path.startsWith("/payments/") ||
    path === "/auth/logout"
  ) {
    const auth = await authenticate(request, env);

    if (path.startsWith("/api/recovery/")) {
      requireValue(
        auth.browser && !auth.actor.grant && auth.actor.scopes.includes("admin"),
        "OWNER_SESSION_REQUIRED",
        "Workspace recovery is available only in the signed-in owner browser.",
        403,
      );
      const owner = await ownerAuthority(request, env, auth);
      if (path === "/api/recovery/status" && request.method === "GET")
        return json(await recoveryStatus(env.IDENTITY, auth.actor.workspace));
      if (path === "/api/recovery/prepare" && request.method === "POST") {
        demandFreshOwner(owner, Date.now());
        const input = parse(recoveryPrepareSchema, await request.json());
        const targetTime = explicitTime(input.at);
        requireValue(
          targetTime <= Date.now(),
          "RECOVERY_TARGET_INVALID",
          "Recovery target must be in the past.",
          400,
        );
        await setWorkspaceQuarantine(
          env.IDENTITY,
          auth.actor.workspace,
          true,
          input.reason,
        );
        try {
          const bookmarks = await internalValue(
            await invoke(
              env,
              auth.actor,
              "",
              { targetTime },
              "/recovery/bookmarks",
            ),
          );
          const plan = await prepareRecoveryPlan(env.IDENTITY, {
            workspace: auth.actor.workspace,
            actor: auth.actor.id,
            targetTime,
            targetBookmark: bookmarks.targetBookmark,
            preRestoreBookmark: bookmarks.preRestoreBookmark,
            reason: input.reason,
          });
          return json({
            plan,
            status: await recoveryStatus(env.IDENTITY, auth.actor.workspace),
          });
        } catch (error) {
          // Preparation has not called the destructive restore primitive yet.
          // Release quarantine only if no concurrent/previous recovery is now
          // prepared or armed and no provider-write fence is unresolved.
          try {
            await assertRecoveryCanResume(env.IDENTITY, auth.actor.workspace);
            const status = await recoveryStatus(env.IDENTITY, auth.actor.workspace);
            if (!status.plan || !["prepared", "armed"].includes(status.plan.state))
              await setWorkspaceQuarantine(
                env.IDENTITY,
                auth.actor.workspace,
                false,
                "Recovery preparation failed before restore was armed.",
              );
          } catch {
            // Fail closed: keep quarantine if safety cannot be proven.
          }
          throw error;
        }
      }
      if (path === "/api/recovery/execute" && request.method === "POST") {
        demandFreshOwner(owner, Date.now());
        const input = parse(recoveryExecuteSchema, await request.json());
        await requireRecoveryPlan(env.IDENTITY, {
          id: input.id,
          digest: input.digest,
          workspace: auth.actor.workspace,
          actor: auth.actor.id,
          states: ["prepared"],
        });
        // A provider write may have crossed its D1 fence just before quarantine.
        // Never arm PITR while that bounded write window can still be live.
        await assertRecoveryCanResume(env.IDENTITY, auth.actor.workspace);
        let returned = false;
        try {
          const response = await invoke(
            env,
            auth.actor,
            "",
            { id: input.id, digest: input.digest },
            "/recovery/restore",
          );
          returned = true;
          await internalValue(response);
        } catch {
          // ctx.abort() deliberately terminates the Durable Object request.
          // The D1 plan is the authority for whether restore was safely armed.
        }
        const status = await recoveryStatus(env.IDENTITY, auth.actor.workspace);
        requireValue(
          status.plan?.id === input.id && status.plan.state === "armed",
          "RECOVERY_EXECUTE_FAILED",
          returned
            ? "Recovery restore returned without entering the armed state."
            : "Recovery restore did not persist an armed plan before restart.",
          502,
        );
        return json({ accepted: true, restartInProgress: true, status }, 202);
      }
      if (path === "/api/recovery/reconcile" && request.method === "POST") {
        demandFreshOwner(owner, Date.now());
        const input = parse(recoveryReconcileSchema, await request.json());
        const plan = await requireRecoveryPlan(env.IDENTITY, {
          id: input.id,
          digest: input.digest,
          workspace: auth.actor.workspace,
          actor: auth.actor.id,
          states: ["armed"],
        });
        const probe = await internalValue(
          await invoke(env, auth.actor, "", {}, "/recovery/probe"),
        );
        requireValue(
          probe.workspacePresent === true && typeof probe.currentBookmark === "string",
          "RECOVERY_RESTORE_NOT_READY",
          "Recovered workspace is not yet ready for reconciliation.",
          409,
        );
        const status = await reconcileRecoveryPlan(env.IDENTITY, plan);
        return json({ reconciled: true, currentBookmarkCaptured: true, status });
      }
      if (path === "/api/recovery/resume" && request.method === "POST") {
        demandFreshOwner(owner, Date.now());
        const input = parse(recoveryResumeSchema, await request.json());
        await requireRecoveryPlan(env.IDENTITY, {
          id: input.id,
          digest: input.digest,
          workspace: auth.actor.workspace,
          actor: auth.actor.id,
          states: ["reconciled"],
        });
        await assertRecoveryCanResume(env.IDENTITY, auth.actor.workspace);
        await setWorkspaceQuarantine(
          env.IDENTITY,
          auth.actor.workspace,
          false,
          "Recovery reconciled and owner resumed publication.",
        );
        try {
          await internalValue(
            await invoke(env, auth.actor, "", {}, "/recovery/resume"),
          );
        } catch (error) {
          await setWorkspaceQuarantine(
            env.IDENTITY,
            auth.actor.workspace,
            true,
            "Recovery resume failed; publication remains quarantined.",
          );
          throw error;
        }
        return json({ resumed: true, status: await recoveryStatus(env.IDENTITY, auth.actor.workspace) });
      }
      if (path === "/api/recovery/undo" && request.method === "POST") {
        demandFreshOwner(owner, Date.now());
        const input = parse(recoveryUndoSchema, await request.json());
        const plan = await requireRecoveryPlan(env.IDENTITY, {
          id: input.id,
          digest: input.digest,
          workspace: auth.actor.workspace,
          actor: auth.actor.id,
          states: ["reconciled"],
        });
        requireValue(
          plan.undo_bookmark,
          "RECOVERY_UNDO_UNAVAILABLE",
          "This recovery plan has no undo bookmark.",
          409,
        );
        await setWorkspaceQuarantine(
          env.IDENTITY,
          auth.actor.workspace,
          true,
          "Owner requested exact recovery undo.",
        );
        try {
          const response = await invoke(
            env,
            auth.actor,
            "",
            { id: input.id, digest: input.digest },
            "/recovery/undo",
          );
          await internalValue(response);
        } catch {
          // Expected when the Durable Object aborts after persisting D1 state.
        }
        const status = await recoveryStatus(env.IDENTITY, auth.actor.workspace);
        requireValue(
          status.plan?.id === input.id && status.plan.state === "armed",
          "RECOVERY_UNDO_FAILED",
          "Recovery undo did not persist its armed state before restart.",
          502,
        );
        return json({ accepted: true, restartInProgress: true, status }, 202);
      }
      if (path === "/api/recovery/cancel" && request.method === "POST") {
        demandFreshOwner(owner, Date.now());
        const input = parse(recoveryCancelSchema, await request.json());
        await requireRecoveryPlan(env.IDENTITY, {
          id: input.id,
          digest: input.digest,
          workspace: auth.actor.workspace,
          actor: auth.actor.id,
          states: ["prepared"],
        });
        await assertRecoveryCanResume(env.IDENTITY, auth.actor.workspace);
        const cancelled = await env.IDENTITY
          .prepare(
            "UPDATE workspace_recovery_plans SET state='cancelled',updated_at=? WHERE id=? AND workspace=? AND digest=? AND state='prepared'",
          )
          .bind(Date.now(), input.id, auth.actor.workspace, input.digest)
          .run();
        requireValue(
          cancelled.meta.changes === 1,
          "RECOVERY_PLAN_CHANGED",
          "Recovery plan changed before cancellation completed.",
          409,
        );
        await setWorkspaceQuarantine(
          env.IDENTITY,
          auth.actor.workspace,
          false,
          "Prepared recovery cancelled by owner before restore.",
        );
        await internalValue(
          await invoke(env, auth.actor, "", {}, "/recovery/resume"),
        );
        return json({ cancelled: true, status: await recoveryStatus(env.IDENTITY, auth.actor.workspace) });
      }
      return json(
        { error: { code: "NOT_FOUND", message: "Unknown recovery route or HTTP method." } },
        404,
      );
    }

    const oauthStart = /^\/api\/connections\/oauth\/(x|threads|linkedin)\/start$/.exec(path);
    if (oauthStart) {
      requireValue(
        request.method === "POST",
        "METHOD_NOT_ALLOWED",
        "Start provider OAuth with POST.",
        405,
      );
      await ownerAuthority(request, env, auth);
      return startProviderOAuth(
        request,
        env,
        auth,
        oauthStart[1] as "x" | "threads" | "linkedin",
      );
    }
    if (path === "/api/connections/oauth/status" && request.method === "GET") {
      requireValue(
        auth.browser && !auth.actor.grant && auth.actor.scopes.includes("admin"),
        "OWNER_CONNECTION_REQUIRED",
        "Provider connection status is restricted to the signed-in owner.",
        403,
      );
      const response = await invoke(env, auth.actor, "", {}, "/oauth/status");
      const connections = await response.json();
      return json(
        {
          providers: oauthConfiguration(env),
          connections: response.ok ? connections : [],
        },
        response.ok ? 200 : response.status,
      );
    }
    if (path.startsWith("/api/pilot/")) {
      const action = path.slice("/api/pilot/".length);
      requireValue(
        ["status", "prepare", "confirm", "cancel", "recheck"].includes(action),
        "NOT_FOUND",
        "Unknown acceptance operation.",
        404,
      );
      requireValue(
        request.method === (action === "status" ? "GET" : "POST"),
        "METHOD_NOT_ALLOWED",
        "Use the documented acceptance method.",
        405,
      );
      const owner = await ownerAuthority(request, env, auth);
      const input = action === "status" ? {} : await request.json();
      const response = await invoke(
        env,
        auth.actor,
        "",
        { input, owner },
        "/pilot/" + action,
      );
      const result = (await response.json()) as Record<string, unknown>;
      return json(
        { ...result, ...(response.ok ? { owner: owner.proof } : {}) },
        response.status,
      );
    }
    if (path === "/api/session" && request.method === "GET")
      return json({
        workspace: auth.actor.workspace,
        actor: auth.actor.id,
        scopes: auth.actor.scopes,
        csrf: auth.csrf,
      });
    if (
      path === "/api/grants" &&
      ["GET", "POST", "DELETE"].includes(request.method)
    )
      return grants(request, env, auth);
    if (path === "/auth/logout" && request.method === "POST")
      return logout(request, env);
    if (path === "/api/connections/import" && request.method === "POST")
      return invoke(env, auth.actor, "", await request.json(), "/connect");
    if (path === "/mcp")
      return mcp(request, env, auth.actor, async (name, input) => {
        const response = await invoke(env, auth.actor, name, input);
        const value: any = await response.json();
        if (!response.ok)
          throw new Fault(
            value.error?.code || "OPERATION_FAILED",
            value.error?.message || "Operation failed.",
            response.status,
          );
        return value;
      });
    if (path.startsWith("/payments/") && request.method === "POST")
      return invoke(
        env,
        auth.actor,
        path.slice("/payments/".length),
        {},
        "/payment",
        {
          url: request.url,
          headers: Object.fromEntries(
            [...request.headers].filter(([key]) =>
              ["payment-authorization", "accept", "content-type"].includes(key),
            ),
          ),
        },
      );
    if (path.startsWith("/api/operations/") && request.method === "POST")
      return invoke(
        env,
        auth.actor,
        path.slice("/api/operations/".length),
        await request.json(),
      );
    return json(
      {
        error: { code: "NOT_FOUND", message: "Unknown route or HTTP method." },
      },
      404,
    );
  }
  if (!["GET", "HEAD"].includes(request.method))
    return json(
      {
        error: {
          code: "METHOD_NOT_ALLOWED",
          message: "Use a documented endpoint.",
        },
      },
      405,
    );
  // Assets already resolves /app and /pilot to their HTML files. Rewriting to
  // .html here would create a canonical redirect loop.
  return env.ASSETS.fetch(request);
}

export default {
  async scheduled(_controller: ScheduledController, env: Env) {
    await expireIdentity(env);
  },
  async fetch(request: Request, env: Env, ctx: ExecutionContext) {
    const requestId = crypto.randomUUID();
    let response: Response;
    try {
      response = await route(request, env, ctx);
    } catch (e) {
      response = errorResponse(e);
    }
    if (response.status >= 500)
      console.error(
        JSON.stringify({
          event: "request_failed",
          requestId,
          status: response.status,
          release: env.RELEASE_SHA,
        }),
      );
    const headers = new Headers(response.headers);
    headers.set("X-Request-ID", requestId);
    headers.set("X-Content-Type-Options", "nosniff");
    headers.set("Strict-Transport-Security", "max-age=31536000");
    headers.set("X-Frame-Options", "DENY");
    if (response.status === 429) headers.set("Retry-After", "60");
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
  },
} satisfies ExportedHandler<Env>;
