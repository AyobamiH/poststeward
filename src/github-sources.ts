import * as oauth from "oauth4webapi";
import { digest, Fault, json, requireValue } from "./common.ts";
import { seal, unseal } from "./crypto.ts";
import type { OwnerAuthority } from "./owner-proof.ts";
import type { Env, Profile } from "./types.ts";

const API_VERSION = "2026-03-10";
const STATE_COOKIE = "__Host-github-source";
const STATE_TTL = 10 * 60 * 1000;
const MAX_PENDING_STATES = 5;
const MAX_LINKED_REPOSITORIES = 50;
const REQUEST_TIMEOUT = 10_000;
const REFRESH_SKEW = 5 * 60 * 1000;
const USER_INSTALLATION_PAGES = 10;

type Send = typeof fetch;

type GitHubCredential = {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
  refreshExpiresAt: number;
};

type GitHubRepository = {
  id: number;
  fullName: string;
  private: boolean;
};

type InstallationRow = {
  workspace: string;
  installation_id: number;
  account_id: number;
  account_login: string;
  account_type: string;
  user_id: number;
  user_login: string;
  repository_selection: "all" | "selected";
  status: "linked" | "stale";
  last_error?: string | null;
  credential: string;
  credential_revision: number;
  token_expires_at: number;
  refresh_expires_at: number;
  linked_at: number;
  last_verified_at: number;
  updated_at: number;
};

type RepositoryLink = {
  workspace: string;
  repository_id: number;
  installation_id: number;
  full_name: string;
  private: number;
  linked_at: number;
  verified_at: number;
};

function appConfig(env: Env) {
  const clientId = (env.GITHUB_APP_CLIENT_ID || "").trim();
  const clientSecret = (env.GITHUB_APP_CLIENT_SECRET || "").trim();
  const slug = (env.GITHUB_APP_SLUG || "").trim();
  const any = Boolean(clientId || clientSecret || slug);
  const available = Boolean(clientId && clientSecret && slug);
  if (any) {
    requireValue(
      available &&
        clientId.length <= 200 &&
        !/[\s\x00-\x1f]/.test(clientId) &&
        clientSecret.length >= 8 &&
        clientSecret.length <= 4096 &&
        /^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$/.test(slug),
      "GITHUB_APP_MISCONFIGURED",
      "GitHub source access is partially or incorrectly configured.",
      503,
    );
  }
  return { available, clientId, clientSecret, slug };
}

export function githubSourceConfiguration(env: Env) {
  const config = appConfig(env);
  return {
    available: config.available,
    ownerOnly: true,
    repositorySelection: "selected_only",
    maxRepositories: MAX_LINKED_REPOSITORIES,
    setupPath: "/sources/github/setup",
    callbackPath: "/sources/github/callback",
  };
}

function requireApp(env: Env) {
  const config = appConfig(env);
  requireValue(
    config.available,
    "GITHUB_APP_UNCONFIGURED",
    "Private GitHub source access is not configured for this deployment.",
    503,
  );
  return config;
}

function requestCookie(request: Request, name: string) {
  return request.headers
    .get("cookie")
    ?.split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(name + "="))
    ?.slice(name.length + 1);
}

