import { appendFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { httpsUrl } from "./deployment-config.mjs";

const accountPattern = /^[a-f0-9]{32}$/;
const databasePattern = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/;
const subdomainPattern = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
class InspectionError extends Error {}
export async function inspectStaging(env, send = fetch) {
  const report = {
    environment: "staging",
    configured: {},
    validSyntax: {},
    credentialLocations: {},
    apiChecks: [],
    discovered: {},
    issues: [],
    deployed: false,
  };
  for (const name of [
    "CLOUDFLARE_ACCOUNT_ID",
    "D1_ID",
    "WORKERS_SUBDOMAIN",
    "OIDC_ISSUER",
    "OIDC_CLIENT_ID",
    "APP_ORIGIN",
  ])
    report.configured[name] = Boolean(env[name]);
  for (const name of [
    "ENCRYPTION_KEY",
    "OIDC_CLIENT_SECRET",
    "ALLOWED_OWNER_EMAILS",
  ])
    report.configured[name] = env[`HAS_${name}`] === "true";
  report.configured.CLOUDFLARE_API_TOKEN = Boolean(env.CLOUDFLARE_API_TOKEN);
  report.credentialLocations.tokenVariable = env.HAS_TOKEN_VARIABLE === "true";
  report.credentialLocations.cfApiTokenSecret =
    env.HAS_TOKEN_ALIAS_SECRET === "true";
  report.validSyntax.CLOUDFLARE_ACCOUNT_ID = accountPattern.test(
    env.CLOUDFLARE_ACCOUNT_ID || "",
  );
  report.validSyntax.D1_ID =
    databasePattern.test(env.D1_ID || "") && !env.D1_ID.startsWith("00000000");
  report.validSyntax.WORKERS_SUBDOMAIN = subdomainPattern.test(
    env.WORKERS_SUBDOMAIN || "",
  );
  for (const name of Object.keys(report.validSyntax))
    if (report.configured[name] && !report.validSyntax[name])
      report.issues.push(`${name} is present but its format is invalid.`);
  try {
    const origin =
      env.APP_ORIGIN ||
      (report.validSyntax.WORKERS_SUBDOMAIN
        ? `https://poststeward-staging.${env.WORKERS_SUBDOMAIN}.workers.dev`
        : "");
    if (origin) {
      httpsUrl(origin, true);
      report.configuredOrigin = origin;
      report.oidcRedirectUri = `${origin}/auth/callback`;
    }
  } catch {
    report.issues.push("APP_ORIGIN is invalid.");
  }
  if (!env.CLOUDFLARE_API_TOKEN) {
    report.issues.push(
      "CLOUDFLARE_API_TOKEN is not available to the staging environment.",
    );
    if (report.credentialLocations.tokenVariable)
      report.issues.push(
        "The token was saved as a variable; move it to the staging CLOUDFLARE_API_TOKEN secret.",
      );
    if (report.credentialLocations.cfApiTokenSecret)
      report.issues.push(
        "A CF_API_TOKEN secret exists; the workflow expects CLOUDFLARE_API_TOKEN.",
      );
  }
  async function api(path, missingAllowed = false) {
    const operation = path.includes("/d1/database/")
      ? "D1 database details"
      : path.includes("/d1/database?")
        ? "D1 database list"
        : path.endsWith("/secrets")
          ? "Worker secret names"
          : path.endsWith("/workers/subdomain")
            ? "Workers subdomain"
            : "Account discovery";
    const response = await send(`https://api.cloudflare.com/client/v4${path}`, {
      method: "GET",
      headers: { Authorization: `Bearer ${env.CLOUDFLARE_API_TOKEN}` },
      redirect: "error",
      signal: AbortSignal.timeout(15000),
    });
    const check = { operation, status: response.status };
    report.apiChecks.push(check);
    if (missingAllowed && response.status === 404) return null;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      check.errorCodes = Array.isArray(data.errors)
        ? data.errors
            .map((e) => e.code)
            .filter(Number.isSafeInteger)
            .slice(0, 5)
        : [];
      throw new InspectionError(
        `Cloudflare ${operation} returned HTTP ${response.status}.`,
      );
    }
    if (!data.success)
      throw new InspectionError("Cloudflare inspection did not succeed.");
    return data.result;
  }
  if (env.CLOUDFLARE_API_TOKEN)
    try {
      let account = env.CLOUDFLARE_ACCOUNT_ID;
      if (!accountPattern.test(account || "")) {
        const accounts = await api("/accounts?per_page=50");
        if (
          !Array.isArray(accounts) ||
          accounts.length !== 1 ||
          !accountPattern.test(accounts[0].id)
        )
          throw new InspectionError(
            "Set CLOUDFLARE_ACCOUNT_ID: the token did not resolve exactly one account.",
          );
        account = accounts[0].id;
      }
      report.discovered.CLOUDFLARE_ACCOUNT_ID = account;
      const subdomain = await api(`/accounts/${account}/workers/subdomain`);
      if (!subdomainPattern.test(subdomain?.subdomain || ""))
        throw new InspectionError(
          "Cloudflare has no usable Workers subdomain configured.",
        );
      report.discovered.WORKERS_SUBDOMAIN = subdomain.subdomain;
      report.discovered.DEFAULT_ORIGIN = `https://poststeward-staging.${subdomain.subdomain}.workers.dev`;
      if (!env.APP_ORIGIN)
        report.oidcRedirectUri = `${report.discovered.DEFAULT_ORIGIN}/auth/callback`;
      if (
        env.WORKERS_SUBDOMAIN &&
        env.WORKERS_SUBDOMAIN !== subdomain.subdomain
      )
        report.issues.push(
          "Saved WORKERS_SUBDOMAIN does not match Cloudflare.",
        );
      const expectedName = "poststeward-identity-staging";
      let database;
      if (databasePattern.test(env.D1_ID || "")) {
        try {
          database = await api(`/accounts/${account}/d1/database/${env.D1_ID}`);
        } catch (error) {
          if (!(error instanceof InspectionError)) throw error;
          report.issues.push(error.message);
        }
      }
      if (!database) {
        for (let page = 1; page <= 20; page++) {
          const databases = await api(
            `/accounts/${account}/d1/database?per_page=100&page=${page}`,
          );
          if (!Array.isArray(databases))
            throw new InspectionError(
              "Cloudflare database listing was invalid.",
            );
          const matches = databases.filter((d) => d.name === expectedName);
          if (matches.length > 1)
            throw new InspectionError("Staging database name is ambiguous.");
          if (matches.length === 1) {
            database = matches[0];
            break;
          }
          if (databases.length < 100) break;
        }
      }
      if (
        !database ||
        database.name !== expectedName ||
        !databasePattern.test(database.uuid || "")
      )
        throw new InspectionError(
          "The staging database was not found with its expected name and UUID.",
        );
      report.discovered.D1_ID = database.uuid;
      report.discovered.D1_NAME = expectedName;
      const names = await api(
        `/accounts/${account}/workers/scripts/poststeward-staging/secrets`,
        true,
      );
      report.discovered.workerExists = names !== null;
      if (Array.isArray(names))
        report.discovered.workerSecretNames = names
          .map((x) => x.name)
          .filter((n) =>
            [
              "ENCRYPTION_KEY",
              "OIDC_CLIENT_SECRET",
              "ALLOWED_OWNER_EMAILS",
            ].includes(n),
          );
    } catch (error) {
      // Never forward API bodies, request headers or arbitrary network exceptions.
      report.issues.push(
        error instanceof InspectionError
          ? error.message
          : "Cloudflare inspection could not complete.",
      );
    }
  if (!env.OIDC_ISSUER || !env.OIDC_CLIENT_ID)
    report.issues.push("Owner OIDC issuer/client configuration is incomplete.");
  for (const name of [
    "OIDC_CLIENT_SECRET",
    "ALLOWED_OWNER_EMAILS",
    "ENCRYPTION_KEY",
  ])
    if (
      !report.configured[name] &&
      !report.discovered.workerSecretNames?.includes(name)
    )
      report.issues.push(`${name} has not been configured.`);
  return report;
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const report = await inspectStaging(process.env);
  console.log("POSTSTEWARD_SETUP_REPORT " + JSON.stringify(report));
  if (process.env.GITHUB_STEP_SUMMARY)
    appendFileSync(
      process.env.GITHUB_STEP_SUMMARY,
      "Staging configuration inspection (no deployment performed):\n\n```json\n" +
        JSON.stringify(report, null, 2) +
        "\n```\n",
    );
}
