import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const workflow = readFileSync(
  ".github/workflows/staging-cross-tenant-acceptance.yml",
  "utf8",
);
const script = readFileSync("scripts/hosted-acceptance.mjs", "utf8");
const harness = readFileSync(
  "scripts/staging-cross-tenant-self-contained.mjs",
  "utf8",
);

test("hosted tenant-isolation acceptance is explicit, main-only and self-contained in protected staging", () => {
  assert.match(workflow, /RUN_CROSS_TENANT_ACCEPTANCE/);
  assert.match(workflow, /github\.ref == 'refs\/heads\/main'/);
  assert.match(workflow, /github\.repository == 'AyobamiH\/poststeward'/);
  assert.match(workflow, /github\.actor == 'AyobamiH'/);
  assert.match(workflow, /environment:\n      name: staging/);
  assert.doesNotMatch(
    workflow,
    /CROSS_TENANT_AGENT_TOKEN_A|CROSS_TENANT_AGENT_TOKEN_B/,
  );
  assert.match(workflow, /secrets\.CLOUDFLARE_API_TOKEN/);
  assert.match(workflow, /secrets\.ALLOWED_OWNER_EMAILS/);
  assert.match(workflow, /staging-cross-tenant-self-contained\.mjs/);
  assert.match(harness, /checkCrossTenantStateIsolation/);
  assert.match(harness, /scopes: \["read", "publish"\]/);
  assert.match(harness, /hours: 1/);
  assert.match(harness, /"\/api\/lifecycle\/delete"/);
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
  assert.match(harness, /process\.env\.GITHUB_SHA/);
  assert.match(
    harness,
    /Hosted staging release does not equal the reviewed workflow SHA/,
  );
});
