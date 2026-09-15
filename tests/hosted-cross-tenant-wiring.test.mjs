import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const workflow = readFileSync(
  ".github/workflows/staging-cross-tenant-acceptance.yml",
  "utf8",
);
const script = readFileSync("scripts/hosted-acceptance.mjs", "utf8");
const fixture = readFileSync("scripts/staging-cross-tenant-fixture.mjs", "utf8");

test("hosted tenant-isolation acceptance is explicit, main-only and protected by staging authority", () => {
  assert.match(workflow, /RUN_CROSS_TENANT_ACCEPTANCE/);
  assert.match(workflow, /github\.ref == 'refs\/heads\/main'/);
  assert.match(workflow, /github\.repository == 'AyobamiH\/poststeward'/);
  assert.match(workflow, /github\.actor == 'AyobamiH'/);
  assert.match(workflow, /environment:\n      name: staging/);
  assert.match(workflow, /vars\.CLOUDFLARE_ACCOUNT_ID/);
  assert.match(workflow, /vars\.D1_ID/);
  assert.match(workflow, /secrets\.CLOUDFLARE_API_TOKEN/);
  assert.match(workflow, /staging-cross-tenant-fixture\.mjs/);
  assert.doesNotMatch(workflow, /CROSS_TENANT_AGENT_TOKEN_A|CROSS_TENANT_AGENT_TOKEN_B/);
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

test("ephemeral observer binds isolation evidence to the exact hosted staging release", () => {
  assert.match(fixture, /await hostedRelease\(origin\)/);
  assert.match(fixture, /body\?\.environment === "staging"/);
  assert.match(fixture, /checkCrossTenantStateIsolation\(origin, secrets\[0\], secrets\[1\], release\)/);
  assert.match(fixture, /observerRelease: env\.GITHUB_SHA/);
  assert.doesNotMatch(fixture, /expectedRelease = env\.GITHUB_SHA/);
});
