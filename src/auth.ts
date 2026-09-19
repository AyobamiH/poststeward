import * as oauth from "oauth4webapi";
import { digest, Fault, json, requireValue, uid } from "./common.ts";
import { activeOwnerSession, ownerProofStatement } from "./owner-proof.ts";
import { loginFailure, type LoginStage } from "./login-failure.ts";
import type { Actor, Env, Scope } from "./types.ts";
export const scopes: Scope[] = [
  "read",
  "campaign:write",
  "publish",
  "schedule",
  "connections",
  "automation",
  "billing",
  "admin",
];
function cookie(request: Request, name: string) {
  return request.headers
    .get("cookie")
    ?.split(";")
    .map((p) => p.trim())
    .find((p) => p.startsWith(name + "="))
    ?.slice(name.length + 1);
}
function sessionCookie(value: string, maxAge: number) {
  return `__Host-session=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}
function stateCookie(value: string, maxAge: number) {
  return `__Host-login=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}
const token = () => `${uid()}.${uid()}`;
export async function authenticate(
  request: Request,
  env: Env,
): Promise<{ actor: Actor; csrf?: string; browser: boolean }> {
  const authorization = request.headers.get("authorization");
  if (authorization !== null) {
    requireValue(
      /^Bearer [^\s]{1,512}$/.test(authorization),
      "UNAUTHENTICATED",
      "Supply a valid Bearer token.",
      401,
    );
    const hash = await digest(authorization.slice(7));
    const row = await env.IDENTITY.prepare(
      "SELECT * FROM grants WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?",
    )
      .bind(hash, Date.now())
      .first<any>();
    requireValue(
      row,
      "UNAUTHENTICATED",
      "Agent token is invalid, expired or revoked.",
      401,
    );
    return {
      actor: {
        workspace: row.workspace,
        id: row.actor,
        scopes: JSON.parse(row.scopes),
        grant: hash,
      },
      browser: false,
    };
  }
  const session = cookie(request, "__Host-session");
  requireValue(
    session,
    "UNAUTHENTICATED",
    "Sign in or supply a scoped agent token.",
    401,
  );
  const row = await env.IDENTITY.prepare(
    "SELECT * FROM sessions WHERE token_hash=? AND expires_at>?",
  )
    .bind(await digest(session), Date.now())
    .first<any>();
  requireValue(row, "UNAUTHENTICATED", "Session expired. Sign in again.", 401);
  if (!["GET", "HEAD"].includes(request.method))
    requireValue(
      request.headers.get("origin") === env.PUBLIC_ORIGIN &&
        request.headers.get("x-csrf-token") === row.csrf,
      "CSRF_REJECTED",
      "The request must originate from the signed-in page.",
      403,
    );
  return {
    actor: { workspace: row.workspace, id: row.actor, scopes: ["admin"] },
    csrf: row.csrf,
    browser: true,
  };
}
export async function isAuthorized(actor: Actor, env: Env) {
  // Only the controlled pilot binds continuing dispatch to a particular owner
  // session. Ordinary approved schedules retain their existing authority model.
  if (actor.ownerSession) return !!(await activeOwnerSession(actor, env));
  if (!actor.grant)
    return !!(await env.IDENTITY.prepare(
      "SELECT subject FROM principals WHERE subject=? AND workspace=?",
    )
      .bind(actor.id, actor.workspace)
      .first());
  const row = await env.IDENTITY.prepare(
    "SELECT token_hash FROM grants WHERE token_hash=? AND workspace=? AND revoked_at IS NULL AND expires_at>?",
  )
    .bind(actor.grant, actor.workspace, Date.now())
    .first();
  return !!row;
}
async function oidc(env: Env) {
  requireValue(
    env.OIDC_ISSUER && env.OIDC_CLIENT_ID && env.OIDC_CLIENT_SECRET,
    "LOGIN_UNCONFIGURED",
    "Owner sign-in has not been configured.",
    503,
  );
  const issuer = new URL(env.OIDC_ISSUER);
  requireValue(
    issuer.protocol === "https:",
    "LOGIN_UNCONFIGURED",
    "OIDC issuer must use HTTPS.",
    503,
  );
  const response = await oauth.discoveryRequest(issuer, {
    signal: AbortSignal.timeout(10000),
  });
  return {
    as: await oauth.processDiscoveryResponse(issuer, response),
    client: { client_id: env.OIDC_CLIENT_ID },
  };
}
export function allowOwner(
  claims: Record<string, unknown>,
  env: Pick<Env, "SIGNUP_MODE" | "ALLOWED_OWNER_EMAILS">,
) {
  requireValue(
    claims.email_verified === true &&
      typeof claims.email === "string" &&
      /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(claims.email),
    "OWNER_EMAIL_UNVERIFIED",
    "Use a provider account with a verified email address.",
    403,
  );
  if (env.SIGNUP_MODE === "public") return;
  const allowed = (env.ALLOWED_OWNER_EMAILS || "")
    .split(",")
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);
  requireValue(
    env.SIGNUP_MODE === "restricted" &&
      allowed.includes(claims.email.toLowerCase()),
    "SIGNUP_RESTRICTED",
    "This deployment is limited to invited owners.",
    403,
  );
}
function publicAdmissionLimit(value: string, name: string, maximum: number) {
  const parsed = Number(value);
  requireValue(
    Number.isInteger(parsed) && parsed >= 1 && parsed <= maximum,
    "PUBLIC_ADMISSION_UNCONFIGURED",
    `${name} is not configured safely.`,
    503,
  );
  return parsed;
}