function stateCookie(value: string, maxAge: number) {
  return `${STATE_COOKIE}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}

function githubHeaders(token?: string) {
  return {
    Accept: "application/vnd.github+json",
    "User-Agent": "poststeward",
    "X-GitHub-Api-Version": API_VERSION,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function boundedJson(response: Response, maxBytes: number) {
  requireValue(
    response.body,
    "GITHUB_RESPONSE_INVALID",
    "GitHub returned an empty response.",
    502,
  );
  const declared = Number(response.headers.get("content-length") || 0);
  requireValue(
    !declared || declared <= maxBytes,
    "GITHUB_RESPONSE_TOO_LARGE",
    "GitHub returned an unexpectedly large response.",
    502,
  );
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      requireValue(
        size <= maxBytes,
        "GITHUB_RESPONSE_TOO_LARGE",
        "GitHub returned an unexpectedly large response.",
        502,
      );
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(bytes)) as any;
  } catch {
    throw new Fault(
      "GITHUB_RESPONSE_INVALID",
      "GitHub returned an invalid response.",
      502,
    );
  }
}

async function apiGet(url: URL, token: string | undefined, send: Send) {
  requireValue(
    url.protocol === "https:" && url.hostname === "api.github.com",
    "GITHUB_HOST_INVALID",
    "GitHub API host is invalid.",
    500,
  );
  const response = await send(url, {
    method: "GET",
    headers: githubHeaders(token),
    redirect: "manual",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT),
  }).catch(() => {
    throw new Fault(
      "SOURCE_UNAVAILABLE",
      "GitHub source request did not complete.",
      502,
    );
  });
  return response;
}

function installationId(value: string | null) {
  requireValue(
    value && /^\d{1,16}$/.test(value),
    "GITHUB_INSTALLATION_INVALID",
    "GitHub did not return a valid installation identifier.",
    400,
  );
  const id = Number(value);
  requireValue(
    Number.isSafeInteger(id) && id > 0,
    "GITHUB_INSTALLATION_INVALID",
    "GitHub did not return a valid installation identifier.",
    400,
  );
  return id;
}

function credentialFromTokenResponse(data: any, now: number): GitHubCredential {
  requireValue(
    typeof data?.access_token === "string" &&
      data.access_token.length >= 20 &&
      data.access_token.length <= 4096 &&
      typeof data?.refresh_token === "string" &&
      data.refresh_token.length >= 20 &&
      data.refresh_token.length <= 4096 &&
      Number.isInteger(data?.expires_in) &&
      data.expires_in >= 300 &&
      data.expires_in <= 86_400 &&
      Number.isInteger(data?.refresh_token_expires_in) &&
      data.refresh_token_expires_in > data.expires_in &&
      data.refresh_token_expires_in <= 400 * 24 * 60 * 60,
    "GITHUB_TOKEN_INVALID",
    "GitHub did not return an expiring, refreshable user credential.",
    502,
  );
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresAt: now + data.expires_in * 1000,
    refreshExpiresAt: now + data.refresh_token_expires_in * 1000,
  };
}

async function exchangeCode(
  env: Env,
  code: string,
  verifier: string,
  send: Send,
  now: number,
) {
  const config = requireApp(env);
  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    code,
    redirect_uri: env.PUBLIC_ORIGIN + "/sources/github/callback",
    code_verifier: verifier,
  });
  const response = await send("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent": "poststeward",
    },
    body,
    redirect: "manual",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT),
  }).catch(() => {
    throw new Fault(
      "GITHUB_AUTH_UNAVAILABLE",
      "GitHub authorization did not complete.",
      502,
    );
  });
  requireValue(
    response.ok,
    "GITHUB_AUTH_FAILED",
    `GitHub authorization returned HTTP ${response.status}.`,
    502,
  );
  const data = await boundedJson(response, 64 * 1024);
  requireValue(
    !data?.error,
    "GITHUB_AUTH_FAILED",
    "GitHub authorization was rejected. Restart repository connection.",
    409,
  );
  return credentialFromTokenResponse(data, now);
}

async function refreshToken(
  env: Env,
  refreshToken: string,
  send: Send,
  now: number,
) {
  const config = requireApp(env);
  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    grant_type: "refresh_token",
    refresh_token: refreshToken,
  });
  const response = await send("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent": "poststeward",
    },
    body,
    redirect: "manual",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT),
  }).catch(() => {
    throw new Fault(
      "SOURCE_UNAVAILABLE",
      "GitHub credential refresh did not complete.",
      502,
    );
  });
  if (response.status >= 500)
    throw new Fault(
      "SOURCE_UNAVAILABLE",
      "GitHub credential refresh is temporarily unavailable.",
      502,
    );
  requireValue(
    response.ok,
    "GITHUB_REAUTH_REQUIRED",
    "GitHub repository authorization expired or was revoked. Reconnect GitHub.",
    409,
  );
  const data = await boundedJson(response, 64 * 1024);
  requireValue(
    !data?.error,
    "GITHUB_REAUTH_REQUIRED",
    "GitHub repository authorization expired or was revoked. Reconnect GitHub.",
    409,
  );
  return credentialFromTokenResponse(data, now);
}

async function markInstallationStale(
  env: Env,
  workspace: string,
  id: number,
  code: string,
  now: number,
) {
  await env.IDENTITY.prepare(
    "UPDATE github_installations SET status='stale',last_error=?,updated_at=? WHERE workspace=? AND installation_id=?",
  )
    .bind(code.slice(0, 100), now, workspace, id)
    .run();
}

async function readInstallation(
  env: Env,
  workspace: string,
  id: number,
): Promise<InstallationRow | undefined> {
  return (
    (await env.IDENTITY.prepare(
      "SELECT * FROM github_installations WHERE workspace=? AND installation_id=?",
    )
      .bind(workspace, id)
      .first<InstallationRow>()) || undefined
  );
}

async function activeCredential(
  env: Env,
  row: InstallationRow,
  send: Send,
  now: number,
): Promise<GitHubCredential> {
  requireValue(
    row.status === "linked",
    "GITHUB_REAUTH_REQUIRED",
    "GitHub repository authorization is stale. Reconnect GitHub.",
    409,
  );
  const context = `${row.workspace}:github:${row.installation_id}`;
  const current = await unseal<GitHubCredential>(
    row.credential,
    env.ENCRYPTION_KEY,
    context,
  );
  if (current.expiresAt > now + REFRESH_SKEW) return current;
  if (current.refreshExpiresAt <= now + REFRESH_SKEW) {
    await markInstallationStale(
      env,
      row.workspace,
      row.installation_id,
      "GITHUB_REAUTH_REQUIRED",
      now,
    );
    throw new Fault(
      "GITHUB_REAUTH_REQUIRED",
      "GitHub repository authorization must be renewed.",
      409,
    );
  }

  let refreshed: GitHubCredential;
  try {
    refreshed = await refreshToken(env, current.refreshToken, send, now);
  } catch (error) {
    const latest = await readInstallation(
      env,
      row.workspace,
      row.installation_id,
    );
    if (
      latest &&
      latest.status === "linked" &&
      latest.credential_revision !== row.credential_revision &&
      latest.token_expires_at > now + 60_000
    )
      return unseal<GitHubCredential>(
        latest.credential,
        env.ENCRYPTION_KEY,
        context,
      );
    if (error instanceof Fault && error.code === "GITHUB_REAUTH_REQUIRED")
      await markInstallationStale(
        env,
        row.workspace,
        row.installation_id,
        error.code,
        now,
      );
    throw error;
  }

  const encrypted = await seal(
    refreshed,
    env.ENCRYPTION_KEY,
    context,
    env.ENCRYPTION_KEY_VERSION,
  );
  const updated = await env.IDENTITY.prepare(
    "UPDATE github_installations SET credential=?,credential_revision=credential_revision+1,token_expires_at=?,refresh_expires_at=?,status='linked',last_error=NULL,updated_at=? WHERE workspace=? AND installation_id=? AND credential_revision=? AND status='linked'",
  )
    .bind(
      encrypted,
      refreshed.expiresAt,
      refreshed.refreshExpiresAt,
      now,
      row.workspace,
      row.installation_id,
      row.credential_revision,
    )
    .run();
  if (updated.meta.changes === 1) return refreshed;

  const winner = await readInstallation(env, row.workspace, row.installation_id);
  requireValue(
    winner?.status === "linked" &&
      winner.credential_revision !== row.credential_revision,
    "GITHUB_REAUTH_REQUIRED",
    "GitHub repository authorization changed. Reconnect GitHub if the next check fails.",
    409,
  );
  return unseal<GitHubCredential>(
    winner.credential,
    env.ENCRYPTION_KEY,
    context,
  );
}

async function accessibleInstallation(
  token: string,
  candidate: number,
  expectedSlug: string,
  send: Send,
) {
  for (let page = 1; page <= USER_INSTALLATION_PAGES; page++) {
    const url = new URL("https://api.github.com/user/installations");
    url.search = new URLSearchParams({
      per_page: "100",
      page: String(page),
    }).toString();
    const response = await apiGet(url, token, send);
    if (response.status === 401)
      throw new Fault(
        "GITHUB_REAUTH_REQUIRED",
        "GitHub repository authorization expired or was revoked. Reconnect GitHub.",
        409,
      );
    if (response.status === 403)
      throw new Fault(
        "GITHUB_ACCESS_REVOKED",
        "The signed-in GitHub user can no longer inspect this installation.",
        409,
      );
    requireValue(
      response.ok,
      "GITHUB_INSTALLATION_VERIFY_FAILED",
      `GitHub installation verification returned HTTP ${response.status}.`,
      502,
    );
    const data = await boundedJson(response, 1024 * 1024);
    const installations = Array.isArray(data?.installations)
      ? data.installations
      : [];
    const found = installations.find(
      (installation: any) => installation?.id === candidate,
    );
    if (found) {
      requireValue(
        found.app_slug === expectedSlug && found.suspended_at == null,
        "GITHUB_INSTALLATION_INVALID",
        "The selected GitHub App installation is not active for this application.",
        409,
      );
      requireValue(
        found.repository_selection === "selected",
        "GITHUB_REPOSITORY_SCOPE_TOO_BROAD",
        "Reconnect the GitHub App with only selected repositories.",
        409,
      );
      const permissions = found.permissions || {};
      const activePermissions = Object.entries(permissions).filter(
        ([, level]) => level && level !== "none",
      );
      requireValue(
        permissions.contents === "read" &&
          activePermissions.every(
            ([name, level]) =>
              ["contents", "metadata"].includes(name) && level === "read",
          ),
        "GITHUB_APP_PERMISSION_TOO_BROAD",
        "The GitHub App must have read-only Contents access and no write permissions.",
        409,
      );
      return found;
    }
    if (installations.length < 100) break;
  }
  throw new Fault(
    "GITHUB_INSTALLATION_NOT_AUTHORISED",
    "The signed-in GitHub user cannot access that installation.",
    403,
  );
}

async function accessibleRepositories(
  token: string,
  id: number,
  send: Send,
): Promise<GitHubRepository[]> {
  const url = new URL(
    `https://api.github.com/user/installations/${id}/repositories`,
  );
  url.search = new URLSearchParams({ per_page: "100", page: "1" }).toString();
  const response = await apiGet(url, token, send);
  if (response.status === 401)
    throw new Fault(
      "GITHUB_REAUTH_REQUIRED",
      "GitHub repository authorization expired or was revoked. Reconnect GitHub.",
      409,
    );
  if (response.status === 403)
    throw new Fault(
      "GITHUB_ACCESS_REVOKED",
      "GitHub repository access is no longer authorised. Reconnect GitHub.",
      409,
    );
  requireValue(
    response.ok,
    "GITHUB_REPOSITORY_VERIFY_FAILED",
    `GitHub repository verification returned HTTP ${response.status}.`,
    502,
  );
  const data = await boundedJson(response, 2 * 1024 * 1024);
  const repositories = Array.isArray(data?.repositories) ? data.repositories : [];
  requireValue(
    Number.isInteger(data?.total_count) &&
      data.total_count > 0 &&
      data.total_count <= MAX_LINKED_REPOSITORIES &&
      repositories.length === data.total_count,
    "GITHUB_REPOSITORY_SCOPE_TOO_BROAD",
    `Select between 1 and ${MAX_LINKED_REPOSITORIES} repositories for PostSteward.`,
    409,
  );
  const seen = new Set<number>();
  return repositories.map((repository: any): GitHubRepository => {
    requireValue(
      Number.isSafeInteger(repository?.id) &&
        repository.id > 0 &&
        typeof repository?.full_name === "string" &&
        /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository.full_name) &&
        typeof repository?.private === "boolean" &&
        !seen.has(repository.id),
      "GITHUB_REPOSITORY_INVALID",
      "GitHub returned invalid repository metadata.",
      502,
    );
    seen.add(repository.id);
    return {
      id: repository.id as number,
      fullName: repository.full_name as string,
      private: repository.private as boolean,
    };
  });
}

