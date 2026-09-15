import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

async function cfJson(url, token, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    redirect: "error",
    signal: AbortSignal.timeout(20_000),
  });
  const value = await response.json().catch(() => ({}));
  demand(
    response.ok && value?.success !== false,
    `Cloudflare D1 canary-boundary request failed with HTTP ${response.status}.`,
  );
  return value;
}

function rows(value) {
  const batches = Array.isArray(value?.result) ? value.result : [];
  return batches.flatMap((batch) =>
    Array.isArray(batch?.results) ? batch.results : [],
  );
}

async function query(accountId, databaseId, token, sql, params = []) {
  return rows(
    await cfJson(
      `https://api.cloudflare.com/client/v4/accounts/${accountId}/d1/database/${databaseId}/query`,
      token,
      { sql, params },
    ),
  );
}

export function canaryBoundaryFromConfig(config, now = Date.now()) {
  const vars = config?.vars || {};
  const release = String(vars.RELEASE_SHA || "");
  demand(/^[a-f0-9]{40}$/.test(release), "Canary boundary requires an exact release SHA.");
  const enabled = vars.ADVANCED_ENABLED === "true";
  const mode = String(vars.ADVANCED_ROLLOUT_MODE || "disabled");
  const bps = Number(vars.ADVANCED_CANARY_BPS || 0);
  const seed = String(vars.ADVANCED_CANARY_SEED || "");
  if (!enabled) {
    demand(mode === "disabled" && bps === 0, "Disabled Advanced rollout is contradictory.");
    return { state: "stopped", release, at: now };
  }
  demand(
    mode === "canary" && Number.isInteger(bps) && bps >= 1 && bps <= 1000,
    "Only bounded staging canaries can create SLO run boundaries.",
  );
  demand(/^[A-Za-z0-9._:-]{8,128}$/.test(seed), "Canary seed is not a reviewed stable value.");
  return {
    state: "active",
    release,
    bps,
    seedHash: createHash("sha256").update(seed).digest("hex"),
    at: now,
  };
}

export async function recordCanaryBoundary(env = process.env, sendQuery = query) {
  demand(env.DEPLOY_ENV === "staging", "Canary run boundaries are staging-only.");
  const accountId = env.CLOUDFLARE_ACCOUNT_ID || "";
  const databaseId = env.D1_ID || "";
  const token = env.CLOUDFLARE_API_TOKEN || "";
  demand(/^[a-f0-9]{32}$/.test(accountId), "CLOUDFLARE_ACCOUNT_ID is required.");
  demand(/^[a-f0-9-]{36}$/.test(databaseId), "D1_ID is required.");
  demand(token.length >= 20, "CLOUDFLARE_API_TOKEN is required.");
  const boundary = canaryBoundaryFromConfig(
    JSON.parse(readFileSync("wrangler.jsonc", "utf8")),
  );

  if (boundary.state === "stopped") {
    const active = await sendQuery(
      accountId,
      databaseId,
      token,
      "SELECT id,release,canary_bps,started_at FROM advanced_canary_runs WHERE stopped_at IS NULL ORDER BY started_at DESC",
    );
    if (active.length)
      await sendQuery(
        accountId,
        databaseId,
        token,
        "UPDATE advanced_canary_runs SET stopped_at=? WHERE stopped_at IS NULL",
        [boundary.at],
      );
    return {
      state: "stopped",
      release: boundary.release,
      closedRuns: active.length,
      observedAt: boundary.at,
    };
  }

  const active = await sendQuery(
    accountId,
    databaseId,
    token,
    "SELECT id,release,canary_bps,seed_hash,started_at FROM advanced_canary_runs WHERE stopped_at IS NULL ORDER BY started_at DESC",
  );
  demand(active.length <= 1, "Multiple active Advanced canary runs require reconciliation.");
  const current = active[0];
  if (
    current &&
    current.release === boundary.release &&
    Number(current.canary_bps) === boundary.bps &&
    current.seed_hash === boundary.seedHash
  )
    return {
      state: "active",
      release: boundary.release,
      bps: boundary.bps,
      startedAt: Number(current.started_at),
      created: false,
    };

  if (current)
    await sendQuery(
      accountId,
      databaseId,
      token,
      "UPDATE advanced_canary_runs SET stopped_at=? WHERE id=? AND stopped_at IS NULL",
      [boundary.at, current.id],
    );
  const id = randomUUID();
  await sendQuery(
    accountId,
    databaseId,
    token,
    `INSERT INTO advanced_canary_runs(
      id,release,canary_bps,seed_hash,started_at,stopped_at,created_at
    ) VALUES (?,?,?,?,?,NULL,?)`,
    [id, boundary.release, boundary.bps, boundary.seedHash, boundary.at, boundary.at],
  );
  return {
    state: "active",
    release: boundary.release,
    bps: boundary.bps,
    startedAt: boundary.at,
    created: true,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  recordCanaryBoundary()
    .then((result) =>
      console.log("POSTSTEWARD_ADVANCED_CANARY_BOUNDARY " + JSON.stringify(result)),
    )
    .catch((error) => {
      console.error(
        "POSTSTEWARD_ADVANCED_CANARY_BOUNDARY_FAILED " +
          JSON.stringify({ message: error instanceof Error ? error.message : "Unknown failure." }),
      );
      process.exitCode = 1;
    });
}
