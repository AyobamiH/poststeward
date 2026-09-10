export const secretNames = [
  "ENCRYPTION_KEY",
  "OIDC_CLIENT_SECRET",
  "ALLOWED_OWNER_EMAILS",
];
export const providerSecretPairs = [
  ["X_OAUTH_CLIENT_ID", "X_OAUTH_CLIENT_SECRET"],
  ["THREADS_OAUTH_CLIENT_ID", "THREADS_OAUTH_CLIENT_SECRET"],
  ["LINKEDIN_OAUTH_CLIENT_ID", "LINKEDIN_OAUTH_CLIENT_SECRET"],
];
export function demand(condition, message) {
  if (!condition) throw new Error(message);
}
export async function resolveCloudflareConfiguration(env, send = fetch) {
  demand(
    /^[a-f0-9]{32}$/.test(env.CLOUDFLARE_ACCOUNT_ID || "") &&
      env.CLOUDFLARE_API_TOKEN,
    "Cloudflare account and deployment token are required for configuration discovery.",
  );
  const response = await send(
    `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/subdomain`,
    {
      method: "GET",
      headers: { Authorization: `Bearer ${env.CLOUDFLARE_API_TOKEN}` },
      redirect: "error",
      signal: AbortSignal.timeout(15000),
    },
  ).catch(() => {
    throw new Error("Cloudflare subdomain discovery request failed.");
  });
  demand(
    response.ok,
    `Cloudflare subdomain discovery failed (HTTP ${response.status}).`,
  );
  const data = await response.json().catch(() => {
    throw new Error("Cloudflare subdomain discovery response was invalid.");
  });
  const subdomain = data.result?.subdomain;
  demand(
    data.success &&
      /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(subdomain || ""),
    "Cloudflare did not return a valid Workers subdomain.",
  );
  return { ...env, WORKERS_SUBDOMAIN: subdomain };
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
function providerClientId(value, name) {
  const result = value || "";
  demand(
    typeof result === "string" &&
      result.length <= 512 &&
      !/[\s\x00-\x1f]/.test(result),
    `${name} must be a bounded non-secret client identifier without whitespace.`,
  );
  return result;
}
function githubAppPublic(clientIdValue, slugValue) {
  const clientId = clientIdValue || "";
  const slug = slugValue || "";
  demand(
    (!clientId && !slug) || (clientId && slug),
    "GITHUB_APP_CLIENT_ID and GITHUB_APP_SLUG must be configured together.",
  );
  demand(
    typeof clientId === "string" &&
      clientId.length <= 200 &&
      !/[\s\x00-\x1f]/.test(clientId),
    "GITHUB_APP_CLIENT_ID must be a bounded non-secret client identifier without whitespace.",
  );
  demand(
    typeof slug === "string" &&
      (!slug || /^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$/.test(slug)),
    "GITHUB_APP_SLUG must be the exact lowercase GitHub App slug.",
  );
  return { clientId, slug };
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
  const github = githubAppPublic(
    env.GITHUB_APP_CLIENT_ID,
    env.GITHUB_APP_SLUG,
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
    STRIPE_SANDBOX_ENABLED: env.STRIPE_SANDBOX_ENABLED || "false",
    STRIPE_PRICE_ID: env.STRIPE_SANDBOX_ENABLED === "true" ? (env.STRIPE_SANDBOX_PRICE_ID || "") : "",
    ADVANCED_ENABLED: "false",
    MPP_ENABLED: "false",
    GITHUB_APP_CLIENT_ID: github.clientId,
    GITHUB_APP_SLUG: github.slug,
    X_OAUTH_CLIENT_ID: providerClientId(env.X_OAUTH_CLIENT_ID, "X_OAUTH_CLIENT_ID"),
    THREADS_OAUTH_CLIENT_ID: providerClientId(
      env.THREADS_OAUTH_CLIENT_ID,
      "THREADS_OAUTH_CLIENT_ID",
    ),
    LINKEDIN_OAUTH_CLIENT_ID: providerClientId(
      env.LINKEDIN_OAUTH_CLIENT_ID,
      "LINKEDIN_OAUTH_CLIENT_ID",
    ),
    LINKEDIN_MEMBER_READBACK:
      env.LINKEDIN_MEMBER_READBACK === "true" ? "true" : "false",
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
  demand(
    ["true", "false"].includes(c.vars.STRIPE_SANDBOX_ENABLED) &&
      (c.vars.STRIPE_SANDBOX_ENABLED !== "true" ||
        (environment === "staging" && /^price_[A-Za-z0-9_]+$/.test(c.vars.STRIPE_PRICE_ID || ""))),
    "Stripe sandbox requires restricted staging and a sandbox Price identifier.",
  );
  githubAppPublic(c.vars.GITHUB_APP_CLIENT_ID, c.vars.GITHUB_APP_SLUG);
  for (const [clientId] of providerSecretPairs)
    providerClientId(c.vars[clientId], clientId);
  demand(
    ["true", "false"].includes(c.vars.LINKEDIN_MEMBER_READBACK),
    "LINKEDIN_MEMBER_READBACK must be explicitly true or false.",
  );
  demand(
    c.vars.LINKEDIN_MEMBER_READBACK !== "true" ||
      !!c.vars.LINKEDIN_OAUTH_CLIENT_ID,
    "LinkedIn member readback cannot be enabled without a LinkedIn OAuth application.",
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
  const github = githubAppPublic(
    env.GITHUB_APP_CLIENT_ID,
    env.GITHUB_APP_SLUG,
  );
  const githubSecret = env.GITHUB_APP_CLIENT_SECRET || "";
  demand(
    (!github.clientId && !githubSecret) ||
      (github.clientId &&
        github.slug &&
        typeof githubSecret === "string" &&
        githubSecret.trim().length >= 8 &&
        githubSecret.length <= 4096),
    "GITHUB_APP_CLIENT_ID, GITHUB_APP_SLUG and GITHUB_APP_CLIENT_SECRET must be configured together.",
  );
  if (github.clientId) values.GITHUB_APP_CLIENT_SECRET = githubSecret;
  for (const [clientId, clientSecret] of providerSecretPairs) {
    const id = env[clientId] || "";
    const secret = env[clientSecret] || "";
    demand(
      (!id && !secret) || (id && secret),
      `${clientId} and ${clientSecret} must be configured as a pair.`,
    );
    if (id) {
      providerClientId(id, clientId);
      demand(
        typeof secret === "string" && secret.trim().length >= 8 && secret.length <= 4096,
        `${clientSecret} is not a usable provider application secret.`,
      );
      values[clientSecret] = secret;
    }
  }
  demand(
    env.LINKEDIN_MEMBER_READBACK !== "true" ||
      (env.LINKEDIN_OAUTH_CLIENT_ID && env.LINKEDIN_OAUTH_CLIENT_SECRET),
    "LinkedIn member readback requires a configured LinkedIn OAuth application.",
  );
  if (env.STRIPE_SANDBOX_ENABLED === "true") {
    demand(env.DEPLOY_ENV === "staging" &&
      /^(sk|rk)_test_[A-Za-z0-9_]+$/.test(env.STRIPE_SANDBOX_SECRET_KEY || "") &&
      /^whsec_[A-Za-z0-9_]+$/.test(env.STRIPE_SANDBOX_WEBHOOK_SECRET || "") &&
      /^price_[A-Za-z0-9_]+$/.test(env.STRIPE_SANDBOX_PRICE_ID || ""),
      "Stripe sandbox requires staging, test-only credentials, a webhook signing secret and a Price.");
    values.STRIPE_SECRET_KEY = env.STRIPE_SANDBOX_SECRET_KEY;
    values.STRIPE_WEBHOOK_SECRET = env.STRIPE_SANDBOX_WEBHOOK_SECRET;
  }
  return values;
}

export async function verifySandboxPrice(env, send = fetch) {
  if (env.STRIPE_SANDBOX_ENABLED !== "true") return { enabled: false };
  // Validate the complete configuration before sending even a read-only request.
  const secrets = deploymentSecrets(env);
  const response = await send(
    "https://api.stripe.com/v1/prices/" + encodeURIComponent(env.STRIPE_SANDBOX_PRICE_ID),
    {
      headers: { Authorization: "Bearer " + secrets.STRIPE_SECRET_KEY },
      redirect: "error", signal: AbortSignal.timeout(15000),
    },
  ).catch(() => { throw new Error("Stripe sandbox Price verification request failed."); });
  demand(response.ok, "Stripe sandbox Price verification was refused. No deployment performed.");
  const price = await response.json().catch(() => null);
  demand(price && price.id === env.STRIPE_SANDBOX_PRICE_ID &&
    price.livemode === false && price.active === true &&
    price.currency === "usd" && price.unit_amount === 500 &&
    price.recurring?.interval === "month" && price.recurring.interval_count === 1,
    "Stripe sandbox Price must be active, test-mode, USD 5 per month.");
  return { enabled: true, priceVerified: true, livemode: false };
}
