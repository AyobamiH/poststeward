import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildConfiguration,
  deploymentSecrets,
  validateConfiguration,
  verifySandboxPrice,
} from "../scripts/deployment-config.mjs";
const base = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
const environment = {
  DEPLOY_ENV: "staging",
  CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
  D1_ID: "11111111-1111-4111-8111-111111111111",
  GITHUB_SHA: "b".repeat(40),
  WORKERS_SUBDOMAIN: "example-account",
  OIDC_ISSUER: "https://identity.example",
  OIDC_CLIENT_ID: "poststeward-staging",
};
test("deployment environments have separate Worker names, D1 names, origins and rate namespaces", () => {
  const staging = buildConfiguration(base, environment);
  const production = buildConfiguration(base, {
    ...environment,
    DEPLOY_ENV: "production",
    D1_ID: "22222222-2222-4222-8222-222222222222",
  });
  for (const pick of [
    (c) => c.name,
    (c) => c.d1_databases[0].database_id,
    (c) => c.d1_databases[0].database_name,
    (c) => c.vars.PUBLIC_ORIGIN,
    (c) => c.ratelimits[0].namespace_id,
  ])
    assert.notEqual(pick(staging), pick(production));
  assert.equal(staging.preview_urls, false);
  assert.equal(staging.vars.ADVANCED_ENABLED, "false");
  assert.equal(staging.vars.SIGNUP_MODE, "restricted");
  assert.equal(staging.vars.GITHUB_APP_CLIENT_ID, "");
  assert.equal(staging.vars.GITHUB_APP_SLUG, "");
  assert.equal(staging.vars.X_OAUTH_CLIENT_ID, "");
  assert.equal(staging.vars.THREADS_OAUTH_CLIENT_ID, "");
  assert.equal(staging.vars.LINKEDIN_OAUTH_CLIENT_ID, "");
  assert.equal(staging.vars.LINKEDIN_MEMBER_READBACK, "false");
  assert.equal(base.name, "poststeward");
});
test("deployment rejects hostile and ambiguous configuration before touching Cloudflare", () => {
  for (const APP_ORIGIN of [
    "https://user:secret@example.com",
    "http://example.com",
    "https://example.com/path",
    "https://example.com/",
    "https://example.com?secret=value",
    "https://127.0.0.1",
    "https://localhost",
    "https://other.example-account.workers.dev",
    "https://configure.invalid",
  ])
    assert.throws(() =>
      buildConfiguration(base, { ...environment, APP_ORIGIN }),
    );
  assert.throws(() =>
    buildConfiguration(base, { ...environment, DEPLOY_ENV: "preview" }),
  );
  assert.throws(() =>
    buildConfiguration(base, {
      ...environment,
      D1_ID: "00000000-0000-0000-0000-000000000000",
    }),
  );
  assert.throws(() =>
    buildConfiguration(base, { ...environment, GITHUB_SHA: "main" }),
  );
  assert.throws(
    () =>
      buildConfiguration(base, {
        ...environment,
        GITHUB_APP_CLIENT_ID: "client-only",
      }),
    /configured together/,
  );
  assert.throws(
    () =>
      buildConfiguration(base, {
        ...environment,
        GITHUB_APP_SLUG: "slug-only",
      }),
    /configured together/,
  );
  assert.throws(() =>
    buildConfiguration(base, {
      ...environment,
      X_OAUTH_CLIENT_ID: "invalid client id",
    }),
    /X_OAUTH_CLIENT_ID/,
  );
  assert.throws(() =>
    buildConfiguration(base, {
      ...environment,
      LINKEDIN_MEMBER_READBACK: "true",
    }),
    /LinkedIn member readback/,
  );
  const unsafe = buildConfiguration(base, environment);
  unsafe.vars.MPP_ENABLED = "true";
  assert.throws(() => validateConfiguration(unsafe), /Purchases/);
});
test("custom domains disable workers.dev and only route the exact chosen hostname", () => {
  const config = buildConfiguration(base, {
    ...environment,
    APP_ORIGIN: "https://publish.example.com",
  });
  assert.equal(config.workers_dev, false);
  assert.deepEqual(config.routes, [
    { pattern: "publish.example.com", custom_domain: true },
  ]);
});
test("deployment validates mandatory secret material and never includes the Cloudflare token", () => {
  const input = {
    ENCRYPTION_KEY: Buffer.alloc(32, 7).toString("base64"),
    OIDC_CLIENT_SECRET: "test-secret-not-real",
    ALLOWED_OWNER_EMAILS: "owner@example.com",
    CLOUDFLARE_API_TOKEN: "deployment-only",
  };
  assert.deepEqual(Object.keys(deploymentSecrets(input)).sort(), [
    "ALLOWED_OWNER_EMAILS",
    "ENCRYPTION_KEY",
    "OIDC_CLIENT_SECRET",
  ]);
  assert.throws(
    () => deploymentSecrets({ ...input, ENCRYPTION_KEY: "not-a-key" }),
    /32-byte/,
  );
  assert.throws(
    () => deploymentSecrets({ ...input, ALLOWED_OWNER_EMAILS: "" }),
    /invited/,
  );
});
test("GitHub App source credentials are optional, all-or-nothing and secret-minimised", () => {
  const mandatory = {
    ENCRYPTION_KEY: Buffer.alloc(32, 7).toString("base64"),
    OIDC_CLIENT_SECRET: "test-secret-not-real",
    ALLOWED_OWNER_EMAILS: "owner@example.com",
  };
  const configuredEnvironment = {
    ...environment,
    GITHUB_APP_CLIENT_ID: "Iv1.test-client",
    GITHUB_APP_SLUG: "poststeward-test",
  };
  const config = buildConfiguration(base, configuredEnvironment);
  assert.equal(config.vars.GITHUB_APP_CLIENT_ID, "Iv1.test-client");
  assert.equal(config.vars.GITHUB_APP_SLUG, "poststeward-test");
  assert.ok(!("GITHUB_APP_CLIENT_SECRET" in config.vars));
  const secrets = deploymentSecrets({
    ...mandatory,
    ...configuredEnvironment,
    GITHUB_APP_CLIENT_SECRET: "github-client-secret",
  });
  assert.deepEqual(Object.keys(secrets).sort(), [
    "ALLOWED_OWNER_EMAILS",
    "ENCRYPTION_KEY",
    "GITHUB_APP_CLIENT_SECRET",
    "OIDC_CLIENT_SECRET",
  ]);
  assert.throws(
    () =>
      deploymentSecrets({
        ...mandatory,
        GITHUB_APP_CLIENT_ID: "Iv1.test-client",
        GITHUB_APP_SLUG: "poststeward-test",
      }),
    /configured together/,
  );
  assert.throws(
    () =>
      deploymentSecrets({
        ...mandatory,
        GITHUB_APP_CLIENT_SECRET: "orphan-github-secret",
      }),
    /configured together/,
  );
});
test("provider application secrets are optional but fail closed unless paired with their client ID", () => {
  const mandatory = {
    ENCRYPTION_KEY: Buffer.alloc(32, 7).toString("base64"),
    OIDC_CLIENT_SECRET: "test-secret-not-real",
    ALLOWED_OWNER_EMAILS: "owner@example.com",
  };
  const configured = deploymentSecrets({
    ...mandatory,
    X_OAUTH_CLIENT_ID: "x-client",
    X_OAUTH_CLIENT_SECRET: "x-client-secret",
    THREADS_OAUTH_CLIENT_ID: "threads-client",
    THREADS_OAUTH_CLIENT_SECRET: "threads-client-secret",
    LINKEDIN_OAUTH_CLIENT_ID: "linkedin-client",
    LINKEDIN_OAUTH_CLIENT_SECRET: "linkedin-client-secret",
    LINKEDIN_MEMBER_READBACK: "true",
  });
  assert.deepEqual(Object.keys(configured).sort(), [
    "ALLOWED_OWNER_EMAILS",
    "ENCRYPTION_KEY",
    "LINKEDIN_OAUTH_CLIENT_SECRET",
    "OIDC_CLIENT_SECRET",
    "THREADS_OAUTH_CLIENT_SECRET",
    "X_OAUTH_CLIENT_SECRET",
  ]);
  assert.throws(
    () => deploymentSecrets({ ...mandatory, X_OAUTH_CLIENT_ID: "x-client" }),
    /configured as a pair/,
  );
  assert.throws(
    () => deploymentSecrets({ ...mandatory, X_OAUTH_CLIENT_SECRET: "orphan-secret" }),
    /configured as a pair/,
  );
  assert.throws(
    () => deploymentSecrets({ ...mandatory, LINKEDIN_MEMBER_READBACK: "true" }),
    /LinkedIn member readback/,
  );
});

