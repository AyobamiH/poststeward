import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  buildConfiguration,
  validateConfiguration,
} from "../scripts/deployment-config.mjs";

const base = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
const staging = {
  DEPLOY_ENV: "staging",
  CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
  D1_ID: "11111111-1111-4111-8111-111111111111",
  GITHUB_SHA: "b".repeat(40),
  WORKERS_SUBDOMAIN: "example-account",
  OIDC_ISSUER: "https://identity.example",
  OIDC_CLIENT_ID: "poststeward-staging",
};

test("ordinary deployment keeps Advanced completely disabled", () => {
  const config = buildConfiguration(base, staging);
  assert.equal(config.vars.ADVANCED_ENABLED, "false");
  assert.equal(config.vars.ADVANCED_ROLLOUT_MODE, "disabled");
  assert.equal(config.vars.ADVANCED_CANARY_BPS, "0");
  assert.equal(config.vars.ADVANCED_CANARY_SEED, "");
});

test("staging may enable only a bounded deterministic canary", () => {
  const config = buildConfiguration(base, {
    ...staging,
    ADVANCED_ENABLED: "true",
    ADVANCED_ROLLOUT_MODE: "canary",
    ADVANCED_CANARY_BPS: "500",
    ADVANCED_CANARY_SEED: "advanced-v1",
  });
  assert.equal(config.vars.ADVANCED_ENABLED, "true");
  assert.equal(config.vars.ADVANCED_ROLLOUT_MODE, "canary");
  assert.equal(config.vars.ADVANCED_CANARY_BPS, "500");
  assert.equal(config.vars.ADVANCED_CANARY_SEED, "advanced-v1");
});

test("deployment refuses broad, malformed or production Advanced rollout", () => {
  assert.throws(
    () =>
      buildConfiguration(base, {
        ...staging,
        ADVANCED_ENABLED: "true",
        ADVANCED_ROLLOUT_MODE: "canary",
        ADVANCED_CANARY_BPS: "1001",
        ADVANCED_CANARY_SEED: "advanced-v1",
      }),
    /at most ten percent/,
  );
  assert.throws(
    () =>
      buildConfiguration(base, {
        ...staging,
        ADVANCED_ENABLED: "true",
        ADVANCED_ROLLOUT_MODE: "global",
        ADVANCED_CANARY_BPS: "0",
        ADVANCED_CANARY_SEED: "advanced-v1",
      }),
  );
  assert.throws(
    () =>
      buildConfiguration(base, {
        ...staging,
        DEPLOY_ENV: "production",
        D1_ID: "22222222-2222-4222-8222-222222222222",
        ADVANCED_ENABLED: "true",
        ADVANCED_ROLLOUT_MODE: "canary",
        ADVANCED_CANARY_BPS: "100",
        ADVANCED_CANARY_SEED: "advanced-v1",
      }),
    /bounded staging canary/,
  );
  const inconsistent = buildConfiguration(base, staging);
  inconsistent.vars.ADVANCED_ROLLOUT_MODE = "canary";
  inconsistent.vars.ADVANCED_CANARY_BPS = "100";
  assert.throws(() => validateConfiguration(inconsistent), /zero, disabled rollout/);
});

test("deploy workflow reads only protected non-secret rollout variables", () => {
  const workflow = readFileSync(".github/workflows/deploy.yml", "utf8");
  for (const name of [
    "ADVANCED_ENABLED",
    "ADVANCED_ROLLOUT_MODE",
    "ADVANCED_CANARY_BPS",
    "ADVANCED_CANARY_SEED",
  ])
    assert.match(workflow, new RegExp(`${name}: \\\$\\{\\{ vars\\.${name} \\}\\}`));
  assert.doesNotMatch(workflow, /secrets\.ADVANCED_CANARY/);
});
