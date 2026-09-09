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
import { errorResponse, Fault, json, requireValue } from "./common.ts";
import { help, openapi } from "./discovery.ts";
import { Engine } from "./engine.ts";
import { mcp } from "./mcp.ts";
import { SocialProviders } from "./providers.ts";
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
    const engine = new Engine(
      this.store,
      this.env,
      new SocialProviders(fetch, this.env.LINKEDIN_VERSION),
      {
        wake: (at) => this.wake(at),
        authorized: (a) => isAuthorized(a, this.env),
        source,
        billing,
      },
    );
    return { billing, engine };
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
      const { engine, billing } = this.services(data.workspace),
        path = new URL(request.url).pathname;
      if (path === "/billing/reconcile") {
        await billing.reconcile();
        await engine.scheduleNext();
        return json({ reconciled: true });
      }
      requireValue(
        data.actor?.workspace === data.workspace,
        "WORKSPACE_MISMATCH",
        "Authenticated workspace is required.",
        403,
      );
      limitWorkspace(this.store, this.env.WORKSPACE_REQUEST_LIMIT);
      let result: unknown;
      if (path === "/connect") {
        const parsed = connectionSchema.safeParse(data.input);
        requireValue(
          parsed.success,
          "INVALID_CONNECTION",
          "Invalid connection inputs.",
        );
        result = await engine.connect(data.actor, parsed.data);
      } else if (path === "/payment") {
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
      } else result = await engine.run(data.name, data.input, data.actor);
      await engine.scheduleNext();
      return json(result);
    } catch (e) {
      return errorResponse(e);
    }
  }
  async alarm() {
    const workspace = this.store.get<string>("workspace");
    if (!workspace) return;
    const { engine, billing } = this.services(workspace);
    // A watchdog survives interruption during provider I/O and reclaims only according to receipt phase.
    await this.ctx.storage.setAlarm(Date.now() + 60000);
    try {
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
      await engine.scheduleNext();
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
  if (
    path.startsWith("/api/") ||
    path === "/mcp" ||
    path.startsWith("/payments/") ||
    path === "/auth/logout"
  ) {
    const auth = await authenticate(request, env);
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
            [...request.headers].filter(([k]) =>
              ["payment-authorization", "accept", "content-type"].includes(k),
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
  if (path === "/app")
    return env.ASSETS.fetch(
      new Request(new URL("/app.html", request.url), request),
    );
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