async function githubUser(token: string, send: Send) {
  const response = await apiGet(
    new URL("https://api.github.com/user"),
    token,
    send,
  );
  requireValue(
    response.ok,
    "GITHUB_USER_VERIFY_FAILED",
    `GitHub user verification returned HTTP ${response.status}.`,
    502,
  );
  const data = await boundedJson(response, 256 * 1024);
  requireValue(
    Number.isSafeInteger(data?.id) &&
      data.id > 0 &&
      typeof data?.login === "string" &&
      data.login.length > 0 &&
      data.login.length <= 100,
    "GITHUB_USER_INVALID",
    "GitHub returned invalid user identity metadata.",
    502,
  );
  return { id: data.id as number, login: data.login as string };
}

export async function startGitHubSourceLink(
  env: Env,
  owner: OwnerAuthority,
  now = Date.now(),
) {
  const config = requireApp(env);
  const state = oauth.generateRandomState();
  const verifier = oauth.generateRandomCodeVerifier();
  const stateHash = await digest(state);
  const expiresAt = now + STATE_TTL;
  await env.IDENTITY.prepare(
    "DELETE FROM github_install_states WHERE expires_at<=?",
  )
    .bind(now)
    .run();
  const inserted = await env.IDENTITY.prepare(
    "INSERT INTO github_install_states(state_hash,session_hash,workspace,actor,verifier,installation_id,expires_at,created_at) SELECT ?,?,?,?,?,NULL,?,? WHERE (SELECT count(*) FROM github_install_states WHERE session_hash=? AND expires_at>?)<?",
  )
    .bind(
      stateHash,
      owner.sessionHash,
      owner.proof.workspace,
      owner.proof.subject,
      verifier,
      expiresAt,
      now,
      owner.sessionHash,
      now,
      MAX_PENDING_STATES,
    )
    .run();
  requireValue(
    inserted.meta.changes === 1,
    "GITHUB_LINK_CAPACITY",
    "Too many GitHub repository connection attempts are already pending.",
    429,
  );
  const installationUrl = new URL(
    `https://github.com/apps/${config.slug}/installations/new`,
  );
  installationUrl.searchParams.set("state", state);
  return json(
    { installationUrl: installationUrl.href, expiresAt },
    200,
    { "Set-Cookie": stateCookie(state, STATE_TTL / 1000) },
  );
}

