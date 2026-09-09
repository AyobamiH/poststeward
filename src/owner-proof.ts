import { digest, requireValue, uid } from "./common.ts";
import type { Actor, Env } from "./types.ts";

export interface OwnerProof {
  id: string;
  issuer: string;
  subject: string;
  workspace: string;
  authenticatedAt: number;
  expiresAt: number;
  release: string;
}
export interface OwnerAuthority {
  proof: OwnerProof;
  sessionHash: string;
}

// Called only by the validated OIDC callback, in the session's D1 batch.
export async function ownerProofStatement(
  env: Env, sessionHash: string, issuer: string,
  claims: Record<string, unknown>, authenticatedAt: number,
) {
  const email = typeof claims.email === "string" ? claims.email.trim().toLowerCase() : "";
  return env.IDENTITY.prepare(
    "INSERT INTO owner_proofs (session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (?,?,?,?,?,?,?,?)",
  ).bind(sessionHash, uid(), issuer, env.OIDC_CLIENT_ID, await digest(email),
    claims.email_verified === true ? 1 : 0, authenticatedAt, env.RELEASE_SHA);
}

export async function activeOwnerSession(actor: Actor, env: Env): Promise<OwnerAuthority | undefined> {
  if (!actor.ownerSession || actor.grant || env.SIGNUP_MODE !== "restricted" || env.OIDC_ISSUER !== "https://accounts.google.com") return;
  const row = await env.IDENTITY.prepare(
    "SELECT p.id,p.issuer,p.client_id,p.email_hash,p.email_verified,p.authenticated_at,p.release,s.expires_at FROM owner_proofs p JOIN sessions s ON s.token_hash=p.session_hash JOIN principals a ON a.subject=s.actor AND a.workspace=s.workspace WHERE s.token_hash=? AND s.actor=? AND s.workspace=? AND s.expires_at>?",
  ).bind(actor.ownerSession, actor.id, actor.workspace, Date.now()).first<{
    id: string; issuer: string; client_id: string; email_hash: string;
    email_verified: number; authenticated_at: number; release: string; expires_at: number;
  }>();
  if (!row || row.email_verified !== 1 || row.issuer !== env.OIDC_ISSUER || row.client_id !== env.OIDC_CLIENT_ID) return;
  const hashes = await Promise.all((env.ALLOWED_OWNER_EMAILS || "").split(",").map((email) => email.trim().toLowerCase()).filter(Boolean).map(digest));
  if (!hashes.includes(row.email_hash)) return;
  return {
    sessionHash: actor.ownerSession,
    proof: { id: row.id, issuer: row.issuer, subject: actor.id, workspace: actor.workspace,
      authenticatedAt: row.authenticated_at, expiresAt: row.expires_at, release: row.release },
  };
}

export async function ownerAuthority(
  request: Request, env: Env,
  auth: { actor: Actor; browser: boolean },
): Promise<OwnerAuthority> {
  requireValue(auth.browser && !auth.actor.grant && auth.actor.scopes.includes("admin"),
    "OWNER_SESSION_REQUIRED", "Use the signed-in owner browser, not an agent token.", 403);
  const session = request.headers.get("cookie")?.split(";").map((part) => part.trim())
    .find((part) => part.startsWith("__Host-session="))?.slice("__Host-session=".length);
  requireValue(session && session.length <= 512, "OWNER_SIGNIN_REQUIRED", "Complete Google sign-in to obtain an owner proof.", 409);
  const authority = await activeOwnerSession({ ...auth.actor, ownerSession: await digest(session) }, env);
  requireValue(authority, "OWNER_SIGNIN_REQUIRED", "Complete Google sign-in with an invited verified identity. Older sessions need a new sign-in.", 409);
  return authority;
}

export function demandFreshOwner(authority: OwnerAuthority, now: number) {
  requireValue(authority.proof.authenticatedAt <= now && now - authority.proof.authenticatedAt <= 15 * 60000 && authority.proof.expiresAt > now,
    "FRESH_SIGNIN_REQUIRED", "Sign in again before approving this publication; the sign-in proof must be under fifteen minutes old.", 409);
}
