import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { resolveExpectedRelease } from "../scripts/release-promotion-controller.mjs";

const workflow = readFileSync(
  ".github/workflows/release-promotion-controller.yml",
  "utf8",
);

const automaticTarget =
  "${{ github.event_name == 'workflow_dispatch' && inputs.target || 'restricted_staging' }}";
const automaticOrigin =
  "${{ github.event_name == 'workflow_dispatch' && inputs.origin || 'https://poststeward-staging.woeinvests.workers.dev' }}";

test("automatic promotion runs evaluate restricted staging rather than pretending staging is production", () => {
  assert.ok(workflow.includes(`POSTSTEWARD_PROMOTION_TARGET: ${automaticTarget}`));
  assert.ok(workflow.includes(`POSTSTEWARD_ORIGIN: ${automaticOrigin}`));
  assert.ok(workflow.includes(`group: release-promotion-controller-${automaticTarget}`));
});

test("automatic report binds to the exact hosted runtime instead of an undeployed repository SHA", () => {
  assert.doesNotMatch(workflow, /POSTSTEWARD_EXPECTED_RELEASE:/);
  assert.equal(
    resolveExpectedRelease({
      mode: "report",
      explicitExpectedRelease: "",
      githubSha: "b".repeat(40),
      hostedRelease: "a".repeat(40),
    }),
    "a".repeat(40),
  );
});

test("enforcement still binds to the reviewed deployment SHA", () => {
  assert.equal(
    resolveExpectedRelease({
      mode: "enforce",
      explicitExpectedRelease: "",
      githubSha: "b".repeat(40),
      hostedRelease: "a".repeat(40),
    }),
    "b".repeat(40),
  );
  assert.equal(
    resolveExpectedRelease({
      mode: "enforce",
      explicitExpectedRelease: "c".repeat(40),
      githubSha: "b".repeat(40),
      hostedRelease: "a".repeat(40),
    }),
    "c".repeat(40),
  );
});

test("manual promotion review can select an explicit target and exact origin", () => {
  assert.match(
    workflow,
    /target:\n\s+description:[\s\S]*?options:\n\s+- restricted_staging\n\s+- advanced_canary\n\s+- production\n\s+- public_launch/,
  );
  assert.match(
    workflow,
    /origin:\n\s+description: Exact HTTPS origin that corresponds to the selected promotion stage\n\s+type: string\n\s+required: true/,
  );
});

test("promotion controller remains read-only", () => {
  assert.match(workflow, /permissions:\n  contents: read\n/);
  assert.doesNotMatch(
    workflow,
    /contents: write|actions: write|deployments: write|secrets\.|CLOUDFLARE_API_TOKEN|OAUTH_CLIENT_SECRET/,
  );
});
