import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

const PITR_HISTORY_WARMUP_MS = 60000;
const PITR_TARGET_AGE_MS = 30000;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function digestString(value) {
  return createHash("sha256").update(JSON.stringify(String(value))).digest("hex");
}

function sqlLiteral(value) {
  return "'" + String(value).replaceAll("'", "''") + "'";
}

function config() {
  return JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
}

function d1(sql) {
  const name = config()?.d1_databases?.[0]?.database_name;
  demand(
    name === "poststeward-identity-staging",
    "PITR diagnostic is restricted to the configured staging D1 database.",
  );
  const output = execFileSync(
    process.platform === "win32" ? "npx.cmd" : "npx",
    [
      "wrangler",
      "d1",
      "execute",
      name,
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

async function request(origin, session, csrf, path, input) {
  const method = input === undefined ? "GET" : "POST";
  const response = await fetch(new URL(path, origin), {
    method,
    redirect: "manual",
    signal: AbortSignal.timeout(20000),
    headers: {
      Accept: "application/json",
      Cookie: `__Host-session=${session}`,
      ...(method === "POST"
        ? {
            "Content-Type": "application/json",
            Origin: origin,
            "X-CSRF-Token": csrf,
          }
        : {}),
    },
    ...(input === undefined ? {} : { body: JSON.stringify(input) }),
  });
  const body = await response.text();
  let value = {};
  try {
    value = body ? JSON.parse(body) : {};
  } catch {
    value = {};
  }
  if (!response.ok) {
    const error = new Error(
      value?.error?.message || `Request ${path} failed with ${response.status}.`,
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

function seedSyntheticOwner({ workspace, subject, sessionHash, csrf, emailHash, release }) {
  const now = Date.now();
  const expires = now + 60 * 60 * 1000;
  const issuer = process.env.OIDC_ISSUER;
  const clientId = process.env.OIDC_CLIENT_ID;
  demand(
    issuer === "https://accounts.google.com" && clientId,
    "Staging Google OIDC configuration is required.",
  );
  const proof = randomUUID();
  d1(
    [
      `INSERT INTO principals(subject,workspace,created_at) VALUES (${sqlLiteral(subject)},${sqlLiteral(workspace)},${now});`,
      `INSERT OR IGNORE INTO workspace_registry(workspace) VALUES (${sqlLiteral(workspace)});`,
      `INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (${sqlLiteral(sessionHash)},${sqlLiteral(workspace)},${sqlLiteral(subject)},${expires},${sqlLiteral(csrf)});`,
      `INSERT INTO owner_proofs(session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (${sqlLiteral(sessionHash)},${sqlLiteral(proof)},${sqlLiteral(issuer)},${sqlLiteral(clientId)},${sqlLiteral(emailHash)},1,${now},${sqlLiteral(release)});`,
    ].join("\n"),
  );
}

async function eraseSyntheticWorkspace(origin, session, csrf, workspace) {
  await request(origin, session, csrf, "/api/operations/workspace_export", {});
  const erased = await request(origin, session, csrf, "/api/lifecycle/delete", {
    delete: true,
    confirmation: `DELETE ${workspace}`,
  });
  demand(
    erased?.deleted === true &&
      erased?.durableObjectCleared === true &&
      erased?.identityStateCleared === true &&
      erased?.tombstoneRetained === true,
    "Synthetic PITR diagnostic workspace did not erase completely.",
  );
  return erased;
}

let disposableWorkspace;

async function main() {
  demand(process.env.DEPLOY_ENV === "staging", "PITR diagnostic is staging-only.");
  demand(
    process.env.GITHUB_REF === "refs/heads/main" &&
      process.env.GITHUB_REPOSITORY === "AyobamiH/poststeward" &&
      process.env.GITHUB_ACTOR === "AyobamiH",
    "Run PITR diagnostic only from AyobamiH/poststeward main as AyobamiH.",
  );
  demand(process.env.CLOUDFLARE_API_TOKEN, "Protected Cloudflare authority is required.");

  const configured = config();
  const origin = configured?.vars?.PUBLIC_ORIGIN;
  const release = configured?.vars?.RELEASE_SHA;
  demand(
    /^https:\/\/[A-Za-z0-9.-]+$/.test(origin || "") &&
      origin === "https://poststeward-staging.woeinvests.workers.dev",
    "Use the exact reviewed staging origin.",
  );
  demand(release === process.env.GITHUB_SHA, "Configured release must equal reviewed main.");
  const health = await fetch(new URL("/health", origin), {
    redirect: "manual",
    signal: AbortSignal.timeout(15000),
  }).then((response) => response.json());
  demand(health?.release === release, "Hosted staging release is not the reviewed main revision.");

  const workspace = randomUUID();
  disposableWorkspace = workspace;
  const fingerprint = digestString(workspace).slice(0, 16);
  const subject = digestString(`poststeward-pitr-diagnostic:${randomUUID()}`);
  const session = `${randomUUID()}.${randomUUID()}`;
  const sessionHash = digestString(session);
  const csrf = `${randomUUID()}.${randomUUID()}`;
  const emailHash = digestString(firstAllowedEmail());

  seedSyntheticOwner({
    workspace,
    subject,
    sessionHash,
    csrf,
    emailHash,
    release,
  });

  const initial = await request(origin, session, csrf, "/api/operations/workspace_status", {});
  demand(initial?.publishingPaused === false, "Synthetic diagnostic workspace did not initialise.");

  const initializedAt = Date.now();
  await sleep(PITR_HISTORY_WARMUP_MS);
  const targetTime = Date.now() - PITR_TARGET_AGE_MS;
  demand(targetTime > initializedAt, "Synthetic PITR history is not old enough to diagnose safely.");

  const reason = "Staging PITR primitive diagnostic";
  let result;
  try {
    const prepared = await request(origin, session, csrf, "/api/recovery/prepare", {
      at: new Date(targetTime).toISOString(),
      reason,
    });
    demand(
      prepared?.plan?.id && prepared?.plan?.digest && prepared?.plan?.state === "prepared",
      "Prepare returned without an immutable prepared plan.",
    );
    await request(origin, session, csrf, "/api/recovery/cancel", {
      id: prepared.plan.id,
      digest: prepared.plan.digest,
      cancel: true,
    });
    result = { outcome: "prepared", code: null };
  } catch (error) {
    const allowed = new Set([
      "RECOVERY_PITR_SYNC_FAILED",
      "RECOVERY_PITR_CURRENT_BOOKMARK_FAILED",
      "RECOVERY_PITR_TARGET_BOOKMARK_FAILED",
      "RECOVERY_PITR_UNAVAILABLE",
    ]);
    demand(
      error?.status >= 500 && allowed.has(error?.code),
      `Unexpected PITR diagnostic failure code ${error?.code || "unknown"}.`,
    );
    const status = await request(origin, session, csrf, "/api/recovery/status");
    demand(
      !status?.plan && status?.control?.quarantined === false,
      "Failed PITR diagnostic left durable recovery state; stop before cleanup.",
    );
    result = { outcome: "platform_failure", code: error.code };
  }

  await eraseSyntheticWorkspace(origin, session, csrf, workspace);
  disposableWorkspace = undefined;
  console.log(
    "POSTSTEWARD_PITR_PRIMITIVE_DIAGNOSTIC " +
      JSON.stringify({
        release,
        observedAt: new Date().toISOString(),
        workspaceFingerprint: fingerprint,
        ...result,
        cleanupVerified: true,
        restoreExecuted: false,
        providerEffectAttempted: false,
        paymentAttempted: false,
      }),
  );
}

try {
  await main();
} catch (error) {
  if (disposableWorkspace)
    console.error(`POSTSTEWARD_PITR_DIAGNOSTIC_FAILED workspace=${disposableWorkspace}`);
  throw error;
}
