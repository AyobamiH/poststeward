import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const deploy = readFileSync(".github/workflows/deploy.yml", "utf8");
const report = readFileSync(".github/workflows/release-promotion-controller.yml", "utf8");
const controller = readFileSync("scripts/release-promotion-controller.mjs", "utf8");

test("protected staging enforces exact promotion context after smoke and before canary acceptance", () => {
  const marker = "      - name: Enforce exact restricted-staging promotion evidence\n";
  assert.ok(deploy.includes(marker));
  const step = deploy.split(marker)[1].split("      - name:")[0];
  assert.match(step, /if: inputs\.environment == 'staging'/);
  assert.match(step, /POSTSTEWARD_PROMOTION_TARGET: restricted_staging/);
  assert.match(step, /POSTSTEWARD_PROMOTION_MODE: enforce/);
  assert.ok(step.includes("POSTSTEWARD_EXPECTED_RELEASE: ${{ github.sha }}"));
  assert.ok(step.includes("POSTSTEWARD_ORIGIN: ${{ steps.config.outputs.origin }}"));
  assert.match(step, /run: node scripts\/release-promotion-controller\.mjs/);
  assert.doesNotMatch(step, /secrets\.|CLOUDFLARE_API_TOKEN|AGENT_TOKEN|continue-on-error/);
  assert.ok(deploy.indexOf("run: node scripts/lifecycle-smoke.mjs") < deploy.indexOf(marker));
  assert.ok(deploy.indexOf(marker) < deploy.indexOf("      - name: Record verified Advanced canary boundary"));
});

test("scheduled promotion status remains read-only, stage-correct and observational", () => {
  assert.match(report, /permissions:\n  contents: read/);
  assert.match(report, /POSTSTEWARD_PROMOTION_MODE: report/);
  assert.doesNotMatch(report, /POSTSTEWARD_EXPECTED_RELEASE:/);
  assert.ok(report.includes("scripts/promotion-evidence.mjs"));
  assert.ok(report.includes('cron: "23 6 * * *"'));
  assert.ok(report.includes("'restricted_staging'"));
  assert.doesNotMatch(report, /secrets\.|contents: write|actions: write|CLOUDFLARE_API_TOKEN|AGENT_TOKEN/);
});

test("controller guards collection and separates observational from enforcement release binding", () => {
  assert.match(controller, /initial\.releaseBinding\.ready && initial\.runtimePolicy\.healthy/);
  assert.match(controller, /process\.exitCode = promotionExitCode\(report, mode\)/);
  assert.match(controller, /export function resolveExpectedRelease/);
  assert.match(controller, /mode === "report" \? hostedRelease : githubSha \|\| ""/);
  assert.doesNotMatch(controller, /workspace_delete|recovery\/execute|publishing_pause|workflow_dispatch|advanced-rollout-request/);
});

test("capacity observation command remains available without removing SLO and governance commands", () => {
  const scripts = JSON.parse(readFileSync("package.json", "utf8")).scripts;
  assert.equal(scripts["capacity:observe"], "node scripts/capacity-observe.mjs");
  for (const name of ["slo:observe", "slo:evaluate", "promotion:status", "governance:check", "governance:apply"])
    assert.equal(typeof scripts[name], "string");
});
