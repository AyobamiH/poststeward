import { appendFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const ACCOUNT = /^[a-f0-9]{32}$/;
const DATABASE = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/;
export const PRODUCTION_DATABASE_NAME = "poststeward-identity-production";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

async function cf(path, token, init = {}, send = fetch) {
  const response = await send(`https://api.cloudflare.com/client/v4${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
    redirect: "error",
    signal: AbortSignal.timeout(20_000),
  });
  const body = await response.json().catch(() => ({}));
  demand(
    response.ok && body?.success !== false,
    `Cloudflare production bootstrap failed with HTTP ${response.status}.`,
  );
  return body.result;
}

export function selectAccount(accounts, configured = "") {
  if (configured) {
    demand(ACCOUNT.test(configured), "Configured Cloudflare account ID is invalid.");
    demand(
      accounts.some((account) => account?.id === configured),
      "Configured Cloudflare account is not visible to the deployment token.",
    );
    return configured;
  }
  const visible = accounts.filter((account) => ACCOUNT.test(account?.id || ""));
  demand(
    visible.length === 1,
    "Cloudflare token must resolve exactly one account when no production account ID is supplied.",
  );
  return visible[0].id;
}

export function selectDatabase(databases) {
  const matches = databases.filter(
    (database) => database?.name === PRODUCTION_DATABASE_NAME,
  );
  demand(matches.length <= 1, "Production D1 database name is ambiguous.");
  if (!matches.length) return null;
  demand(
    DATABASE.test(matches[0]?.uuid || ""),
    "Existing production D1 database has an invalid UUID.",
  );
  return matches[0];
}

async function listDatabases(account, token, send) {
  const all = [];
  for (let page = 1; page <= 20; page++) {
    const result = await cf(
      `/accounts/${account}/d1/database?per_page=100&page=${page}`,
      token,
      {},
      send,
    );
    demand(Array.isArray(result), "Cloudflare D1 inventory response is invalid.");
    all.push(...result);
    if (result.length < 100) break;
    demand(page < 20, "Cloudflare D1 inventory exceeds the bounded bootstrap window.");
  }
  return all;
}

export async function bootstrapProductionCloudflare(env, send = fetch) {
  demand(
    env.GITHUB_REPOSITORY === "AyobamiH/poststeward" &&
      env.GITHUB_REF === "refs/heads/main" &&
      env.GITHUB_ACTOR === "AyobamiH" &&
      /^[a-f0-9]{40}$/.test(env.GITHUB_SHA || ""),
    "Production bootstrap runs only from reviewed AyobamiH/poststeward main.",
  );
  demand(
    env.POSTSTEWARD_PRODUCTION_BOOTSTRAP === "CREATE_OR_REUSE_PRODUCTION_D1",
    "Production D1 bootstrap requires explicit reviewed confirmation.",
  );
  const token = env.CLOUDFLARE_API_TOKEN || "";
  demand(token.length >= 20, "Protected Cloudflare authority is required.");

  const accounts = await cf("/accounts?per_page=50", token, {}, send);
  demand(Array.isArray(accounts), "Cloudflare account inventory response is invalid.");
  const accountId = selectAccount(accounts, env.CLOUDFLARE_ACCOUNT_ID || "");

  let database = selectDatabase(await listDatabases(accountId, token, send));
  let created = false;
  if (!database) {
    database = await cf(
      `/accounts/${accountId}/d1/database`,
      token,
      {
        method: "POST",
        body: JSON.stringify({ name: PRODUCTION_DATABASE_NAME }),
      },
      send,
    );
    created = true;
  }
  demand(
    database?.name === PRODUCTION_DATABASE_NAME && DATABASE.test(database?.uuid || ""),
    "Cloudflare did not return the reviewed production D1 identity.",
  );

  const reread = await cf(
    `/accounts/${accountId}/d1/database/${database.uuid}`,
    token,
    {},
    send,
  );
  demand(
    reread?.uuid === database.uuid && reread?.name === PRODUCTION_DATABASE_NAME,
    "Independent production D1 readback did not match the created/discovered database.",
  );

  const report = {
    schemaVersion: 1,
    release: env.GITHUB_SHA,
    accountId,
    d1Id: database.uuid,
    d1Name: PRODUCTION_DATABASE_NAME,
    created,
    independentReadback: true,
    destructiveEffectAttempted: false,
    providerEffectAttempted: false,
    paymentAttempted: false,
  };
  if (env.GITHUB_OUTPUT) {
    appendFileSync(env.GITHUB_OUTPUT, `account_id=${accountId}\n`, "utf8");
    appendFileSync(env.GITHUB_OUTPUT, `d1_id=${database.uuid}\n`, "utf8");
  }
  console.log("POSTSTEWARD_PRODUCTION_CLOUDFLARE_BOOTSTRAP " + JSON.stringify(report));
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  bootstrapProductionCloudflare(process.env).catch((error) => {
    console.error(
      "POSTSTEWARD_PRODUCTION_CLOUDFLARE_BOOTSTRAP_FAILED " +
        JSON.stringify({ message: error instanceof Error ? error.message : "Unknown failure." }),
    );
    process.exitCode = 1;
  });
