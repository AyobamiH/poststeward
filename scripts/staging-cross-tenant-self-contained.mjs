import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import {
  canonicalStringDigest,
  sqlLiteral,
} from "./staging-pitr-erasure-acceptance.mjs";
import { checkCrossTenantStateIsolation } from "./hosted-acceptance.mjs";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function config() {
  return JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
}

function d1(sql) {
  const database = config()?.d1_databases?.[0]?.database_name;
  demand(
    database === "poststeward-identity-staging",
    "Cross-tenant acceptance is restricted to the staging identity database.",
  );
  const output = execFileSync(
    process.platform === "win32" ? "npx.cmd" : "npx",
    [
      "wrangler",
      "d1",
      "execute",
      database,
      "--remote",
      "--command",
      sql,
      "--json",
    ],
    {
      encoding: "utf8",
      env: process.env,
      maxBuffer: 2 * 1024 * 1024,
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  return JSON.parse(output);
}

async function request(origin, session, csrf, path, input, method) {
  const requestMethod = method || (input === undefined ? "GET" : "POST");
  const response = await fetch(new URL(path, origin), {
    method: requestMethod,
    redirect: "manual",
    signal: AbortSignal.timeout(20_000),
    headers: {
      Accept: "application/json",
      Cookie: `__Host-session=${session}`,
      ...(requestMethod === "GET"
        ? {}
        : {
            "Content-Type": "application/json",
            Origin: origin,
            "X-CSRF-Token": csrf,
          }),
    },
    ...(input === undefined ? {} : { body: JSON.stringify(input) }),
  });
  const text = await response.text();
  let value = {};
  try {
    value = text ? JSON.parse(text) : {};
  } catch {
    value = {};
  }
  if (!response.ok) {
    const error = new Error(
      value?.error?.message || `${path} returned HTTP ${response.status}.`,
    );
    error.status = response.status;
    error.code = value?.error?.code;
    throw error;
  }
  return value;
}

function firstAllowedEmail() {
  const email = (process.env.ALLOWED_OWNER_EMAILS || "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .find(Boolean);
  demand(email, "The staging ALLOWED_OWNER_EMAILS secret is required.");
  return email;
}

function stagingContext() {
  const configured = config();
  const origin = configured?.vars?.PUBLIC_ORIGIN;
  const release = configured?.vars?.RELEASE_SHA;
  demand(
    origin === "https://poststeward-staging.woeinvests.workers.dev" &&
      /^[a-f0-9]{40}$/.test(release || ""),
    "Use the exact reviewed staging origin and release.",
  );
  return { origin, release };
}

async function verifyRelease(origin, release) {
  const response = await fetch(new URL("/health", origin), {
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(15_000),
  });
  demand(response.ok, `Staging health returned HTTP ${response.status}.`);
  const health = await response.json();
  demand(
    health?.release === release && release === process.env.GITHUB_SHA,
    "Hosted staging release does not equal the reviewed workflow SHA.",
  );
}

function seedOwner({ workspace, subject, sessionHash, csrf, emailHash, release }) {
  const issuer = process.env.OIDC_ISSUER;
  const clientId = process.env.OIDC_CLIENT_ID;
  demand(
    issuer === "https://accounts.google.com" && clientId,
    "Staging Google OIDC configuration is required.",
  );
  const now = Date.now();
  const proof = randomUUID();
  d1(
    [
      `INSERT INTO principals(subject,workspace,created_at) VALUES (${sqlLiteral(subject)},${sqlLiteral(workspace)},${now});`,
      `INSERT OR IGNORE INTO workspace_registry(workspace) VALUES (${sqlLiteral(workspace)});`,
      `INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (${sqlLiteral(sessionHash)},${sqlLiteral(workspace)},${sqlLiteral(subject)},${now + 3600000},${sqlLiteral(csrf)});`,
      `INSERT INTO owner_proofs(session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (${sqlLiteral(sessionHash)},${sqlLiteral(proof)},${sqlLiteral(issuer)},${sqlLiteral(clientId)},${sqlLiteral(emailHash)},1,${now},${sqlLiteral(release)});`,
    ].join("\n"),
  );
}

async function createDisposableOwner(origin, release, label) {
  const workspace = randomUUID();
  const subject = canonicalStringDigest(
    `poststeward-cross-tenant-${label}:${randomUUID()}`,
  );
  const session = `${randomUUID()}.${randomUUID()}`;
  const csrf = `${randomUUID()}.${randomUUID()}`;
  const sessionHash = canonicalStringDigest(session);
  seedOwner({
    workspace,
    subject,
    sessionHash,
    csrf,
    emailHash: canonicalStringDigest(firstAllowedEmail()),
    release,
  });
  const status = await request(
    origin,
    session,
    csrf,
    "/api/operations/workspace_status",
    {},
  );
  demand(
    status?.workspace === workspace &&
      status?.release === release &&
      status?.publishingPaused === false,
    "Synthetic acceptance workspace did not initialise on the reviewed release.",
  );
  return { workspace, subject, session, csrf };
}

async function createGrant(origin, owner) {
  const grant = await request(origin, owner.session, owner.csrf, "/api/grants", {
    scopes: ["read", "publish"],
    hours: 1,
  });
  demand(
    /^[a-f0-9]{64}$/.test(grant?.id || "") &&
      typeof grant?.token === "string" &&
      grant.token.length >= 16,
    "Synthetic scoped grant was not created.",
  );
  return grant;
}

async function revokeGrant(origin, owner, grant) {
  if (!grant?.id) return;
  const result = await request(
    origin,
    owner.session,
    owner.csrf,
    "/api/grants",
    { id: grant.id },
    "DELETE",
  );
  demand(result?.revoked === true, "Synthetic grant revocation did not complete.");
}

async function eraseWorkspace(origin, owner) {
  await request(
    origin,
    owner.session,
    owner.csrf,
    "/api/operations/workspace_export",
    {},
  );
  const erased = await request(origin, owner.session, owner.csrf, "/api/lifecycle/delete", {
    delete: true,
    confirmation: `DELETE ${owner.workspace}`,
  });
  demand(
    erased?.deleted === true &&
      erased?.durableObjectCleared === true &&
      erased?.identityStateCleared === true &&
      erased?.tombstoneRetained === true,
    "Synthetic cross-tenant workspace erasure did not complete.",
  );
}

export async function runCrossTenantAcceptance() {
  demand(process.env.DEPLOY_ENV === "staging", "Cross-tenant acceptance is staging-only.");
  demand(
    process.env.GITHUB_REF === "refs/heads/main" &&
      process.env.GITHUB_REPOSITORY === "AyobamiH/poststeward" &&
      process.env.GITHUB_ACTOR === "AyobamiH",
    "Run cross-tenant acceptance only from AyobamiH/poststeward main as AyobamiH.",
  );
  demand(
    process.env.CLOUDFLARE_API_TOKEN,
    "Protected staging Cloudflare authority is required.",
  );

  const { origin, release } = stagingContext();
  await verifyRelease(origin, release);

  let ownerA;
  let ownerB;
  let grantA;
  let grantB;
  let result;
  let cleanupError;
  try {
    ownerA = await createDisposableOwner(origin, release, "a");
    ownerB = await createDisposableOwner(origin, release, "b");
    demand(ownerA.workspace !== ownerB.workspace, "Disposable workspaces collided.");
    grantA = await createGrant(origin, ownerA);
    grantB = await createGrant(origin, ownerB);

    result = await checkCrossTenantStateIsolation(
      origin,
      grantA.token,
      grantB.token,
      release,
    );
    demand(result?.ready === true, "Hosted cross-tenant isolation was not accepted.");
  } finally {
    for (const [owner, grant] of [
      [ownerA, grantA],
      [ownerB, grantB],
    ]) {
      if (!owner) continue;
      try {
        if (grant) await revokeGrant(origin, owner, grant);
        await eraseWorkspace(origin, owner);
      } catch (error) {
        cleanupError ||= error;
      }
    }
  }
  if (cleanupError) throw cleanupError;

  const report = {
    schemaVersion: 1,
    evidenceClass: "hosted_observation",
    environment: "staging",
    origin,
    release,
    observedAt: new Date().toISOString(),
    ...result,
    grantsCreated: 2,
    grantsRevoked: 2,
    disposableWorkspacesErased: 2,
    providerEffectAttempted: false,
    paymentAttempted: false,
    recoveryAttempted: false,
    customerWorkspaceTouched: false,
    tokenValuesEmitted: false,
    rawWorkspaceIdsEmitted: false,
    ready: true,
  };
  console.log("POSTSTEWARD_CROSS_TENANT_ACCEPTANCE " + JSON.stringify(report));
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  runCrossTenantAcceptance().catch(() => {
    console.error("POSTSTEWARD_CROSS_TENANT_ACCEPTANCE_FAILED safe_cleanup_required");
    process.exitCode = 1;
  });
