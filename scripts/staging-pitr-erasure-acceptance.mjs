import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function canonicalStringDigest(value) {
  return createHash("sha256").update(JSON.stringify(String(value))).digest("hex");
}

export function sqlLiteral(value) {
  return "'" + String(value).replaceAll("'", "''") + "'";
}

export function flattenD1Results(value) {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) =>
    Array.isArray(item?.results) ? item.results : [],
  );
}

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function d1(sql) {
  const output = execFileSync(
    process.platform === "win32" ? "npx.cmd" : "npx",
    [
      "wrangler",
      "d1",
      "execute",
      "poststeward-identity",
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

async function request(origin, session, csrf, path, input, options = {}) {
  const method = input === undefined ? "GET" : "POST";
  try {
    const response = await fetch(new URL(path, origin), {
      method,
      redirect: "manual",
      signal: AbortSignal.timeout(options.timeoutMs || 20000),
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
      if (options.allowUncertain && response.status >= 500) return undefined;
      throw error;
    }
    return value;
  } catch (error) {
    if (options.allowUncertain && !(error?.status >= 400 && error?.status < 500))
      return undefined;
    throw error;
  }
}

async function poll(fn, accept, label, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await fn();
      if (accept(last)) return last;
    } catch (error) {
      last = error;
    }
    await sleep(1000);
  }
  const detail =
    last instanceof Error
      ? `${last.code || last.name}: ${last.message}`
      : JSON.stringify(last);
  throw new Error(`${label} did not settle before timeout. Last observation: ${detail}`);
}

function firstAllowedEmail() {
  const email = (process.env.ALLOWED_OWNER_EMAILS || "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .find(Boolean);
  demand(email, "The staging ALLOWED_OWNER_EMAILS secret is required.");
  return email;
}

function configuredOrigin() {
  const config = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
  const origin = config?.vars?.PUBLIC_ORIGIN;
  demand(
    typeof origin === "string" &&
      /^https:\/\/[A-Za-z0-9.-]+$/.test(origin) &&
      !origin.includes("configure-before-deployment"),
    "Configure the reviewed staging origin before running live acceptance.",
  );
  return { origin, release: config.vars.RELEASE_SHA };
}

async function verifyRelease(origin, release) {
  const response = await fetch(new URL("/health", origin), {
    signal: AbortSignal.timeout(15000),
    redirect: "manual",
  });
  demand(response.ok, `Staging health failed with ${response.status}.`);
  const value = await response.json();
  demand(
    value.release === release && release === process.env.GITHUB_SHA,
    `Staging release ${value.release || "unknown"} does not equal reviewed main ${process.env.GITHUB_SHA}.`,
  );
  return value;
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
  const statements = [
    `INSERT INTO principals(subject,workspace,created_at) VALUES (${sqlLiteral(subject)},${sqlLiteral(workspace)},${now});`,
    `INSERT OR IGNORE INTO workspace_registry(workspace) VALUES (${sqlLiteral(workspace)});`,
    `INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (${sqlLiteral(sessionHash)},${sqlLiteral(workspace)},${sqlLiteral(subject)},${expires},${sqlLiteral(csrf)});`,
    `INSERT INTO owner_proofs(session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (${sqlLiteral(sessionHash)},${sqlLiteral(proof)},${sqlLiteral(issuer)},${sqlLiteral(clientId)},${sqlLiteral(emailHash)},1,${now},${sqlLiteral(release)});`,
  ];
  d1(statements.join("\n"));
}

function queryDeletion(workspace) {
  const rows = flattenD1Results(
    d1(
      `SELECT state,completed_at FROM workspace_deletions WHERE workspace=${sqlLiteral(workspace)};`,
    ),
  );
  return rows[0];
}

function queryRegistry(workspace) {
  const rows = flattenD1Results(
    d1(
      `SELECT count(*) AS count FROM workspace_registry WHERE workspace=${sqlLiteral(workspace)};`,
    ),
  );
  return Number(rows[0]?.count || 0);
}

let disposableWorkspace;

async function runAcceptance() {
  demand(
    process.env.DEPLOY_ENV === "staging",
    "Disposable recovery acceptance is staging-only.",
  );
  demand(
    process.env.GITHUB_REF === "refs/heads/main" &&
      process.env.GITHUB_REPOSITORY === "AyobamiH/poststeward" &&
      process.env.GITHUB_ACTOR === "AyobamiH",
    "Run this only from AyobamiH/poststeward main as AyobamiH.",
  );
  demand(
    process.env.CLOUDFLARE_API_TOKEN,
    "Protected staging Cloudflare authority is required.",
  );

  const { origin, release } = configuredOrigin();
  await verifyRelease(origin, release);

  const workspace = randomUUID();
  disposableWorkspace = workspace;
  console.log(`POSTSTEWARD_DISPOSABLE_WORKSPACE ${workspace}`);
  const subject = canonicalStringDigest(`poststeward-disposable-acceptance:${randomUUID()}`);
  const session = `${randomUUID()}.${randomUUID()}`;
  const sessionHash = canonicalStringDigest(session);
  const csrf = `${randomUUID()}.${randomUUID()}`;
  const emailHash = canonicalStringDigest(firstAllowedEmail());
  const workspaceFingerprint = canonicalStringDigest(workspace).slice(0, 16);

  seedSyntheticOwner({
    workspace,
    subject,
    sessionHash,
    csrf,
    emailHash,
    release,
  });

  const initial = await request(origin, session, csrf, "/api/operations/workspace_status", {});
  demand(initial.publishingPaused === false, "Disposable workspace did not start unpaused.");

  // The restore target is captured after the Durable Object exists but before
  // the canary state mutation. Leave wall-clock separation for Cloudflare's
  // approximate timestamp-to-bookmark mapping.
  const restoreTarget = Date.now();
  await sleep(4000);

  const canaryKey = `pitr-canary-${randomUUID()}`;
  const mutated = await request(origin, session, csrf, "/api/operations/publishing_pause", {
    paused: true,
    idempotencyKey: canaryKey,
  });
  demand(mutated, "Could not write the disposable PITR canary.");
  const afterMutation = await request(
    origin,
    session,
    csrf,
    "/api/operations/workspace_status",
    {},
  );
  demand(afterMutation.publishingPaused === true, "PITR canary was not observable.");

  await sleep(3000);

  const prepared = await request(origin, session, csrf, "/api/recovery/prepare", {
    at: new Date(restoreTarget).toISOString(),
    reason: "Disposable staging PITR acceptance",
  });
  const plan = prepared?.plan;
  demand(
    plan?.id && /^[a-f0-9]{64}$/.test(plan?.digest || "") && plan.state === "prepared",
    "Recovery prepare did not return an immutable prepared plan.",
  );
  demand(
    prepared?.status?.control?.quarantined === true,
    "Recovery prepare did not establish quarantine.",
  );

  await request(
    origin,
    session,
    csrf,
    "/api/recovery/execute",
    {
      id: plan.id,
      digest: plan.digest,
      execute: true,
      confirmation: `RESTORE ${workspace}`,
    },
    { allowUncertain: true, timeoutMs: 15000 },
  );

  const armed = await poll(
    () => request(origin, session, csrf, "/api/recovery/status"),
    (value) => value?.plan?.id === plan.id && value?.plan?.state === "armed",
    "PITR arm",
  );
  demand(armed.control?.quarantined === true, "Recovery lost quarantine after arming.");

  const reconciled = await poll(
    () =>
      request(origin, session, csrf, "/api/recovery/reconcile", {
        id: plan.id,
        digest: plan.digest,
        reconcile: true,
      }),
    (value) =>
      value?.status?.plan?.id === plan.id &&
      value?.status?.plan?.state === "reconciled",
    "PITR reconciliation",
  );
  demand(
    reconciled.status?.control?.quarantined === true,
    "Reconciliation cleared quarantine before explicit resume.",
  );

  const restored = await request(
    origin,
    session,
    csrf,
    "/api/operations/workspace_status",
    {},
  );
  demand(
    restored.publishingPaused === false,
    "Real PITR did not restore the pre-canary workspace state.",
  );

  const resumed = await request(origin, session, csrf, "/api/recovery/resume", {
    id: plan.id,
    digest: plan.digest,
    resume: true,
    confirmation: `RESUME ${workspace}`,
  });
  demand(
    resumed?.status?.control?.quarantined === false,
    "Recovery resume did not clear quarantine.",
  );

  const exported = await request(
    origin,
    session,
    csrf,
    "/api/operations/workspace_export",
    {},
  );
  demand(exported && typeof exported === "object", "Disposable workspace export failed.");

  const erased = await request(origin, session, csrf, "/api/lifecycle/delete", {
    delete: true,
    confirmation: `DELETE ${workspace}`,
  });
  demand(
    erased?.deleted === true &&
      erased?.durableObjectCleared === true &&
      erased?.identityStateCleared === true &&
      erased?.tombstoneRetained === true,
    "Disposable workspace erasure did not report complete local and identity cleanup.",
  );

  const oldSessionDenied = await (async () => {
    try {
      await request(origin, session, csrf, "/api/session");
      return false;
    } catch (error) {
      return error?.status === 401 || error?.status === 410;
    }
  })();
  demand(oldSessionDenied, "Deleted workspace session remained usable.");

  const tombstone = queryDeletion(workspace);
  demand(
    tombstone?.state === "completed" && Number(tombstone.completed_at) > 0,
    "D1 did not retain the completed deletion tombstone.",
  );
  demand(queryRegistry(workspace) === 1, "Minimal workspace registry entry was not retained.");

  const report = {
    release,
    observedAt: new Date().toISOString(),
    workspaceFingerprint,
    pitr: {
      passed: true,
      publishingPausedAfterMutation: true,
      publishingPausedAfterRestore: false,
      quarantineHeldUntilResume: true,
      realCloudflarePitr: true,
    },
    erasure: {
      passed: true,
      oldSessionDenied: true,
      completedTombstone: true,
      registryRetained: true,
    },
    boundaries: {
      syntheticWorkspaceOnly: true,
      customerWorkspaceTouched: false,
      providerCredentialsCreated: false,
      externalProviderEffectsAttempted: false,
      paymentAttempted: false,
      googleOidcRepeated: false,
      acceptedSocialEvidenceRepeated: false,
    },
  };
  console.log("POSTSTEWARD_PITR_ERASURE_ACCEPTANCE " + JSON.stringify(report));
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    await runAcceptance();
  } catch (error) {
    if (disposableWorkspace)
      console.error(`POSTSTEWARD_DISPOSABLE_ACCEPTANCE_FAILED workspace=${disposableWorkspace}`);
    throw error;
  }
}
