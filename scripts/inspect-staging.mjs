import { appendFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const accountPattern = /^[a-f0-9]{32}$/;
const databasePattern = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/;
const subdomainPattern = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
export async function inspectStaging(env, send = fetch) {
  const report = {
    environment: "staging",
    configured: {},
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
  if (!env.CLOUDFLARE_API_TOKEN) {
    report.issues.push(
      "CLOUDFLARE_API_TOKEN is not available to the staging environment.",
    );
    return report;
  }
  async function api(path, missingAllowed = false) {
    const response = await send(`https://api.cloudflare.com/client/v4${path}`, {
      method: "GET",
      headers: { Authorization: `Bearer ${env.CLOUDFLARE_API_TOKEN}` },
      redirect: "error",
      signal: AbortSignal.timeout(15000),
    });
    if (missingAllowed && response.status === 404) return null;
    if (!response.ok)
      throw new Error(
        `Cloudflare inspection returned HTTP ${response.status}.`,
      );
    const data = await response.json();
    if (!data.success)
      throw new Error("Cloudflare inspection did not succeed.");
    return data.result;
  }
  try {
    let account = env.CLOUDFLARE_ACCOUNT_ID;
    if (!accountPattern.test(account || "")) {
      const accounts = await api("/accounts?per_page=50");
      if (
        !Array.isArray(accounts) ||
        accounts.length !== 1 ||
        !accountPattern.test(accounts[0].id)
      )
        throw new Error(
          "Set CLOUDFLARE_ACCOUNT_ID: the token did not resolve exactly one account.",
        );
      account = accounts[0].id;
    }
    report.discovered.CLOUDFLARE_ACCOUNT_ID = account;
    const subdomain = await api(`/accounts/${account}/workers/subdomain`);
    if (!subdomainPattern.test(subdomain?.subdomain || ""))
      throw new Error("Cloudflare has no usable Workers subdomain configured.");
    report.discovered.WORKERS_SUBDOMAIN = subdomain.subdomain;
    if (env.WORKERS_SUBDOMAIN && env.WORKERS_SUBDOMAIN !== subdomain.subdomain)
      report.issues.push("Saved WORKERS_SUBDOMAIN does not match Cloudflare.");
    const expectedName = "poststeward-identity-staging";
    let database;
    if (databasePattern.test(env.D1_ID || "")) {
      database = await api(`/accounts/${account}/d1/database/${env.D1_ID}`);
    } else {
      for (let page = 1; page <= 20; page++) {
        const databases = await api(
          `/accounts/${account}/d1/database?per_page=100&page=${page}`,
        );
        if (!Array.isArray(databases))
          throw new Error("Cloudflare database listing was invalid.");
        const matches = databases.filter((d) => d.name === expectedName);
        if (matches.length > 1)
          throw new Error("Staging database name is ambiguous.");
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
      throw new Error(
        "The staging database was not found with its expected name and UUID.",
      );
    report.discovered.D1_ID = database.uuid;
    report.discovered.D1_NAME = expectedName;
    report.discovered.DEFAULT_ORIGIN = `https://poststeward-staging.${subdomain.subdomain}.workers.dev`;
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
      error instanceof Error &&
        /^(Cloudflare|Set CLOUDFLARE|Saved|The staging|Staging)/.test(
          error.message,
        )
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
