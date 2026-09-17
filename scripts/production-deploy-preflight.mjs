import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import {
  buildConfiguration,
  deploymentSecrets,
  resolveCloudflareConfiguration,
} from "./deployment-config.mjs";
import { operationalAlertSecrets } from "./operational-alert-config.mjs";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

async function cloudflareJson(url, token, send = fetch) {
  const response = await send(url, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
    redirect: "error",
    signal: AbortSignal.timeout(15_000),
  });
  const body = await response.json().catch(() => ({}));
  demand(
    response.ok && body?.success !== false,
    `Cloudflare production preflight failed with HTTP ${response.status}.`,
  );
  return body;
}

export async function preflightProductionDeploy(
  env,
  send = fetch,
  base = JSON.parse(readFileSync("wrangler.jsonc", "utf8")),
) {
  demand(env.DEPLOY_ENV === "production", "Production preflight requires DEPLOY_ENV=production.");
  demand(
    env.GITHUB_REF === "refs/heads/main" &&
      env.GITHUB_REPOSITORY === "AyobamiH/poststeward" &&
      env.GITHUB_ACTOR === "AyobamiH" &&
      /^[a-f0-9]{40}$/.test(env.GITHUB_SHA || ""),
    "Production preflight runs only for AyobamiH/poststeward main as AyobamiH.",
  );
  demand(
    env.APP_ORIGIN === "https://poststeward.com",
    "Production APP_ORIGIN must be exactly https://poststeward.com.",
  );
  demand(
    env.ENCRYPTION_ROOT_WRITE !== "next",
    "Production must not adopt the staging next-root writer implicitly.",
  );
  demand(
    env.STRIPE_SANDBOX_ENABLED !== "true",
    "Stripe sandbox must remain disabled in production.",
  );
  demand(
    env.ADVANCED_ENABLED !== "true" &&
      (env.ADVANCED_ROLLOUT_MODE || "disabled") === "disabled" &&
      String(env.ADVANCED_CANARY_BPS || "0") === "0",
    "Production deploy requires Advanced globally disabled.",
  );

  const resolved = await resolveCloudflareConfiguration(env, send);
  const configuration = buildConfiguration(base, resolved);
  demand(
    configuration.name === "poststeward" &&
      configuration.vars.PUBLIC_ORIGIN === "https://poststeward.com" &&
      configuration.vars.DEPLOY_ENV === "production" &&
      configuration.workers_dev === false,
    "Production Worker configuration did not resolve to the reviewed custom origin.",
  );

  // Validate real protected environment values without returning any secret.
  deploymentSecrets(env);
  const alertSecrets = operationalAlertSecrets(env);

  const token = env.CLOUDFLARE_API_TOKEN || "";
  const account = configuration.account_id;
  const database = configuration.d1_databases[0];
  const databaseMetadata = await cloudflareJson(
    `https://api.cloudflare.com/client/v4/accounts/${account}/d1/database/${database.database_id}`,
    token,
    send,
  );
  demand(
    databaseMetadata?.result?.uuid === database.database_id &&
      databaseMetadata?.result?.name === "poststeward-identity-production" &&
      databaseMetadata?.result?.read_replication?.mode !== "auto",
    "Production D1 identity does not match the reviewed dedicated database contract.",
  );

  const workerResponse = await send(
    `https://api.cloudflare.com/client/v4/accounts/${account}/workers/scripts/poststeward/settings`,
    {
      method: "GET",
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      redirect: "error",
      signal: AbortSignal.timeout(15_000),
    },
  );
  demand(
    workerResponse.ok || workerResponse.status === 404,
    `Cloudflare Worker settings inspection failed with HTTP ${workerResponse.status}.`,
  );

  return {
    schemaVersion: 1,
    ready: true,
    environment: "production",
    origin: "https://poststeward.com",
    release: env.GITHUB_SHA,
    workerName: "poststeward",
    workerAlreadyPresent: workerResponse.ok,
    dedicatedProductionDatabase: true,
    cloudflareAccountAuthority: true,
    requiredSecretsValidated: true,
    operationalAlertDestinationConfigured: Boolean(
      alertSecrets.OPERATIONAL_ALERT_WEBHOOK_URL,
    ),
    restrictedSignup: true,
    advancedDisabled: true,
    mppDisabled: true,
    stripeSandboxDisabled: true,
    externalEffectAttempted: false,
  };
}

async function main() {
  const report = await preflightProductionDeploy(process.env);
  const serialized = JSON.stringify(report);
  for (const secret of [
    process.env.CLOUDFLARE_API_TOKEN,
    process.env.ENCRYPTION_KEY,
    process.env.ENCRYPTION_KEY_NEXT,
    process.env.OIDC_CLIENT_SECRET,
    process.env.ALLOWED_OWNER_EMAILS,
    process.env.GITHUB_APP_CLIENT_SECRET,
    process.env.X_OAUTH_CLIENT_SECRET,
    process.env.THREADS_OAUTH_CLIENT_SECRET,
    process.env.LINKEDIN_OAUTH_CLIENT_SECRET,
    process.env.OPERATIONAL_ALERT_WEBHOOK_URL,
    process.env.OPERATIONAL_ALERT_WEBHOOK_TOKEN,
  ].filter(Boolean))
    demand(!serialized.includes(secret), "Production preflight attempted to emit protected material.");
  console.log("POSTSTEWARD_PRODUCTION_DEPLOY_PREFLIGHT " + serialized);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch((error) => {
    console.error(
      "POSTSTEWARD_PRODUCTION_DEPLOY_PREFLIGHT_FAILED " +
        JSON.stringify({ message: error instanceof Error ? error.message : "Unknown failure." }),
    );
    process.exitCode = 1;
  });
