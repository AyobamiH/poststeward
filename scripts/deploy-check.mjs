import { readFileSync } from "node:fs";
const config = JSON.parse(
  readFileSync(process.env.DEPLOY_CONFIG || "wrangler.jsonc", "utf8"),
);
const failures = [];
if (
  !config.vars.PUBLIC_ORIGIN?.startsWith("https://") ||
  config.vars.PUBLIC_ORIGIN.includes(".invalid")
)
  failures.push("Set the real HTTPS PUBLIC_ORIGIN.");
if (
  config.d1_databases.some(
    (db) => !db.database_id || db.database_id.startsWith("00000000"),
  )
)
  failures.push("Set the actual D1 database ID.");
if (!config.vars.OIDC_ISSUER || !config.vars.OIDC_CLIENT_ID)
  failures.push("Configure owner OIDC issuer and client ID.");
if (!config.vars.RELEASE_SHA || config.vars.RELEASE_SHA === "development")
  failures.push("Set RELEASE_SHA to the reviewed commit.");
if (failures.length) {
  console.error(failures.join("\n"));
  process.exitCode = 1;
} else
  console.log(
    "Deployment configuration is concrete. Verify Worker secrets and live gates separately.",
  );
