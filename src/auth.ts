import * as oauth from "oauth4webapi";
import { digest, Fault, json, requireValue, uid } from "./common.ts";
import { activeOwnerSession, ownerProofStatement } from "./owner-proof.ts";
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
  if (env.SIGNUP_MODE === "public") return;
  const allowed = (env.ALLOWED_OWNER_EMAILS || "")
    .split(",")
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);
  requireValue(
    env.SIGNUP_MODE === "restricted" &&
      claims.email_verified === true &&
      typeof claims.email === "string" &&
      allowed.includes(claims.email.toLowerCase()),
    "SIGNUP_RESTRICTED",
    "This deployment is limited to invited owners.",
    403,
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
  const { as, client } = await oidc(env),
    params = oauth.validateAuthResponse(as, client, url, state);
  const response = await oauth.authorizationCodeGrantRequest(
    as,
    client,
    oauth.ClientSecretBasic(env.OIDC_CLIENT_SECRET),
    params,
    env.PUBLIC_ORIGIN + "/auth/callback",
    record.verifier,
    { signal: AbortSignal.timeout(10000) },
  );
  const result = await oauth.processAuthorizationCodeResponse(
    as,
    client,
    response,
    { expectedNonce: record.nonce, requireIdToken: true },
  );
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
  const subject = await digest({ issuer: as.issuer, subject: claims.sub });
  await env.IDENTITY.prepare(
    "INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?) ON CONFLICT(subject) DO NOTHING",
  )
    .bind(subject, uid(), Date.now())
    .run();
  const principal = await env.IDENTITY.prepare(
    "SELECT workspace FROM principals WHERE subject=?",
  )
    .bind(subject)
    .first<{ workspace: string }>();
  requireValue(
    principal,
    "WORKSPACE_CREATION_FAILED",
    "Unable to create workspace.",
    500,
  );
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
