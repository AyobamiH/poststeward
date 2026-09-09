import { readFileSync, writeFileSync } from "node:fs";
for (const key of [
  "WORKER_NAME",
  "D1_ID",
  "D1_NAME",
  "APP_ORIGIN",
  "OIDC_ISSUER",
  "OIDC_CLIENT_ID",
  "GITHUB_SHA",
])
  if (!process.env[key]) throw new Error("Missing deployment variable: " + key);
const c = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
c.name = process.env.WORKER_NAME;
c.d1_databases[0].database_id = process.env.D1_ID;
c.d1_databases[0].database_name = process.env.D1_NAME;
Object.assign(c.vars, {
  PUBLIC_ORIGIN: process.env.APP_ORIGIN,
  OIDC_ISSUER: process.env.OIDC_ISSUER,
  OIDC_CLIENT_ID: process.env.OIDC_CLIENT_ID,
  RELEASE_SHA: process.env.GITHUB_SHA,
});
writeFileSync("wrangler.jsonc", JSON.stringify(c, null, 2) + "\n");