export async function continueGitHubSourceSetup(
  request: Request,
  env: Env,
  owner: OwnerAuthority,
  now = Date.now(),
) {
  const config = requireApp(env);
  const url = new URL(request.url);
  const state = url.searchParams.get("state");
  requireValue(
    state &&
      state.length <= 512 &&
      requestCookie(request, STATE_COOKIE) === state,
    "GITHUB_LINK_STATE_INVALID",
    "GitHub repository connection state is invalid. Restart connection.",
    400,
  );
  const candidate = installationId(url.searchParams.get("installation_id"));
  const stateHash = await digest(state);
  const updated = await env.IDENTITY.prepare(
    "UPDATE github_install_states SET installation_id=? WHERE state_hash=? AND session_hash=? AND workspace=? AND actor=? AND expires_at>? AND (installation_id IS NULL OR installation_id=?)",
  )
    .bind(
      candidate,
      stateHash,
      owner.sessionHash,
      owner.proof.workspace,
      owner.proof.subject,
      now,
      candidate,
    )
    .run();
  requireValue(
    updated.meta.changes === 1,
    "GITHUB_LINK_STATE_INVALID",
    "GitHub repository connection state expired or changed. Restart connection.",
    400,
  );
  const pending = await env.IDENTITY.prepare(
    "SELECT verifier FROM github_install_states WHERE state_hash=? AND session_hash=?",
  )
    .bind(stateHash, owner.sessionHash)
    .first<{ verifier: string }>();
  requireValue(
    pending,
    "GITHUB_LINK_STATE_INVALID",
    "GitHub repository connection state is unavailable. Restart connection.",
    400,
  );
  const authorizationUrl = new URL("https://github.com/login/oauth/authorize");
  authorizationUrl.search = new URLSearchParams({
    client_id: config.clientId,
    redirect_uri: env.PUBLIC_ORIGIN + "/sources/github/callback",
    state,
    code_challenge: await oauth.calculatePKCECodeChallenge(pending.verifier),
    code_challenge_method: "S256",
  }).toString();
  return new Response(null, {
    status: 302,
    headers: {
      Location: authorizationUrl.href,
      "Cache-Control": "no-store",
      "Referrer-Policy": "no-referrer",
    },
  });
}

