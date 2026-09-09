import { digest, Fault, json, requireValue, uid } from "./common.ts";
import { seal, unseal } from "./crypto.ts";
import {
  readProviderBody,
  type Credential,
  type ProviderAPI,
} from "./providers.ts";
import type { Account, Actor, Env, Provider, Store } from "./types.ts";

const providerNames = ["x", "threads", "linkedin"] as const;
type OAuthProvider = (typeof providerNames)[number];
const aliasPattern = /^[A-Za-z0-9_-]{1,100}$/;
const tokenLimit = 8192;
const stateCookieName = "__Host-provider-oauth";

export interface OAuthTokenSet {
  provider: OAuthProvider;
  accessToken: string;
  expiresAt: number;
  refreshToken?: string;
  refreshExpiresAt?: number;
  scopes: string[];
  obtainedAt: number;
}
interface OAuthMeta {
  alias: string;
  provider: OAuthProvider;
  scopes: string[];
  strategy: "refresh_token" | "threads_long_lived" | "reauthorize";
  accessExpiresAt: number;
  refreshExpiresAt?: number;
  nextRefreshAt?: number;
  status:
    | "healthy"
    | "refresh_failed"
    | "reauthorization_required"
    | "identity_drift"
    | "expired";
  secret?: string;
  lastRefreshAt?: number;
  lastError?: string;
}
interface OAuthStateRow {
  session_hash: string;
  workspace: string;
  actor: string;
  provider: OAuthProvider;
  alias: string;
  verifier?: string;
  expires_at: number;
}
interface BrowserAuth {
  actor: Actor;
  browser: boolean;
}
interface ProviderConfig {
  provider: OAuthProvider;
  clientId: string;
  clientSecret: string;
  callback: string;
  scopes: string[];
  authorizationEndpoint: string;
}

type HttpClient = typeof fetch;

