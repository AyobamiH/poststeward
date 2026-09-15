import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { resolveAdvancedRolloutRequest } from "../scripts/advanced-rollout-request.mjs";

const reviewed = {
  DEPLOY_ENV: "staging",
  GITHUB_REF: "refs/heads/main",
  GITHUB_REPOSITORY: "AyobamiH/poststeward",
  GITHUB_ACTOR: "AyobamiH",
  ADVANCED_ENABLED: "false",
  ADVANCED_ROLLOUT_MODE: "disabled",
  ADVANCED_CANARY_BPS: "0",
  ADVANCED_CANARY_SEED: "advanced-v1-stable",
};

test("ordinary deployment preserves protected environment rollout values", () => {
  assert.deepEqual(resolveAdvancedRolloutRequest("environment", reviewed), {
    request: "environment",
    ADVANCED_ENABLED: "false",
    ADVANCED_ROLLOUT_MODE: "disabled",
    ADVANCED_CANARY_BPS: "0",
    ADVANCED_CANARY_SEED: "advanced-v1-stable",
  });
});

test("reviewed staging operator exposes only bounded 1, 5 and 10 percent starts", () => {
  for (const [request, bps] of [
    ["start_100bps", "100"],
    ["start_500bps", "500"],
    ["start_1000bps", "1000"],
  ]) {
    const value = resolveAdvancedRolloutRequest(request, reviewed);
    assert.equal(value.ADVANCED_ENABLED, "true");
    assert.equal(value.ADVANCED_ROLLOUT_MODE, "canary");
    assert.equal(value.ADVANCED_CANARY_BPS, bps);
    assert.equal(value.ADVANCED_CANARY_SEED, reviewed.ADVANCED_CANARY_SEED);
  }
  assert.throws(
    () => resolveAdvancedRolloutRequest("start_2500bps", reviewed),
    /reviewed bounded actions/,
  );
  assert.throws(
    () => resolveAdvancedRolloutRequest("global", reviewed),
    /reviewed bounded actions/,
  );
});

test("stop is a fail-closed kill path and preserves the stable cohort seed", () => {
  const value = resolveAdvancedRolloutRequest("stop", {
    ...reviewed,
    ADVANCED_ENABLED: "true",
    ADVANCED_ROLLOUT_MODE: "canary",
    ADVANCED_CANARY_BPS: "500",
  });
  assert.deepEqual(value, {
    request: "stop",
    ADVANCED_ENABLED: "false",
    ADVANCED_ROLLOUT_MODE: "disabled",
    ADVANCED_CANARY_BPS: "0",
    ADVANCED_CANARY_SEED: "advanced-v1-stable",
  });
});

test("canary changes are staging-main-owner only and starts require a stable seed", () => {
  for (const override of [
    { DEPLOY_ENV: "production" },
    { GITHUB_REF: "refs/heads/feature" },
    { GITHUB_REPOSITORY: "someone/else" },
    { GITHUB_ACTOR: "other-maintainer" },
  ])
    assert.throws(
      () => resolveAdvancedRolloutRequest("start_100bps", { ...reviewed, ...override }),
      /staging-only|reviewed main/,
    );
  assert.throws(
    () =>
      resolveAdvancedRolloutRequest("start_100bps", {
        ...reviewed,
        ADVANCED_CANARY_SEED: "",
      }),
    /stable ADVANCED_CANARY_SEED/,
  );
});

test("workflow has no global option and routes start-stop through the reviewed deploy", () => {
  const workflow = readFileSync(
    ".github/workflows/staging-advanced-canary.yml",
    "utf8",
  );
  const deploy = readFileSync(".github/workflows/deploy.yml", "utf8");
  assert.match(workflow, /APPLY_ADVANCED_CANARY_CHANGE/);
  assert.match(workflow, /start_100bps/);
  assert.match(workflow, /start_500bps/);
  assert.match(workflow, /start_1000bps/);
  assert.match(workflow, /- stop/);
  assert.doesNotMatch(workflow, /- global/);
  assert.match(workflow, /github\.actor == 'AyobamiH'/);
  assert.match(workflow, /uses: \.\/\.github\/workflows\/deploy\.yml/);
  assert.match(workflow, /advanced_rollout_request: \$\{\{ inputs\.action \}\}/);
  assert.match(deploy, /advanced_rollout_request:/);
  const resolveAt = deploy.indexOf("Resolve bounded Advanced rollout request");
  const configureAt = deploy.indexOf("Configure isolated environment");
  assert.ok(resolveAt >= 0 && configureAt > resolveAt);
  assert.match(deploy, /node scripts\/advanced-rollout-request\.mjs/);
});