export async function completeGitHubSourceLink(
  request: Request,
  env: Env,
  owner: OwnerAuthority,
  send: Send = fetch,
  now = Date.now(),
) {
  const config = requireApp(env);
  const url = new URL(request.url);
  const state = url.searchParams.get("state");
  requireValue(
    state &&
      state.length <= 512 &&
      requestCookie(request, STATE_COOKIE) === state,
    "GITHUB_LINK_STATE_INVALID",
    "GitHub repository connection state is invalid. Restart connection.",
    400,
  );
  const stateHash = await digest(state);
  if (url.searchParams.get("error")) {
    await env.IDENTITY.prepare(
      "DELETE FROM github_install_states WHERE state_hash=? AND session_hash=? AND workspace=? AND actor=?",
    )
      .bind(
        stateHash,
        owner.sessionHash,
        owner.proof.workspace,
        owner.proof.subject,
      )
      .run();
    return new Response(null, {
      status: 302,
      headers: {
        Location: env.PUBLIC_ORIGIN + "/app?github=cancelled",
        "Set-Cookie": stateCookie("", 0),
        "Cache-Control": "no-store",
      },
    });
  }
  const code = url.searchParams.get("code");
  requireValue(
    code && code.length <= 1024,
    "GITHUB_AUTH_CODE_MISSING",
    "GitHub returned no authorization code. Restart connection.",
    400,
  );
  const pending = await env.IDENTITY.prepare(
    "DELETE FROM github_install_states WHERE state_hash=? AND session_hash=? AND workspace=? AND actor=? AND expires_at>? AND installation_id IS NOT NULL RETURNING verifier,installation_id",
  )
    .bind(
      stateHash,
      owner.sessionHash,
      owner.proof.workspace,
      owner.proof.subject,
      now,
    )
    .first<{ verifier: string; installation_id: number }>();
  requireValue(
    pending && Number.isSafeInteger(pending.installation_id),
    "GITHUB_LINK_STATE_EXPIRED",
    "GitHub repository connection expired or was already consumed. Restart connection.",
    400,
  );

  const credential = await exchangeCode(env, code, pending.verifier, send, now);
  const user = await githubUser(credential.accessToken, send);
  const installation = await accessibleInstallation(
    credential.accessToken,
    pending.installation_id,
    config.slug,
    send,
  );
  const repositories = await accessibleRepositories(
    credential.accessToken,
    pending.installation_id,
    send,
  );
  const account = installation.account || {};
  requireValue(
    Number.isSafeInteger(account.id) &&
      account.id > 0 &&
      typeof account.login === "string" &&
      account.login.length > 0 &&
      account.login.length <= 100 &&
      ["User", "Organization"].includes(account.type),
    "GITHUB_INSTALLATION_INVALID",
    "GitHub returned invalid installation account metadata.",
    502,
  );
  const encrypted = await seal(
    credential,
    env.ENCRYPTION_KEY,
    `${owner.proof.workspace}:github:${pending.installation_id}`,
    env.ENCRYPTION_KEY_VERSION,
  );
  const statements = [
    env.IDENTITY.prepare(
      "INSERT INTO github_installations(workspace,installation_id,account_id,account_login,account_type,user_id,user_login,repository_selection,status,last_error,credential,credential_revision,token_expires_at,refresh_expires_at,linked_at,last_verified_at,updated_at) VALUES (?,?,?,?,?,?,?,'selected','linked',NULL,?,1,?,?,?,?,?) ON CONFLICT(workspace,installation_id) DO UPDATE SET account_id=excluded.account_id,account_login=excluded.account_login,account_type=excluded.account_type,user_id=excluded.user_id,user_login=excluded.user_login,repository_selection='selected',status='linked',last_error=NULL,credential=excluded.credential,credential_revision=github_installations.credential_revision+1,token_expires_at=excluded.token_expires_at,refresh_expires_at=excluded.refresh_expires_at,last_verified_at=excluded.last_verified_at,updated_at=excluded.updated_at",
    ).bind(
      owner.proof.workspace,
      pending.installation_id,
      account.id,
      account.login,
      account.type,
      user.id,
      user.login,
      encrypted,
      credential.expiresAt,
      credential.refreshExpiresAt,
      now,
      now,
      now,
    ),
    env.IDENTITY.prepare(
      "DELETE FROM github_repository_links WHERE workspace=? AND installation_id=?",
    ).bind(owner.proof.workspace, pending.installation_id),
    ...repositories.map((repository: GitHubRepository) =>
      env.IDENTITY.prepare(
        "INSERT INTO github_repository_links(workspace,repository_id,installation_id,full_name,private,linked_at,verified_at) VALUES (?,?,?,?,?,?,?)",
      ).bind(
        owner.proof.workspace,
        repository.id,
        pending.installation_id,
        repository.fullName,
        repository.private ? 1 : 0,
        now,
        now,
      ),
    ),
  ];
  await env.IDENTITY.batch(statements);
  return new Response(null, {
    status: 302,
    headers: {
      Location: env.PUBLIC_ORIGIN + "/app?github=linked",
      "Set-Cookie": stateCookie("", 0),
      "Cache-Control": "no-store",
      "Referrer-Policy": "no-referrer",
    },
  });
}