function cookie(request: Request, name: string) {
  return request.headers
    .get("cookie")
    ?.split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(name + "="))
    ?.slice(name.length + 1);
}
function stateCookie(value: string, maxAge: number) {
  return `${stateCookieName}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}
function base64url(bytes: Uint8Array) {
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/g, "");
}
function base64Utf8(value: string) {
  return btoa(String.fromCharCode(...new TextEncoder().encode(value)));
}
function randomSecret(bytes = 32) {
  return base64url(crypto.getRandomValues(new Uint8Array(bytes)));
}
async function challenge(verifier: string) {
  return base64url(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)),
    ),
  );
}
function parseScopes(value: unknown): string[] {
  const parts = Array.isArray(value)
    ? value
    : typeof value === "string"
      ? value.split(/[\s,]+/)
      : [];
  return [
    ...new Set(parts.map(String).map((value) => value.trim()).filter(Boolean)),
  ].sort();
}
function demandToken(value: unknown, name = "access token") {
  requireValue(
    typeof value === "string" && value.length >= 10 && value.length <= tokenLimit,
    "OAUTH_TOKEN_INVALID",
    `Provider returned no usable ${name}.`,
    502,
  );
  return value;
}
function demandExpiry(value: unknown, now: number) {
  const seconds = Number(value);
  requireValue(
    Number.isFinite(seconds) && seconds >= 60 && seconds <= 370 * 86400,
    "OAUTH_TOKEN_INVALID",
    "Provider returned an invalid token lifetime.",
    502,
  );
  return now + seconds * 1000;
}
function config(env: Env, provider: OAuthProvider): ProviderConfig {
  const values = {
    x: {
      clientId: env.X_OAUTH_CLIENT_ID || "",
      clientSecret: env.X_OAUTH_CLIENT_SECRET || "",
      authorizationEndpoint: "https://x.com/i/oauth2/authorize",
      scopes: ["tweet.read", "tweet.write", "users.read", "offline.access"],
    },
    threads: {
      clientId: env.THREADS_OAUTH_CLIENT_ID || "",
      clientSecret: env.THREADS_OAUTH_CLIENT_SECRET || "",
      authorizationEndpoint: "https://threads.net/oauth/authorize",
      scopes: ["threads_basic", "threads_content_publish"],
    },
    linkedin: {
      clientId: env.LINKEDIN_OAUTH_CLIENT_ID || "",
      clientSecret: env.LINKEDIN_OAUTH_CLIENT_SECRET || "",
      authorizationEndpoint: "https://www.linkedin.com/oauth/v2/authorization",
      scopes: [
        "openid",
        "profile",
        "w_member_social",
        ...(env.LINKEDIN_MEMBER_READBACK === "true" ? ["r_member_social"] : []),
      ],
    },
  }[provider];
  return {
    provider,
    ...values,
    callback: `${env.PUBLIC_ORIGIN}/connections/oauth/${provider}/callback`,
  };
}
function configured(c: ProviderConfig) {
  return c.clientId.length > 0 && c.clientSecret.length > 0;
}
export function oauthConfiguration(env: Env) {
  return Object.fromEntries(
    providerNames.map((provider) => {
      const c = config(env, provider);
      return [
        provider,
        {
          available: configured(c),
          callback: c.callback,
          scopes: c.scopes,
          readback:
            provider !== "linkedin" || c.scopes.includes("r_member_social"),
          reason: configured(c) ? undefined : "provider_app_not_configured",
        },
      ];
    }),
  );
}
async function providerJson(
  url: string,
  init: RequestInit = {},
  http: HttpClient = fetch,
) {
  const signal = AbortSignal.timeout(15000);
  let response: Response;
  try {
    response = await http(url, { ...init, redirect: "manual", signal });
  } catch {
    throw new Fault(
      "OAUTH_PROVIDER_UNAVAILABLE",
      "Provider authorization service could not be reached.",
      502,
    );
  }
  if (!response.ok) {
    void response.body?.cancel().catch(() => {});
    throw new Fault(
      `OAUTH_PROVIDER_HTTP_${response.status}`,
      "Provider authorization was not accepted.",
      502,
    );
  }
  try {
    const raw = await readProviderBody(response, signal);
    const data = raw ? JSON.parse(raw) : {};
    requireValue(
      data && typeof data === "object" && !Array.isArray(data),
      "OAUTH_PROVIDER_INVALID_RESPONSE",
      "Provider authorization returned invalid evidence.",
      502,
    );
    return data as Record<string, any>;
  } catch (error) {
    if (error instanceof Fault) throw error;
    throw new Fault(
      "OAUTH_PROVIDER_INVALID_RESPONSE",
      "Provider authorization returned incomplete evidence.",
      502,
    );
  }
}
function requireScopes(actual: string[], required: string[]) {
  requireValue(
    required.every((scope) => actual.includes(scope)),
    "OAUTH_SCOPE_MISSING",
    "Provider did not grant every required permission.",
    403,
  );
}
async function exchangeCode(
  env: Env,
  provider: OAuthProvider,
  code: string,
  verifier?: string,
  now = Date.now(),
  http: HttpClient = fetch,
): Promise<OAuthTokenSet> {
  const c = config(env, provider);
  requireValue(
    configured(c),
    "OAUTH_NOT_CONFIGURED",
    "This provider OAuth connection is not configured.",
    503,
  );
  if (provider === "x") {
    requireValue(verifier, "OAUTH_STATE_INVALID", "PKCE verifier is missing.", 400);
    const body = new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: c.callback,
      code_verifier: verifier,
      client_id: c.clientId,
    });
    const data = await providerJson(
      "https://api.x.com/2/oauth2/token",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          Authorization: `Basic ${base64Utf8(`${c.clientId}:${c.clientSecret}`)}`,
        },
        body,
      },
      http,
    );
    const actualScopes = parseScopes(data.scope);
    requireScopes(actualScopes, c.scopes);
    return {
      provider,
      accessToken: demandToken(data.access_token),
      refreshToken: demandToken(data.refresh_token, "refresh token"),
      expiresAt: demandExpiry(data.expires_in, now),
      scopes: actualScopes,
      obtainedAt: now,
    };
  }
  if (provider === "threads") {
    const exchange = new URL("https://graph.threads.net/oauth/access_token");
    exchange.search = new URLSearchParams({
      client_id: c.clientId,
      client_secret: c.clientSecret,
      code,
      grant_type: "authorization_code",
      redirect_uri: c.callback,
    }).toString();
    const short = await providerJson(exchange.href, { method: "POST" }, http);
    const shortToken = demandToken(short.access_token);
    const long = new URL("https://graph.threads.net/access_token");
    long.search = new URLSearchParams({
      grant_type: "th_exchange_token",
      client_secret: c.clientSecret,
      access_token: shortToken,
    }).toString();
    const data = await providerJson(long.href, {}, http);
    return {
      provider,
      accessToken: demandToken(data.access_token),
      expiresAt: demandExpiry(data.expires_in, now),
      scopes: c.scopes,
      obtainedAt: now,
    };
  }
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: c.callback,
    client_id: c.clientId,
    client_secret: c.clientSecret,
  });
  const data = await providerJson(
    "https://www.linkedin.com/oauth/v2/accessToken",
    {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    },
    http,
  );
  const actualScopes = parseScopes(data.scope || c.scopes);
  requireScopes(actualScopes, c.scopes);
  return {
    provider,
    accessToken: demandToken(data.access_token),
    ...(data.refresh_token
      ? { refreshToken: demandToken(data.refresh_token, "refresh token") }
      : {}),
    expiresAt: demandExpiry(data.expires_in, now),
    ...(data.refresh_token_expires_in
      ? { refreshExpiresAt: demandExpiry(data.refresh_token_expires_in, now) }
      : {}),
    scopes: actualScopes,
    obtainedAt: now,
  };
}
async function refreshToken(
  env: Env,
  meta: OAuthMeta,
  current: Credential,
  refreshSecret: { refreshToken?: string },
  now: number,
  http: HttpClient,
): Promise<OAuthTokenSet> {
  const c = config(env, meta.provider);
  requireValue(
    configured(c),
    "OAUTH_NOT_CONFIGURED",
    "Provider application configuration is unavailable.",
    503,
  );
  if (meta.provider === "threads") {
    const url = new URL("https://graph.threads.net/refresh_access_token");
    url.search = new URLSearchParams({
      grant_type: "th_refresh_token",
      access_token: current.accessToken,
    }).toString();
    const data = await providerJson(url.href, {}, http);
    return {
      provider: "threads",
      accessToken: demandToken(data.access_token),
      expiresAt: demandExpiry(data.expires_in, now),
      scopes: meta.scopes,
      obtainedAt: now,
    };
  }
  requireValue(
    refreshSecret.refreshToken,
    "OAUTH_REAUTHORIZE_REQUIRED",
    "Reconnect this provider before the access token expires.",
    409,
  );
  const endpoint =
    meta.provider === "x"
      ? "https://api.x.com/2/oauth2/token"
      : "https://www.linkedin.com/oauth/v2/accessToken";
  const body = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: refreshSecret.refreshToken,
    ...(meta.provider === "x"
      ? { client_id: c.clientId }
      : {
          client_id: c.clientId,
          client_secret: c.clientSecret,
        }),
  });
  const headers: Record<string, string> = {
    "Content-Type": "application/x-www-form-urlencoded",
  };
  if (meta.provider === "x")
    headers.Authorization = `Basic ${base64Utf8(`${c.clientId}:${c.clientSecret}`)}`;
  const data = await providerJson(endpoint, { method: "POST", headers, body }, http);
  const actualScopes = parseScopes(data.scope || meta.scopes);
  requireScopes(actualScopes, c.scopes);
  return {
    provider: meta.provider,
    accessToken: demandToken(data.access_token),
    refreshToken: data.refresh_token
      ? demandToken(data.refresh_token, "refresh token")
      : refreshSecret.refreshToken,
    expiresAt: demandExpiry(data.expires_in, now),
    ...(data.refresh_token_expires_in
      ? { refreshExpiresAt: demandExpiry(data.refresh_token_expires_in, now) }
      : meta.refreshExpiresAt
        ? { refreshExpiresAt: meta.refreshExpiresAt }
        : {}),
    scopes: actualScopes,
    obtainedAt: now,
  };
}
function nextRefresh(token: OAuthTokenSet) {
  const ttl = token.expiresAt - token.obtainedAt;
  if (token.provider === "threads")
    return Math.max(
      token.obtainedAt + 24 * 3600000,
      token.expiresAt - 14 * 86400000,
    );
  if (token.provider === "linkedin" && !token.refreshToken)
    return token.expiresAt - 7 * 86400000;
  return token.obtainedAt + Math.max(5 * 60000, Math.floor(ttl * 0.7));
}
function capabilities(provider: Provider, granted: string[], refreshable: boolean) {
  return {
    oauth: true,
    refresh: refreshable,
    readback: provider !== "linkedin" || granted.includes("r_member_social"),
  };
}
function publicAccount(account: Account) {
  const { secret, ...result } = account;
  return result;
}

export class ProviderOAuthConnections {
  constructor(
    private store: Store,
    private env: Env,
    private api: ProviderAPI,
    private now: () => number = Date.now,
    private http: HttpClient = fetch,
  ) {}
  private workspace() {
    return this.store.get<string>("workspace")!;
  }
  private account(alias: string) {
    return this.store.get<Account>("account:" + alias);
  }
  private async accountCredential(account: Account) {
    return unseal<Credential>(
      account.secret,
      this.env.ENCRYPTION_KEY,
      this.workspace() + ":" + account.alias,
    );
  }
  private async metaSecret(meta: OAuthMeta) {
    return meta.secret
      ? unseal<{ refreshToken?: string }>(
          meta.secret,
          this.env.ENCRYPTION_KEY,
          this.workspace() + ":oauth:" + meta.alias,
        )
      : {};
  }
  private async metadata(alias: string, token: OAuthTokenSet): Promise<OAuthMeta> {
    const strategy =
      token.provider === "threads"
        ? "threads_long_lived"
        : token.refreshToken
          ? "refresh_token"
          : "reauthorize";
    return {
      alias,
      provider: token.provider,
      scopes: token.scopes,
      strategy,
      accessExpiresAt: token.expiresAt,
      ...(token.refreshExpiresAt ? { refreshExpiresAt: token.refreshExpiresAt } : {}),
      nextRefreshAt: nextRefresh(token),
      status: "healthy",
      ...(token.refreshToken
        ? {
            secret: await seal(
              { refreshToken: token.refreshToken },
              this.env.ENCRYPTION_KEY,
              this.workspace() + ":oauth:" + alias,
              this.env.ENCRYPTION_KEY_VERSION,
            ),
          }
        : {}),
    };
  }
  async connect(actor: Actor, input: { alias: string; token: OAuthTokenSet }) {
    requireValue(
      actor.workspace === this.workspace() &&
        !actor.grant &&
        actor.scopes.includes("admin") &&
        aliasPattern.test(input.alias),
      "OWNER_CONNECTION_REQUIRED",
      "A signed-in workspace owner must complete provider OAuth.",
      403,
    );
    const c = config(this.env, input.token.provider);
    requireValue(
      configured(c),
      "OAUTH_NOT_CONFIGURED",
      "Provider OAuth is unavailable.",
      503,
    );
    requireScopes(input.token.scopes, c.scopes);
    requireValue(
      input.token.expiresAt > this.now() + 30000,
      "OAUTH_TOKEN_INVALID",
      "Provider token expires too soon.",
      409,
    );
    const identity = await this.api.identity(input.token.provider, {
      accessToken: input.token.accessToken,
      expiresAt: input.token.expiresAt,
      ...(input.token.provider === "x" ? { funding: "service_app" as const } : {}),
    });
    const encrypted = await seal(
      {
        accessToken: input.token.accessToken,
        expiresAt: input.token.expiresAt,
        ...(input.token.provider === "x" ? { funding: "service_app" as const } : {}),
      },
      this.env.ENCRYPTION_KEY,
      actor.workspace + ":" + input.alias,
      this.env.ENCRYPTION_KEY_VERSION,
    );
    const meta = await this.metadata(input.alias, input.token);
    const old = this.account(input.alias);
    const account: Account = {
      alias: input.alias,
      provider: input.token.provider,
      identity,
      version: (old?.version || 0) + 1,
      secret: encrypted,
      active: true,
      verifiedAt: this.now(),
      capabilities: capabilities(
        input.token.provider,
        input.token.scopes,
        meta.strategy !== "reauthorize",
      ),
    };
    this.store.tx(() => {
      this.store.put("account:" + input.alias, account);
      this.store.put("oauth:" + input.alias, meta);
    });
    return { account: publicAccount(account), oauth: this.publicMeta(meta) };
  }
  private publicMeta(meta: OAuthMeta) {
    const { secret, ...result } = meta;
    return {
      ...result,
      needsReauthorization:
        result.strategy === "reauthorize" &&
        (result.status === "reauthorization_required" ||
          result.accessExpiresAt - this.now() <= 7 * 86400000),
    };
  }
  status() {
    return this.store
      .list<OAuthMeta>("oauth:")
      .sort((a, b) => a.alias.localeCompare(b.alias))
      .map((meta) => this.publicMeta(meta));
  }
  nextWake() {
    const times = this.store
      .list<OAuthMeta>("oauth:")
      .filter((meta) => this.account(meta.alias)?.active)
      .flatMap((meta) =>
        meta.strategy === "reauthorize"
          ? [meta.nextRefreshAt || meta.accessExpiresAt]
          : meta.nextRefreshAt
            ? [meta.nextRefreshAt]
            : [],
      );
    return times.length ? Math.min(...times) : undefined;
  }
  private deactivate(
    account: Account,
    meta: OAuthMeta,
    status: OAuthMeta["status"],
    reason: string,
  ) {
    account.active = false;
    account.version++;
    meta.status = status;
    meta.lastError = reason;
    meta.nextRefreshAt = undefined;
    this.store.tx(() => {
      this.store.put("account:" + account.alias, account);
      this.store.put("oauth:" + meta.alias, meta);
    });
  }
  async refreshDue(limit = 10) {
    const now = this.now();
    const due = this.store
      .list<OAuthMeta>("oauth:")
      .filter((meta) => this.account(meta.alias)?.active)
      .filter((meta) => (meta.nextRefreshAt || Infinity) <= now)
      .sort(
        (a, b) =>
          (a.nextRefreshAt || a.accessExpiresAt) -
          (b.nextRefreshAt || b.accessExpiresAt),
      )
      .slice(0, limit);
    for (const meta of due) {
      const account = this.account(meta.alias)!;
      if (meta.strategy === "reauthorize") {
        if (meta.accessExpiresAt <= now + 30000) {
          this.deactivate(account, meta, "expired", "OAUTH_REAUTHORIZE_REQUIRED");
        } else {
          meta.status = "reauthorization_required";
          meta.lastError = "OAUTH_REAUTHORIZE_REQUIRED";
          meta.nextRefreshAt = undefined;
          this.store.put("oauth:" + meta.alias, meta);
        }
        continue;
      }
      try {
        const current = await this.accountCredential(account);
        const fresh = await refreshToken(
          this.env,
          meta,
          current,
          await this.metaSecret(meta),
          now,
          this.http,
        );
        const identity = await this.api.identity(meta.provider, {
          accessToken: fresh.accessToken,
          expiresAt: fresh.expiresAt,
          ...(meta.provider === "x" ? { funding: "service_app" as const } : {}),
        });
        if (identity.id !== account.identity.id) {
          this.deactivate(account, meta, "identity_drift", "ACCOUNT_DRIFT");
          continue;
        }
        const updatedMeta = await this.metadata(meta.alias, fresh);
        updatedMeta.lastRefreshAt = now;
        const updatedCapabilities = capabilities(
          meta.provider,
          fresh.scopes,
          updatedMeta.strategy !== "reauthorize",
        );
        const capabilityChanged =
          account.capabilities?.readback !== updatedCapabilities.readback ||
          account.capabilities?.refresh !== updatedCapabilities.refresh ||
          account.capabilities?.oauth !== updatedCapabilities.oauth;
        account.secret = await seal(
          {
            accessToken: fresh.accessToken,
            expiresAt: fresh.expiresAt,
            ...(meta.provider === "x" ? { funding: "service_app" as const } : {}),
          },
          this.env.ENCRYPTION_KEY,
          this.workspace() + ":" + account.alias,
          this.env.ENCRYPTION_KEY_VERSION,
        );
        account.verifiedAt = now;
        account.capabilities = updatedCapabilities;
        if (capabilityChanged) account.version++;
        this.store.tx(() => {
          this.store.put("account:" + account.alias, account);
          this.store.put("oauth:" + meta.alias, updatedMeta);
        });
      } catch (error) {
        meta.status = "refresh_failed";
        meta.lastError =
          error instanceof Fault ? error.code : "OAUTH_REFRESH_FAILED";
        if (meta.accessExpiresAt <= now + 90000) {
          this.deactivate(account, meta, "expired", meta.lastError);
          continue;
        }
        meta.nextRefreshAt = Math.min(
          now + 15 * 60000,
          Math.max(now + 60000, meta.accessExpiresAt - 30000),
        );
        this.store.put("oauth:" + meta.alias, meta);
      }
    }
    return this.status();
  }
  async afterDisconnect(alias: string) {
    const account = this.account(alias);
    if (!account) return;
    const meta = this.store.get<OAuthMeta>("oauth:" + alias);
    if (meta?.provider === "x") {
      try {
        const c = config(this.env, "x");
        const credential = await this.accountCredential(account);
        if (
          configured(c) &&
          credential.accessToken &&
          credential.accessToken !== "disconnected"
        ) {
          const body = new URLSearchParams({
            token: credential.accessToken,
            client_id: c.clientId,
          });
          await providerJson(
            "https://api.x.com/2/oauth2/revoke",
            {
              method: "POST",
              headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                Authorization: `Basic ${base64Utf8(`${c.clientId}:${c.clientSecret}`)}`,
              },
              body,
            },
            this.http,
          );
        }
      } catch {
        // Local revocation is authoritative for PostSteward. Provider-side
        // revocation is best-effort and never keeps a disconnected token locally.
      }
    }
    account.secret = await seal(
      { accessToken: "disconnected", expiresAt: 0 },
      this.env.ENCRYPTION_KEY,
      this.workspace() + ":" + alias,
      this.env.ENCRYPTION_KEY_VERSION,
    );
    this.store.tx(() => {
      this.store.put("account:" + alias, account);
      this.store.delete("oauth:" + alias);
    });
  }
}

export async function startProviderOAuth(
  request: Request,
  env: Env,
  auth: BrowserAuth,
  provider: OAuthProvider,
) {
  requireValue(
    auth.browser && !auth.actor.grant && auth.actor.scopes.includes("admin"),
    "OWNER_CONNECTION_REQUIRED",
    "Start provider OAuth from the signed-in owner browser.",
    403,
  );
  const c = config(env, provider);
  requireValue(
    configured(c),
    "OAUTH_NOT_CONFIGURED",
    "This provider connection is not configured yet.",
    503,
  );
  const input = (await request.json()) as { alias?: unknown };
  requireValue(
    typeof input.alias === "string" && aliasPattern.test(input.alias),
    "INVALID_CONNECTION",
    "Choose a short account alias using letters, numbers, underscore or hyphen.",
    400,
  );
  const session = cookie(request, "__Host-session");
  requireValue(
    session && session.length <= 512,
    "OWNER_SESSION_REQUIRED",
    "Owner session is required.",
    401,
  );
  const state = `${uid()}.${randomSecret(24)}`;
  const verifier = provider === "x" ? randomSecret(48) : undefined;
  const stateHash = await digest(state);
  const inserted = await env.IDENTITY.prepare(
    "INSERT INTO provider_oauth_states(state_hash,session_hash,workspace,actor,provider,alias,verifier,expires_at,created_at) SELECT ?,?,?,?,?,?,?,?,? WHERE (SELECT count(*) FROM provider_oauth_states) < 10000",
  )
    .bind(
      stateHash,
      await digest(session),
      auth.actor.workspace,
      auth.actor.id,
      provider,
      input.alias,
      verifier || null,
      Date.now() + 600000,
      Date.now(),
    )
    .run();
  requireValue(
    inserted.meta.changes === 1,
    "OAUTH_CAPACITY",
    "Provider sign-in is temporarily at capacity.",
    503,
  );
  const url = new URL(c.authorizationEndpoint);
  const params: Record<string, string> = {
    response_type: "code",
    client_id: c.clientId,
    redirect_uri: c.callback,
    scope: provider === "threads" ? c.scopes.join(",") : c.scopes.join(" "),
    state,
  };
  if (verifier) {
    params.code_challenge = await challenge(verifier);
    params.code_challenge_method = "S256";
  }
  url.search = new URLSearchParams(params).toString();
  return json(
    {
      provider,
      alias: input.alias,
      authorizationUrl: url.href,
      callback: c.callback,
      scopes: c.scopes,
    },
    200,
    { "Set-Cookie": stateCookie(state, 600) },
  );
}

export async function completeProviderOAuth(
  request: Request,
  env: Env,
  auth: BrowserAuth,
  provider: OAuthProvider,
): Promise<{ alias: string; token?: OAuthTokenSet; response?: Response }> {
  requireValue(
    auth.browser && !auth.actor.grant,
    "OWNER_CONNECTION_REQUIRED",
    "Complete provider OAuth in the same signed-in browser.",
    403,
  );
  const url = new URL(request.url);
  const state = url.searchParams.get("state");
  requireValue(
    state && cookie(request, stateCookieName) === state,
    "OAUTH_STATE_INVALID",
    "Provider connection state is invalid or belongs to another browser.",
    400,
  );
  const session = cookie(request, "__Host-session");
  requireValue(
    session,
    "OWNER_SESSION_REQUIRED",
    "Owner session expired during provider connection.",
    401,
  );
  const row = await env.IDENTITY.prepare(
    "DELETE FROM provider_oauth_states WHERE state_hash=? AND expires_at>? RETURNING *",
  )
    .bind(await digest(state), Date.now())
    .first<OAuthStateRow>();
  requireValue(
    row,
    "OAUTH_STATE_EXPIRED",
    "Restart the provider connection.",
    400,
  );
  requireValue(
    row.provider === provider &&
      row.workspace === auth.actor.workspace &&
      row.actor === auth.actor.id &&
      row.session_hash === (await digest(session)) &&
      aliasPattern.test(row.alias),
    "OAUTH_STATE_INVALID",
    "Provider connection state no longer matches this owner session.",
    403,
  );
  if (url.searchParams.has("error"))
    return {
      alias: row.alias,
      response: new Response(null, {
        status: 302,
        headers: {
          Location: "/pilot?connection=denied",
          "Set-Cookie": stateCookie("", 0),
          "Cache-Control": "no-store",
        },
      }),
    };
  const code = url.searchParams.get("code");
  requireValue(
    code && code.length <= 4096,
    "OAUTH_CODE_MISSING",
    "Provider returned no authorization code.",
    400,
  );
  return {
    alias: row.alias,
    token: await exchangeCode(env, provider, code, row.verifier),
  };
}

export function providerOAuthSuccess(provider: OAuthProvider) {
  return new Response(null, {
    status: 302,
    headers: {
      Location: `/pilot?connected=${provider}`,
      "Set-Cookie": stateCookie("", 0),
      "Cache-Control": "no-store",
    },
  });
}