test("sandbox deployment is explicit, staging-only, and keeps paid automation disabled", () => {
  const sandbox = { ...environment, STRIPE_SANDBOX_ENABLED: "true", STRIPE_SANDBOX_PRICE_ID: "price_sandbox" };
  const c = buildConfiguration(base, sandbox);
  assert.equal(c.vars.STRIPE_SANDBOX_ENABLED, "true");
  assert.equal(c.vars.STRIPE_PRICE_ID, "price_sandbox");
  assert.equal(c.vars.ADVANCED_ENABLED, "false");
  assert.equal(c.vars.MPP_ENABLED, "false");
  assert.throws(() => buildConfiguration(base, { ...sandbox, DEPLOY_ENV: "production" }), /sandbox/);
  assert.throws(() => buildConfiguration(base, { ...sandbox, STRIPE_SANDBOX_PRICE_ID: "" }), /sandbox/);
});
test("sandbox preflight rejects live keys before network and rejects live or wrong-price objects", async () => {
  const env = { ...environment, STRIPE_SANDBOX_ENABLED: "true",
    STRIPE_SANDBOX_PRICE_ID: "price_sandbox", STRIPE_SANDBOX_SECRET_KEY: "sk_test_placeholder",
    STRIPE_SANDBOX_WEBHOOK_SECRET: "whsec_placeholder",
    ENCRYPTION_KEY: Buffer.alloc(32, 7).toString("base64"),
    OIDC_CLIENT_SECRET: "test-secret-not-real", ALLOWED_OWNER_EMAILS: "owner@example.com",
  };
  let calls = 0;
  let price = { id: "price_sandbox", livemode: false, active: true, currency: "usd", unit_amount: 500,
    recurring: { interval: "month", interval_count: 1 } };
  const send = async (url, init) => {
    calls++;
    assert.equal(url, "https://api.stripe.com/v1/prices/price_sandbox");
    assert.equal(init.redirect, "error");
    assert.equal(init.headers.Authorization, "Bearer sk_test_placeholder");
    return Response.json(price);
  };
  await assert.rejects(verifySandboxPrice({ ...env, STRIPE_SANDBOX_SECRET_KEY: "sk_live_placeholder" }, send), /test-only/);
  assert.equal(calls, 0);
  assert.deepEqual(await verifySandboxPrice(env, send), { enabled: true, priceVerified: true, livemode: false });
  price = { ...price, livemode: true };
  await assert.rejects(verifySandboxPrice(env, send), /test-mode/);
  price = { ...price, livemode: false, unit_amount: 5000 };
  await assert.rejects(verifySandboxPrice(env, send), /USD 5/);
  const values = deploymentSecrets(env);
  assert.equal(values.STRIPE_SECRET_KEY, env.STRIPE_SANDBOX_SECRET_KEY);
  assert.equal(values.STRIPE_WEBHOOK_SECRET, env.STRIPE_SANDBOX_WEBHOOK_SECRET);
  assert.equal(values.STRIPE_SANDBOX_SECRET_KEY, undefined);
});