export async function githubSourceStatus(env: Env, workspace: string) {
  const configuration = githubSourceConfiguration(env);
  const installations = await env.IDENTITY.prepare(
    "SELECT installation_id,account_id,account_login,account_type,user_id,user_login,repository_selection,status,last_error,token_expires_at,refresh_expires_at,linked_at,last_verified_at,updated_at FROM github_installations WHERE workspace=? ORDER BY linked_at DESC",
  )
    .bind(workspace)
    .all<any>();
  const repositories = await env.IDENTITY.prepare(
    "SELECT repository_id,installation_id,full_name,private,linked_at,verified_at FROM github_repository_links WHERE workspace=? ORDER BY full_name",
  )
    .bind(workspace)
    .all<any>();
  return {
    configuration,
    installations: installations.results || [],
    repositories: repositories.results || [],
  };
}

export async function unlinkGitHubSource(
  env: Env,
  workspace: string,
  id: number,
) {
  requireValue(
    Number.isSafeInteger(id) && id > 0,
    "GITHUB_INSTALLATION_INVALID",
    "Choose a valid linked GitHub installation.",
  );
  const results = await env.IDENTITY.batch([
    env.IDENTITY.prepare(
      "DELETE FROM github_repository_links WHERE workspace=? AND installation_id=?",
    ).bind(workspace, id),
    env.IDENTITY.prepare(
      "DELETE FROM github_installations WHERE workspace=? AND installation_id=?",
    ).bind(workspace, id),
  ]);
  return { installationId: id, unlinked: results[1]?.meta.changes === 1 };
}