export async function resolveOwnerPrincipal(
  env: Env,
  subject: string,
  now = Date.now(),
) {
  const existing = await env.IDENTITY.prepare(
    "SELECT workspace,created_at FROM principals WHERE subject=?",
  )
    .bind(subject)
    .first<{ workspace: string; created_at: number }>();
  if (existing) {
    await env.IDENTITY.prepare(
      "INSERT OR IGNORE INTO workspace_admissions(workspace,created_at,admission_mode) VALUES (?,?,'preexisting')",
    )
      .bind(existing.workspace, existing.created_at)
      .run();
    return { workspace: existing.workspace };
  }

  const workspace = uid();
  if (env.SIGNUP_MODE !== "public") {
    await env.IDENTITY.batch([
      env.IDENTITY.prepare(
        "INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?) ON CONFLICT(subject) DO NOTHING",
      ).bind(subject, workspace, now),
      env.IDENTITY.prepare(
        `INSERT OR IGNORE INTO workspace_admissions(workspace,created_at,admission_mode)
         SELECT workspace,created_at,'restricted' FROM principals WHERE subject=?`,
      ).bind(subject),
    ]);
  } else {
    const workspaceLimit = publicAdmissionLimit(
      env.PUBLIC_WORKSPACE_LIMIT,
      "PUBLIC_WORKSPACE_LIMIT",
      10_000,
    );
    const hourlyLimit = publicAdmissionLimit(
      env.PUBLIC_SIGNUPS_PER_HOUR,
      "PUBLIC_SIGNUPS_PER_HOUR",
      1_000,
    );
    await env.IDENTITY.batch([
      env.IDENTITY.prepare(
        `INSERT INTO principals(subject,workspace,created_at)
         SELECT ?,?,?
         WHERE (SELECT count(*) FROM workspace_admissions) < ?
           AND (SELECT count(*) FROM workspace_admissions WHERE created_at>=?) < ?
         ON CONFLICT(subject) DO NOTHING`,
      ).bind(
        subject,
        workspace,
        now,
        workspaceLimit,
        now - 60 * 60_000,
        hourlyLimit,
      ),
      env.IDENTITY.prepare(
        `INSERT OR IGNORE INTO workspace_admissions(workspace,created_at,admission_mode)
         SELECT workspace,created_at,'public' FROM principals WHERE subject=?`,
      ).bind(subject),
    ]);
  }

  const principal = await env.IDENTITY.prepare(
    "SELECT workspace FROM principals WHERE subject=?",
  )
    .bind(subject)
    .first<{ workspace: string }>();
  if (principal) return principal;

  if (env.SIGNUP_MODE === "public") {
    const workspaceLimit = publicAdmissionLimit(
      env.PUBLIC_WORKSPACE_LIMIT,
      "PUBLIC_WORKSPACE_LIMIT",
      10_000,
    );
    const hourlyLimit = publicAdmissionLimit(
      env.PUBLIC_SIGNUPS_PER_HOUR,
      "PUBLIC_SIGNUPS_PER_HOUR",
      1_000,
    );
    const counts = await env.IDENTITY.prepare(
      `SELECT
         (SELECT count(*) FROM workspace_admissions) AS total,
         (SELECT count(*) FROM workspace_admissions WHERE created_at>=?) AS recent`,
    )
      .bind(now - 60 * 60_000)
      .first<{ total: number; recent: number }>();
    requireValue(
      Number(counts?.total || 0) < workspaceLimit,
      "PUBLIC_WORKSPACE_LIMIT_REACHED",
      "New workspace admission is temporarily closed.",
      503,
    );
    requireValue(
      Number(counts?.recent || 0) < hourlyLimit,
      "PUBLIC_SIGNUP_RATE_LIMIT",
      "New workspace admission is temporarily rate limited.",
      429,
    );
  }
  throw new Fault(
    "WORKSPACE_CREATION_FAILED",
    "Unable to create workspace.",
    500,
  );
}

