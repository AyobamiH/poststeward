import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const workflow = readFileSync(
  ".github/workflows/staging-cross-tenant-acceptance.yml",
  "utf8",
);
const script = readFileSync("scripts/hosted-acceptance.mjs", "utf8");

test("hosted tenant-isolation acceptance is explicit, main-only and protected by staging secrets", () => {
  assert.match(workflow, /RUN_CROSS_TENANT_ACCEPTANCE/);
  assert.match(workflow, /github\.ref == 'refs\/heads\/main'/);
  assert.match(workflow, /github\.repository == 'AyobamiH\/poststeward'/);
  assert.match(workflow, /github\.actor == 'AyobamiH'/);
  assert.match(workflow, /environment:\n      name: staging/);
  assert.match(workflow, /secrets\.CROSS_TENANT_AGENT_TOKEN_A/);
  assert.match(workflow, /secrets\.CROSS_TENANT_AGENT_TOKEN_B/);
  assert.match(workflow, /cross-tenant-state/);
});

test("state-isolation mode uses only workspace status and reversible publishing pause", () => {
  const start = script.indexOf("export async function checkCrossTenantStateIsolation");
  const end = script.indexOf("\nasync function main()", start);
  assert.ok(start >= 0 && end > start);
  const body = script.slice(start, end);
  assert.match(body, /workspace_status/);
  assert.match(body, /publishing_pause/);
  assert.match(body, /paused: true/);
  assert.match(body, /paused: false/);
  assert.match(body, /hostile Origin replay/);
  assert.doesNotMatch(
    body,
    /"publish_now"|"schedule_create"|"account_disconnect"|\/api\/recovery\/|"billing_[^"]*"/,
  );
  assert.match(body, /providerEffectAttempted: false/);
  assert.match(body, /paymentAttempted: false/);
  assert.match(body, /recoveryAttempted: false/);
  assert.match(body, /rawWorkspaceIdsEmitted: false/);
});

test("cross-tenant acceptance binds hosted evidence to the exact workflow release", () => {
  assert.match(script, /process\.env\.GITHUB_SHA/);
  assert.match(script, /Hosted staging release does not equal the reviewed workflow revision/);
});
