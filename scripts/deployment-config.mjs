export const secretNames = [
  "ENCRYPTION_KEY",
  "OIDC_CLIENT_SECRET",
  "ALLOWED_OWNER_EMAILS",
];
export function demand(condition, message) {
  if (!condition) throw new Error(message);
}
export function httpsUrl(value, originOnly = false) {
  let u;
  try {
    u = new URL(value);
  } catch {
    throw new Error("Configure a valid HTTPS URL.");
  }
  demand(
    u.protocol === "https:" &&
      !u.username &&
      !u.password &&
      !u.search &&
      !u.hash &&
      !u.port &&
      u.hostname.includes(".") &&
      !u.hostname.endsWith(".invalid") &&
      u.hostname !== "localhost" &&
      !/^[\d.]+$/.test(u.hostname) &&
      !u.hostname.includes(":"),
    "Use a public HTTPS hostname without credentials, ports, query or fragment.",
  );
  demand(
    !originOnly || value === u.origin,
    "APP_ORIGIN must be an exact origin without a trailing slash or path.",
  );
  return u;
}
export function buildConfiguration(base, env) {
  demand(
    ["staging", "production"].includes(env.DEPLOY_ENV),
    "DEPLOY_ENV must be staging or production.",
  );
  demand(
    /^[a-f0-9]{32}$/.test(env.CLOUDFLARE_ACCOUNT_ID || ""),
    "Configure CLOUDFLARE_ACCOUNT_ID.",
  );
  demand(
    /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(env.D1_ID || "") &&
      !env.D1_ID.startsWith("00000000"),
    "Configure the dedicated environment D1_ID.",
  );
  demand(
    /^[a-f0-9]{40}$/.test(env.GITHUB_SHA || ""),
    "GITHUB_SHA must identify the reviewed commit.",
  );
  demand(
    /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(env.WORKERS_SUBDOMAIN || ""),
    "Configure WORKERS_SUBDOMAIN from the Cloudflare Workers dashboard.",
  );
  httpsUrl(env.OIDC_ISSUER);
  demand(
    typeof env.OIDC_CLIENT_ID === "string" && env.OIDC_CLIENT_ID.length > 0,
    "Configure OIDC_CLIENT_ID.",
  );
  const c = structuredClone(base);
  c.name = env.DEPLOY_ENV === "staging" ? "poststeward-staging" : "poststeward";
  c.account_id = env.CLOUDFLARE_ACCOUNT_ID;
  c.d1_databases[0].database_id = env.D1_ID;
  c.d1_databases[0].database_name = `poststeward-identity-${env.DEPLOY_ENV}`;
  const workerOrigin = `https://${c.name}.${env.WORKERS_SUBDOMAIN}.workers.dev`;
  const origin = env.APP_ORIGIN || workerOrigin;
  const url = httpsUrl(origin, true);
  demand(
    !url.hostname.endsWith(".workers.dev") || origin === workerOrigin,
    "Workers origin must match this environment's Worker and account subdomain.",
  );
  c.workers_dev = origin === workerOrigin;
  c.preview_urls = false;
  c.routes = c.workers_dev
    ? []
    : [{ pattern: url.hostname, custom_domain: true }];
  c.ratelimits.forEach((r, i) => {
    r.namespace_id = String((env.DEPLOY_ENV === "staging" ? 51001 : 52001) + i);
  });
  Object.assign(c.vars, {
    PUBLIC_ORIGIN: origin,
    RELEASE_SHA: env.GITHUB_SHA,
    OIDC_ISSUER: env.OIDC_ISSUER,
    OIDC_CLIENT_ID: env.OIDC_CLIENT_ID,
    DEPLOY_ENV: env.DEPLOY_ENV,
    SIGNUP_MODE: "restricted",
    ADVANCED_ENABLED: "false",
    MPP_ENABLED: "false",
  });
  validateConfiguration(c);
  return c;
}
export function validateConfiguration(c) {
  httpsUrl(c.vars?.PUBLIC_ORIGIN, true);
  httpsUrl(c.vars?.OIDC_ISSUER);
  demand(
    c.vars?.OIDC_CLIENT_ID && /^[a-f0-9]{40}$/.test(c.vars.RELEASE_SHA || ""),
    "Configure owner OIDC and a full release SHA.",
  );
  const environment = c.vars.DEPLOY_ENV;
  demand(
    ["staging", "production"].includes(environment),
    "An explicit deployment environment is required.",
  );
  demand(
    c.name ===
      (environment === "staging" ? "poststeward-staging" : "poststeward"),
    "Worker name must match the environment.",
  );
  demand(
    /^[a-f0-9]{32}$/.test(c.account_id || ""),
    "A concrete Cloudflare account is required.",
  );
  const db = c.d1_databases?.[0];
  demand(
    c.d1_databases?.length === 1 &&
      db.binding === "IDENTITY" &&
      db.database_name === `poststeward-identity-${environment}` &&
      /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(
        db.database_id || "",
      ) &&
      !db.database_id.startsWith("00000000"),
    "Use a dedicated, correctly named D1 database.",
  );
  demand(
    c.preview_urls === false &&
      c.limits?.cpu_ms <= 10000 &&
      c.limits.cpu_ms > 0,
    "Disable preview URLs and bound CPU time.",
  );
  demand(
    c.secrets?.required?.length === secretNames.length &&
      secretNames.every((name) => c.secrets.required.includes(name)),
    "Declare every required Worker secret.",
  );
  demand(
    c.observability?.logs?.invocation_logs === false &&
      c.observability?.traces?.enabled === false,
    "Automatic request logs and traces must not capture authentication URLs.",
  );
  demand(
    ["EDGE_LIMITER", "LOGIN_LIMITER"].every((name) =>
      c.ratelimits?.some(
        (r) =>
          r.name === name &&
          r.simple.period === 60 &&
          r.simple.limit > 0 &&
          r.simple.limit <= (name === "LOGIN_LIMITER" ? 10 : 120),
      ),
    ),
    "Request rate bindings are required.",
  );
  demand(
    c.vars.SIGNUP_MODE === "restricted",
    "Initial deployment requires restricted owner access.",
  );
  demand(
    c.vars.ADVANCED_ENABLED === "false" && c.vars.MPP_ENABLED === "false",
    "Purchases remain disabled pending product and payment acceptance.",
  );
}
export function deploymentSecrets(env) {
  const values = Object.fromEntries(
    secretNames.map((name) => [name, env[name]]),
  );
  const key = values.ENCRYPTION_KEY;
  demand(
    typeof key === "string" &&
      /^[A-Za-z0-9+/]{43}=$/.test(key) &&
      Buffer.from(key, "base64").length === 32 &&
      Buffer.from(key, "base64").toString("base64") === key,
    "ENCRYPTION_KEY must be a base64-encoded random 32-byte key, generated once and backed up.",
  );
  demand(
    typeof values.OIDC_CLIENT_SECRET === "string" &&
      values.OIDC_CLIENT_SECRET.trim().length >= 8,
    "Set OIDC_CLIENT_SECRET in this GitHub environment.",
  );
  demand(
    typeof values.ALLOWED_OWNER_EMAILS === "string" &&
      values.ALLOWED_OWNER_EMAILS.split(",").every((v) =>
        /^[^\s@,]+@[^\s@,]+\.[^\s@,]+$/.test(v.trim()),
      ),
    "Set ALLOWED_OWNER_EMAILS to verified, invited owner addresses.",
  );
  return values;
}