export async function login(request: Request, env: Env): Promise<Response> {
  const returnPath = new URL(request.url).searchParams.get("return") || "/app";
  requireValue(["/app", "/pilot"].includes(returnPath), "RETURN_NOT_ALLOWED", "Choose a documented sign-in destination.");
  const { as, client } = await oidc(env),
    state = oauth.generateRandomState(),
    verifier = oauth.generateRandomCodeVerifier(),
    nonce = oauth.generateRandomNonce();
  requireValue(as.authorization_endpoint, "LOGIN_UNCONFIGURED", "Issuer has no authorization endpoint.", 503);
  const stateHash = await digest(state);
  // Keep the old four-column table contract intact during deploy/rollback.
  // The optional return path commits with its login state in a separate table.
  const pending = await env.IDENTITY.batch([
    env.IDENTITY.prepare(
      "INSERT INTO login_states (state_hash,verifier,nonce,expires_at) SELECT ?,?,?,? WHERE (SELECT count(*) FROM login_states) < 10000",
    ).bind(stateHash, verifier, nonce, Date.now() + 600000),
    env.IDENTITY.prepare(
      "INSERT INTO login_return_paths (state_hash,return_path) SELECT state_hash,? FROM login_states WHERE state_hash=?",
    ).bind(returnPath, stateHash),
  ]);
  requireValue(
    pending[0].meta.changes === 1,
    "LOGIN_CAPACITY",
    "Sign-in is temporarily at capacity. Try again later.",
    503,
  );
  const url = new URL(as.authorization_endpoint);
  url.search = new URLSearchParams({
    client_id: client.client_id,
    redirect_uri: env.PUBLIC_ORIGIN + "/auth/callback",
    response_type: "code",
    scope: "openid profile email",
    state,
    nonce,
    code_challenge: await oauth.calculatePKCECodeChallenge(verifier),
    code_challenge_method: "S256",
  }).toString();
  return new Response(null, {
    status: 302,
    headers: {
      Location: url.href,
      "Set-Cookie": stateCookie(state, 600),
      "Cache-Control": "no-store",
    },
  });
}
export async function callback(request: Request, env: Env): Promise<Response> {
  let stage: LoginStage = "state";
  try {
    const url = new URL(request.url),
      state = url.searchParams.get("state");
    requireValue(
      state && cookie(request, "__Host-login") === state,
      "LOGIN_STATE_INVALID",
      "Sign-in state is invalid or belongs to another browser.",
      400,
    );
    const stateHash = await digest(state);
    const destination = await env.IDENTITY.prepare("SELECT return_path FROM login_return_paths WHERE state_hash=?")
      .bind(stateHash).first<{ return_path: string }>();
    // The consuming DELETE is the one-use gate. Concurrent callbacks may read
    // the fixed return path, but only the winner may exchange the authorization code.
    const record = await env.IDENTITY.prepare(
      "DELETE FROM login_states WHERE state_hash=? AND expires_at>? RETURNING *",
    )
      .bind(stateHash, Date.now())
      .first<any>();
    requireValue(record, "LOGIN_STATE_EXPIRED", "Restart sign-in.", 400);
    stage = "discovery";
    const { as, client } = await oidc(env);
    stage = "authorization";
    const params = oauth.validateAuthResponse(as, client, url, state);
    // Google's documented form authentication avoids its observed rejection
    // of percent-encoded Basic credentials. Choose once before exchanging the
    // one-use code; never retry a real code with another authentication method.
    const google = as.issuer === "https://accounts.google.com";
    requireValue(
      !google || as.token_endpoint_auth_methods_supported?.includes("client_secret_post"),
      "LOGIN_UNCONFIGURED",
      "Google's documented client authentication method is unavailable.",
      503,
    );
    const clientAuthentication = google
      ? oauth.ClientSecretPost(env.OIDC_CLIENT_SECRET)
      : oauth.ClientSecretBasic(env.OIDC_CLIENT_SECRET);
    stage = "token_exchange";
    const response = await oauth.authorizationCodeGrantRequest(
      as,
      client,
      clientAuthentication,
      params,
      env.PUBLIC_ORIGIN + "/auth/callback",
      record.verifier,
      { signal: AbortSignal.timeout(10000) },
    );
    stage = "token_validation";
    const result = await oauth.processAuthorizationCodeResponse(
      as,
      client,
      response,
      { expectedNonce: record.nonce, requireIdToken: true },
    );
    stage = "signature";
    await oauth.validateApplicationLevelSignature(as, response, {
      signal: AbortSignal.timeout(10000),
    });
    const claims = oauth.getValidatedIdTokenClaims(result);
    requireValue(
      claims?.sub,
      "LOGIN_IDENTITY_MISSING",
      "Identity provider returned no subject.",
      400,
    );
    allowOwner(claims, env);
    stage = "principal";
    const subject = await digest({ issuer: as.issuer, subject: claims.sub });
    const principal = await resolveOwnerPrincipal(env, subject);
    await env.IDENTITY.prepare(
      "INSERT OR IGNORE INTO workspace_registry(workspace) VALUES (?)",
    )
      .bind(principal.workspace)
      .run();
    stage = "session";
    const session = token(), csrf = token(), at = Date.now();
    const sessionHash = await digest(session);
    const statements = [
      env.IDENTITY.prepare("INSERT INTO sessions VALUES (?,?,?,?,?)")
        .bind(sessionHash, principal.workspace, subject, at + 86400000, csrf),
      await ownerProofStatement(env, sessionHash, as.issuer, claims, at),
    ];
    const previousSession = cookie(request, "__Host-session");
    if (previousSession)
      statements.push(env.IDENTITY.prepare("DELETE FROM sessions WHERE token_hash=?").bind(await digest(previousSession)));
    // No usable new session is returned unless its completion proof also commits.
    await env.IDENTITY.batch(statements);
    const headers = new Headers({
      Location: destination?.return_path === "/pilot" ? "/pilot" : "/app",
      "Cache-Control": "no-store",
    });
    headers.append("Set-Cookie", sessionCookie(session, 86400));
    headers.append("Set-Cookie", stateCookie("", 0));
    return new Response(null, { status: 302, headers });
  } catch (error) {
    throw loginFailure(error, stage, env.RELEASE_SHA);
  }
}
export async function grants(
  request: Request,
  env: Env,
  auth: Awaited<ReturnType<typeof authenticate>>,
) {
  requireValue(
    auth.browser && auth.actor.scopes.includes("admin"),
    "OWNER_SESSION_REQUIRED",
    "Manage agent grants from the signed-in owner session.",
    403,
  );
  if (request.method === "GET") {
    const rows = await env.IDENTITY.prepare(
      "SELECT token_hash AS id,actor,scopes,expires_at,revoked_at FROM grants WHERE workspace=?",
    )
      .bind(auth.actor.workspace)
      .all();
    return json(rows.results);
  }
  const input = (await request.json()) as any;
  if (request.method === "DELETE") {
    requireValue(
      typeof input.id === "string",
      "INVALID_GRANT",
      "Supply the grant ID.",
    );
    await env.IDENTITY.prepare(
      "UPDATE grants SET revoked_at=? WHERE token_hash=? AND workspace=?",
    )
      .bind(Date.now(), input.id, auth.actor.workspace)
      .run();
    return json({ revoked: true });
  }
  requireValue(
    Array.isArray(input.scopes) &&
      input.scopes.length > 0 &&
      input.scopes.every((s: Scope) => scopes.includes(s) && s !== "admin"),
    "INVALID_SCOPES",
    "Choose explicit agent scopes. Admin cannot be delegated through this endpoint.",
  );
  const hours = Number(input.hours || 168);
  requireValue(
    Number.isInteger(hours) && hours >= 1 && hours <= 720,
    "INVALID_EXPIRY",
    "Grant lifetime must be 1–720 hours.",
  );
  const secret = token(),
    hash = await digest(secret),
    actor = uid(),
    expires = Date.now() + hours * 3600000;
  const inserted = await env.IDENTITY.prepare(
    "INSERT INTO grants SELECT ?,?,?,?,?,NULL WHERE (SELECT count(*) FROM grants WHERE workspace=? AND revoked_at IS NULL AND expires_at>?) < 50",
  )
    .bind(
      hash,
      auth.actor.workspace,
      actor,
      JSON.stringify([...new Set(input.scopes)]),
      expires,
      auth.actor.workspace,
      Date.now(),
    )
    .run();
  requireValue(
    inserted.meta.changes === 1,
    "GRANT_LIMIT",
    "Revoke an existing grant before creating another. Maximum 50 active grants.",
    409,
  );
  return json(
    {
      id: hash,
      token: secret,
      actor,
      scopes: input.scopes,
      expiresAt: expires,
      notice: "Shown once. Store in your agent secret manager.",
    },
    201,
  );
}
export async function logout(request: Request, env: Env) {
  const session = cookie(request, "__Host-session");
  if (session)
    await env.IDENTITY.prepare("DELETE FROM sessions WHERE token_hash=?")
      .bind(await digest(session))
      .run();
  return json({ signedOut: true }, 200, { "Set-Cookie": sessionCookie("", 0) });
}