async function linkedRepository(
  env: Env,
  workspace: string,
  fullName: string,
): Promise<(RepositoryLink & InstallationRow) | undefined> {
  return (
    (await env.IDENTITY.prepare(
      "SELECT r.workspace,r.repository_id,r.installation_id,r.full_name,r.private,r.linked_at,r.verified_at,i.account_id,i.account_login,i.account_type,i.user_id,i.user_login,i.repository_selection,i.status,i.last_error,i.credential,i.credential_revision,i.token_expires_at,i.refresh_expires_at,i.last_verified_at,i.updated_at FROM github_repository_links r JOIN github_installations i ON i.workspace=r.workspace AND i.installation_id=r.installation_id WHERE r.workspace=? AND r.full_name=?",
    )
      .bind(workspace, fullName)
      .first<any>()) || undefined
  );
}

async function verifyLinkedRepositoryStillAccessible(
  env: Env,
  link: RepositoryLink & InstallationRow,
  credential: GitHubCredential,
  send: Send,
  now: number,
) {
  const config = requireApp(env);
  try {
    await accessibleInstallation(
      credential.accessToken,
      link.installation_id,
      config.slug,
      send,
    );
  } catch (error) {
    if (
      error instanceof Fault &&
      [
        "GITHUB_REAUTH_REQUIRED",
        "GITHUB_ACCESS_REVOKED",
        "GITHUB_INSTALLATION_NOT_AUTHORISED",
        "GITHUB_INSTALLATION_INVALID",
        "GITHUB_REPOSITORY_SCOPE_TOO_BROAD",
        "GITHUB_APP_PERMISSION_TOO_BROAD",
      ].includes(error.code)
    )
      await markInstallationStale(
        env,
        link.workspace,
        link.installation_id,
        error.code,
        now,
      );
    throw error;
  }

  let repositories: GitHubRepository[];
  try {
    repositories = await accessibleRepositories(
      credential.accessToken,
      link.installation_id,
      send,
    );
  } catch (error) {
    if (
      error instanceof Fault &&
      [
        "GITHUB_REAUTH_REQUIRED",
        "GITHUB_ACCESS_REVOKED",
        "GITHUB_REPOSITORY_SCOPE_TOO_BROAD",
      ].includes(error.code)
    )
      await markInstallationStale(
        env,
        link.workspace,
        link.installation_id,
        error.code,
        now,
      );
    throw error;
  }
  const current = repositories.find(
    (repository: GitHubRepository) => repository.id === link.repository_id,
  );
  if (!current) {
    await env.IDENTITY.prepare(
      "DELETE FROM github_repository_links WHERE workspace=? AND repository_id=?",
    )
      .bind(link.workspace, link.repository_id)
      .run();
    throw new Fault(
      "GITHUB_REPOSITORY_ACCESS_REVOKED",
      "This repository is no longer available through the linked GitHub installation.",
      409,
    );
  }
  if (current.fullName !== link.full_name) {
    await env.IDENTITY.prepare(
      "UPDATE github_repository_links SET full_name=?,private=?,verified_at=? WHERE workspace=? AND repository_id=?",
    )
      .bind(
        current.fullName,
        current.private ? 1 : 0,
        now,
        link.workspace,
        link.repository_id,
      )
      .run();
    throw new Fault(
      "GITHUB_REPOSITORY_RENAMED",
      "The linked GitHub repository was renamed. Review and update the automation profile.",
      409,
    );
  }
  await env.IDENTITY.batch([
    env.IDENTITY.prepare(
      "UPDATE github_repository_links SET private=?,verified_at=? WHERE workspace=? AND repository_id=?",
    ).bind(current.private ? 1 : 0, now, link.workspace, link.repository_id),
    env.IDENTITY.prepare(
      "UPDATE github_installations SET status='linked',last_error=NULL,last_verified_at=?,updated_at=? WHERE workspace=? AND installation_id=?",
    ).bind(now, now, link.workspace, link.installation_id),
  ]);
}

