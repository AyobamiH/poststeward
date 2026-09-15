import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const script = readFileSync("scripts/staging-cross-tenant-fixture.mjs", "utf8");
const workflow = readFileSync(".github/workflows/staging-cross-tenant-acceptance.yml", "utf8");

test("hosted isolation creates only short-lived synthetic grants and verifies cleanup", () => {
  assert.match(script, /60 \* 60 \* 1000/);
  assert.match(script, /JSON\.stringify\(\["read", "publish"\]\)/);
  assert.match(script, /DELETE FROM grants WHERE token_hash IN \(\?,\?\)/);
  assert.match(script, /SELECT count\(\*\) AS remaining FROM grants WHERE token_hash IN \(\?,\?\)/);
  assert.doesNotMatch(script, /principals|sessions|provider|stripe|recovery/);
  assert.match(script, /observerRelease/);
  assert.match(script, /await hostedRelease\(origin\)/);
});

test("cross-tenant workflow uses protected staging D1 authority rather than stored agent tokens", () => {
  assert.match(workflow, /environment:\s*\n\s*name: staging/);
  assert.match(workflow, /CLOUDFLARE_API_TOKEN: \$\{\{ secrets\.CLOUDFLARE_API_TOKEN \}\}/);
  assert.match(workflow, /staging-cross-tenant-fixture\.mjs/);
  assert.doesNotMatch(workflow, /CROSS_TENANT_AGENT_TOKEN_A|CROSS_TENANT_AGENT_TOKEN_B|secrets: inherit/);
  assert.match(workflow, /poststeward-staging\.woeinvests\.workers\.dev/);
});
