import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const edge = readFileSync("src/canary-edge.ts", "utf8");
const deploy = readFileSync(".github/workflows/deploy.yml", "utf8");
const observeWorkflow = readFileSync(
  ".github/workflows/staging-advanced-slo-observe.yml",
  "utf8",
);
const collector = readFileSync("scripts/advanced-slo-observe.mjs", "utf8");
const migration = readFileSync(
  "migrations/0014_advanced_slo_observations.sql",
  "utf8",
);

test("canary wrapper observes durable product state without changing provider effect code", () => {
  for (const event of [
    "source_change",
    "inventory_snapshot",
    "spaced_allocation",
    "publication_eligible",
    "verified_readback",
    "schedule_observation",
    "provider_reconciliation",
    "oauth_refresh_attempt",
    "oauth_refresh_success",
    "scheduled_metrics_capture",
  ])
    assert.ok(edge.includes(`"${event}"`));
  assert.match(edge, /FROM external_effects WHERE workspace=\?/);
  assert.match(edge, /operation\?\.tier === "advanced"/);
  assert.match(edge, /pruneAdvancedSloTelemetry/);
});

test("canary evidence tables retain only bounded observation fields", () => {
  assert.match(migration, /CREATE TABLE advanced_slo_events/);
  assert.match(migration, /CREATE TABLE advanced_canary_runs/);
  assert.match(migration, /seed_hash TEXT NOT NULL/);
  assert.doesNotMatch(migration, /access_token|refresh_token|content|raw_workspace/);
  assert.match(migration, /canary_bps INTEGER NOT NULL CHECK \(canary_bps BETWEEN 1 AND 1000\)/);
});

test("deploy records a canary boundary only after hosted safety checks and attempts stop on failed starts", () => {
  const deployAt = deploy.indexOf("Validate secrets and database; migrate and deploy");
  const lifecycleAt = deploy.indexOf("Verify non-destructive account lifecycle boundary");
  const boundaryAt = deploy.indexOf("Record verified Advanced canary boundary");
  const rollbackAt = deploy.indexOf("Resolve fail-closed Advanced rollback");
  assert.ok(deployAt >= 0 && lifecycleAt > deployAt && boundaryAt > lifecycleAt);
  assert.ok(rollbackAt > boundaryAt);
  assert.match(deploy, /startsWith\(inputs\.advanced_rollout_request, 'start_'\)/);
  assert.match(deploy, /POSTSTEWARD_ADVANCED_ROLLOUT_REQUEST: stop/);
  assert.match(deploy, /node scripts\/advanced-canary-run\.mjs/);
});

test("scheduled SLO observer uses protected staging read authority and treats accumulation as normal", () => {
  assert.match(observeWorkflow, /environment:\n      name: staging/);
  assert.match(observeWorkflow, /secrets\.CLOUDFLARE_API_TOKEN/);
  assert.match(observeWorkflow, /POSTSTEWARD_SLO_OPTIONAL: "true"/);
  assert.match(observeWorkflow, /status -eq 2/);
  assert.match(observeWorkflow, /evidence is valid but not promotion-ready yet/);
  assert.doesNotMatch(observeWorkflow, /provider.*secret|X_OAUTH_CLIENT_SECRET|STRIPE_SANDBOX_SECRET_KEY/);
});

test("collector outputs aggregate evidence and never selects secret/provider content columns", () => {
  assert.match(collector, /count\(DISTINCT workspace\)/);
  assert.match(collector, /duplicateExternalEffects/);
  assert.match(collector, /providerReconciliation/);
  assert.match(collector, /webhookReconciliation/);
  assert.doesNotMatch(collector, /access_token|refresh_token|text_digest|post_id|seed_hash.*console/);
});