async function commitSnapshot(
  profile: Profile,
  token: string | undefined,
  send: Send,
) {
  const url = new URL(
    `https://api.github.com/repos/${profile.repository}/commits`,
  );
  url.search = new URLSearchParams({
    sha: profile.branch,
    path: profile.path,
    per_page: "1",
  }).toString();
  const response = await apiGet(url, token, send);
  if (
    response.status === 429 ||
    response.headers.get("x-ratelimit-remaining") === "0"
  )
    throw new Fault(
      "GITHUB_RATE_LIMITED",
      "GitHub source checks are temporarily rate limited.",
      429,
    );
  return response;
}

export async function readGitHubSource(
  profile: Profile,
  env: Env,
  workspace: string,
  send: Send = fetch,
  now = Date.now(),
) {
  const link = await linkedRepository(env, workspace, profile.repository);
  if (!link) {
    const response = await commitSnapshot(profile, undefined, send);
    requireValue(
      response.ok,
      "SOURCE_UNAVAILABLE",
      `Repository source returned HTTP ${response.status}.`,
      502,
    );
    const data = await boundedJson(response, 512 * 1024);
    requireValue(
      Array.isArray(data) && /^[a-f0-9]{40}$/.test(data[0]?.sha),
      "SOURCE_INVALID",
      "No valid source snapshot was returned.",
      502,
    );
    return { sha: data[0].sha as string };
  }

  const credential = await activeCredential(env, link, send, now);
  await verifyLinkedRepositoryStillAccessible(env, link, credential, send, now);
  const response = await commitSnapshot(profile, credential.accessToken, send);
  if (response.status === 401) {
    await markInstallationStale(
      env,
      workspace,
      link.installation_id,
      "GITHUB_REAUTH_REQUIRED",
      now,
    );
    throw new Fault(
      "GITHUB_REAUTH_REQUIRED",
      "GitHub repository authorization was revoked. Reconnect GitHub.",
      409,
    );
  }
  if (response.status === 403) {
    await markInstallationStale(
      env,
      workspace,
      link.installation_id,
      "GITHUB_ACCESS_REVOKED",
      now,
    );
    throw new Fault(
      "GITHUB_ACCESS_REVOKED",
      "GitHub repository access is no longer authorised. Reconnect GitHub.",
      409,
    );
  }
  requireValue(
    response.ok,
    "SOURCE_UNAVAILABLE",
    `Repository source returned HTTP ${response.status}.`,
    502,
  );
  const data = await boundedJson(response, 512 * 1024);
  requireValue(
    Array.isArray(data) && /^[a-f0-9]{40}$/.test(data[0]?.sha),
    "SOURCE_INVALID",
    "No valid source snapshot was returned.",
    502,
  );
  return { sha: data[0].sha as string };
}
