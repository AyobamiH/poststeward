import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const workflow = readFileSync(
  ".github/workflows/staging-cross-tenant-acceptance.yml",
  "utf8",
);
const harness = readFileSync(
  "scripts/staging-cross-tenant-self-contained.mjs",
  "utf8",
);
const controller = readFileSync(
  "scripts/release-promotion-controller.mjs",
  "utf8",
);

test("cross-tenant workflow no longer depends on pre-created agent token secrets", () => {
  assert.doesNotMatch(workflow, /CROSS_TENANT_AGENT_TOKEN_A|CROSS_TENANT_AGENT_TOKEN_B/);
  assert.match(workflow, /scripts\/staging-cross-tenant-self-contained\.mjs/);
  assert.match(workflow, /ALLOWED_OWNER_EMAILS/);
  assert.match(workflow, /CLOUDFLARE_API_TOKEN/);
  assert.match(workflow, /RUN_CROSS_TENANT_ACCEPTANCE/);
});

test("self-contained acceptance mints only bounded read+publish grants then revokes and erases", () => {
  assert.match(harness, /"\/api\/grants"/);
  assert.match(harness, /scopes: \["read", "publish"\]/);
  assert.match(harness, /hours: 1/);
  assert.match(harness, /"DELETE"/);
  assert.match(harness, /"\/api\/lifecycle\/delete"/);
  assert.match(harness, /checkCrossTenantStateIsolation/);
  assert.match(harness, /providerEffectAttempted: false/);
  assert.match(harness, /paymentAttempted: false/);
  assert.match(harness, /customerWorkspaceTouched: false/);
  assert.doesNotMatch(harness, /publish_now|billing_checkout|recovery\/execute/);
});

test("promotion controller consumes reversible exact-release tenant isolation evidence", () => {
  assert.match(controller, /checkCrossTenantStateIsolation/);
  assert.doesNotMatch(controller, /checkCrossTenantIsolation/);
  assert.doesNotMatch(controller, /POSTSTEWARD_FOREIGN_DELIVERY_ID/);
  assert.match(controller, /expectedRelease/);
  assert.match(controller, /evidenceClass: "hosted_observation"/);
});
