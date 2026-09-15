import { createHash, randomUUID } from "node:crypto";
import { checkCrossTenantStateIsolation } from "./hosted-acceptance.mjs";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}
function digestString(value) {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}
async function cloudflareQuery(accountId, databaseId, token, sql, params = []) {
  const response = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${accountId}/d1/database/${databaseId}/query`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ sql, params }),
      redirect: "error",
      signal: AbortSignal.timeout(20_000),
    },
  );
  const body = await response.json().catch(() => ({}));
  demand(response.ok && body?.success !== false, `D1 acceptance fixture request failed with HTTP ${response.status}.`);
  return body;
}
async function hostedRelease(origin) {
  const response = await fetch(`${origin}/readiness.json`, { cache: "no-store", redirect: "error", signal: AbortSignal.timeout(20_000) });
  demand(response.status === 200, "Hosted readiness is unavailable.");
  const body = await response.json();
  demand(body?.environment === "staging" && /^[a-f0-9]{40}$/.test(body?.release || ""), "Cross-tenant fixture requires an exact staging release.");
  demand(body?.policy?.healthy === true && Array.isArray(body?.policy?.violations) && body.policy.violations.length === 0, "Cross-tenant fixture requires a healthy staging policy.");
  return body.release;
}

export async function runEphemeralCrossTenantAcceptance(env = process.env) {
  const origin = env.POSTSTEWARD_ORIGIN || "https://poststeward-staging.woeinvests.workers.dev";
  const accountId = env.CLOUDFLARE_ACCOUNT_ID || "";
  const databaseId = env.D1_ID || "";
  const token = env.CLOUDFLARE_API_TOKEN || "";
  demand(/^https:\/\/[A-Za-z0-9.-]+$/.test(origin), "POSTSTEWARD_ORIGIN must be an exact HTTPS origin.");
  demand(/^[a-f0-9]{32}$/.test(accountId), "CLOUDFLARE_ACCOUNT_ID is required.");
  demand(/^[a-f0-9-]{36}$/.test(databaseId), "D1_ID is required.");
  demand(token.length >= 20, "Read/write staging D1 Cloudflare authority is required.");

  const release = await hostedRelease(origin);
  const workspaces = [randomUUID(), randomUUID()];
  const actors = [randomUUID(), randomUUID()];
  const secrets = [`${randomUUID()}.${randomUUID()}`, `${randomUUID()}.${randomUUID()}`];
  const hashes = secrets.map(digestString);
  const expiresAt = Date.now() + 60 * 60 * 1000;
  const scopes = JSON.stringify(["read", "publish"]);
  let inserted = false;
  try {
    await cloudflareQuery(
      accountId,
      databaseId,
      token,
      "INSERT INTO grants (token_hash,workspace,actor,scopes,expires_at,revoked_at) VALUES (?,?,?,?,?,NULL),(?,?,?,?,?,NULL)",
      [hashes[0], workspaces[0], actors[0], scopes, expiresAt, hashes[1], workspaces[1], actors[1], scopes, expiresAt],
    );
    inserted = true;
    const result = await checkCrossTenantStateIsolation(origin, secrets[0], secrets[1], release);
    demand(result?.ready === true, "Hosted cross-tenant acceptance did not become ready.");
    return {
      ...result,
      observerRelease: env.GITHUB_SHA || null,
      fixture: "ephemeral_d1_grants",
      grantLifetimeMinutes: 60,
      cleanupRequired: true,
    };
  } finally {
    if (inserted) {
      await cloudflareQuery(accountId, databaseId, token, "DELETE FROM grants WHERE token_hash IN (?,?)", hashes);
      const check = await cloudflareQuery(accountId, databaseId, token, "SELECT count(*) AS remaining FROM grants WHERE token_hash IN (?,?)", hashes);
      const rows = check?.result?.flatMap((batch) => batch?.results || []) || [];
      demand(Number(rows?.[0]?.remaining || 0) === 0, "Ephemeral grant cleanup did not verify.");
    }
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runEphemeralCrossTenantAcceptance().then((report) => {
    const serialized = JSON.stringify(report);
    for (const secret of [process.env.CLOUDFLARE_API_TOKEN].filter(Boolean)) demand(!serialized.includes(secret), "Acceptance report attempted to emit protected material.");
    console.log("POSTSTEWARD_CROSS_TENANT_EPHEMERAL_ACCEPTANCE " + serialized);
  }).catch((error) => {
    console.error("POSTSTEWARD_CROSS_TENANT_EPHEMERAL_ACCEPTANCE_FAILED " + JSON.stringify({ message: error instanceof Error ? error.message : "unknown_error" }));
    process.exitCode = 1;
  });
}
