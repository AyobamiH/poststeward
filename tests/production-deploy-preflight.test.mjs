import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { preflightProductionDeploy } from "../scripts/production-deploy-preflight.mjs";

const account = "a".repeat(32);
const database = "11111111-2222-4333-8444-555555555555";
const release = "b".repeat(40);
const encryption = Buffer.alloc(32, 7).toString("base64");
const base = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));

function environment(overrides = {}) {
  return {
    DEPLOY_ENV: "production",
    GITHUB_REF: "refs/heads/main",
    GITHUB_REPOSITORY: "AyobamiH/poststeward",
    GITHUB_ACTOR: "AyobamiH",
    GITHUB_SHA: release,
    CLOUDFLARE_ACCOUNT_ID: account,
    CLOUDFLARE_API_TOKEN: "cf-token-" + "x".repeat(32),
    D1_ID: database,
    APP_ORIGIN: "https://app.poststeward.com",
    PRODUCTION_CREDENTIAL_MODE: "shared_staging_bootstrap",
    OIDC_ISSUER: "https://accounts.google.com",
    OIDC_CLIENT_ID: "production-google-client",
    ENCRYPTION_ROOT_WRITE: "legacy",
    ENCRYPTION_KEY: encryption,
    OIDC_CLIENT_SECRET: "production-google-secret",
    ALLOWED_OWNER_EMAILS: "owner@example.com",
    GITHUB_APP_CLIENT_ID: "",
    GITHUB_APP_SLUG: "",
    GITHUB_APP_CLIENT_SECRET: "",
    X_OAUTH_CLIENT_ID: "",
    X_OAUTH_CLIENT_SECRET: "",
    THREADS_OAUTH_CLIENT_ID: "",
    THREADS_OAUTH_CLIENT_SECRET: "",
    LINKEDIN_OAUTH_CLIENT_ID: "",
    LINKEDIN_OAUTH_CLIENT_SECRET: "",
    LINKEDIN_MEMBER_READBACK: "false",
    STRIPE_SANDBOX_ENABLED: "false",
    STRIPE_SANDBOX_PRICE_ID: "",
    ADVANCED_ENABLED: "false",
    ADVANCED_ROLLOUT_MODE: "disabled",
    ADVANCED_CANARY_BPS: "0",
    ADVANCED_CANARY_SEED: "",
    ...overrides,
  };
}

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function cloudflare({ d1Name = "poststeward-identity-production", workerStatus = 404 } = {}) {
  return async (url) => {
    const value = String(url);
    if (value.endsWith(`/accounts/${account}/workers/subdomain`))
      return json(200, { success: true, result: { subdomain: "woeinvests" } });
    if (value.endsWith(`/accounts/${account}/d1/database/${database}`))
      return json(200, {
        success: true,
        result: {
          uuid: database,
          name: d1Name,
          read_replication: { mode: "disabled" },
        },
      });
    if (value.endsWith(`/accounts/${account}/workers/scripts/poststeward/settings`))
      return json(workerStatus, workerStatus === 200 ? { success: true, result: { bindings: [] } } : {});
    throw new Error(`unexpected request ${value}`);
  };
}

test("production preflight validates exact protected environment without exposing secrets", async () => {
  const env = environment();
  const report = await preflightProductionDeploy(env, cloudflare(), base);
  assert.equal(report.ready, true);
  assert.equal(report.origin, "https://app.poststeward.com");
  assert.equal(report.credentialMode, "shared_staging_bootstrap");
  assert.equal(report.credentialRotationRequired, true);
  assert.equal(report.release, release);
  assert.equal(report.dedicatedProductionDatabase, true);
  assert.equal(report.workerAlreadyPresent, false);
  const serialized = JSON.stringify(report);
  assert.ok(!serialized.includes(env.CLOUDFLARE_API_TOKEN));
  assert.ok(!serialized.includes(env.ENCRYPTION_KEY));
  assert.ok(!serialized.includes(env.OIDC_CLIENT_SECRET));
});

test("production preflight refuses origin drift and Advanced enablement", async () => {
  await assert.rejects(
    () =>
      preflightProductionDeploy(
        environment({ APP_ORIGIN: "https://other.example.com" }),
        cloudflare(),
        base,
      ),
    /exactly https:\/\/app\.poststeward\.com/,
  );
  await assert.rejects(
    () =>
      preflightProductionDeploy(
        environment({
          ADVANCED_ENABLED: "true",
          ADVANCED_ROLLOUT_MODE: "global",
          ADVANCED_CANARY_BPS: "10000",
        }),
        cloudflare(),
        base,
      ),
    /Advanced globally disabled/,
  );
});

test("production preflight refuses half-configured provider authority", async () => {
  await assert.rejects(
    () =>
      preflightProductionDeploy(
        environment({ X_OAUTH_CLIENT_ID: "x-client" }),
        cloudflare(),
        base,
      ),
    /X_OAUTH_CLIENT_ID and X_OAUTH_CLIENT_SECRET must be configured as a pair/,
  );
});

test("production preflight refuses the wrong D1 identity", async () => {
  await assert.rejects(
    () => preflightProductionDeploy(environment(), cloudflare({ d1Name: "wrong-db" }), base),
    /Production D1 identity does not match/,
  );
});


test("production preflight requires an explicit credential isolation mode", async () => {
  await assert.rejects(
    () =>
      preflightProductionDeploy(
        environment({ PRODUCTION_CREDENTIAL_MODE: "" }),
        cloudflare(),
        base,
      ),
    /PRODUCTION_CREDENTIAL_MODE/,
  );
});

test("production preflight records isolated credentials without rotation debt", async () => {
  const report = await preflightProductionDeploy(
    environment({ PRODUCTION_CREDENTIAL_MODE: "isolated" }),
    cloudflare(),
    base,
  );
  assert.equal(report.credentialMode, "isolated");
  assert.equal(report.credentialRotationRequired, false);
});
