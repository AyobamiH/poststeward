import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildConfiguration,
  deploymentSecrets,
  validateConfiguration,
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
test("deployment validates secret material and never includes the Cloudflare token in Worker secrets", () => {
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
