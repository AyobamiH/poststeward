import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const workflow = readFileSync(".github/workflows/staging-capacity-observation.yml", "utf8");

test("capacity observer covers staging and production, stays read-only and exact-release bound", () => {
  assert.match(workflow, /environment:\n      name: \$\{\{ matrix\.environment \}\}/);
  assert.match(workflow, /- environment: staging/);
  assert.match(workflow, /- environment: production/);
  assert.match(workflow, /permissions:\n  contents: read/);
  assert.match(workflow, /readiness\.json/);
  assert.match(workflow, /POSTSTEWARD_EXPECTED_RELEASE/);
  assert.match(workflow, /secrets\.CLOUDFLARE_API_TOKEN/);
  assert.match(workflow, /capacity-observe\.mjs/);
  assert.match(workflow, /POSTSTEWARD_CAPACITY_TARGET_WORKSPACES: "100"/);
  assert.match(workflow, /provider-quota-20260919\.json/);
  assert.match(workflow, /cloudflare-pricing-20260919\.json/);
  assert.doesNotMatch(
    workflow,
    /contents: write|actions: write|secrets: inherit|wrangler deploy|publish_now|schedule_create/,
  );
});

test("valid but incomplete cost evidence remains non-promotional", () => {
  assert.match(workflow, /status -eq 2/);
  assert.match(
    workflow,
    /Provider quota and reviewed pricing remain explicit evidence inputs rather than assumptions/,
  );
  assert.match(workflow, /ready:j\.ready/);
});
