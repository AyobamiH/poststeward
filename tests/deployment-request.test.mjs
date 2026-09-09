import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const request = readFileSync(".github/workflows/deploy-staging-request.yml", "utf8");
const deploy = readFileSync(".github/workflows/deploy.yml", "utf8");
const secretNames = [
  "CLOUDFLARE_API_TOKEN",
  "ENCRYPTION_KEY",
  "OIDC_CLIENT_SECRET",
  "ALLOWED_OWNER_EMAILS",
  "X_OAUTH_CLIENT_SECRET",
  "THREADS_OAUTH_CLIENT_SECRET",
  "LINKEDIN_OAUTH_CLIENT_SECRET",
];

test("staging deployment requests are limited to their explicit main-only path", () => {
  assert.equal(
    request.split("\non:\n")[1].split("\npermissions:")[0],
    "  push:\n    branches: [main]\n    paths:\n      - .github/workflows/deploy-staging-request.yml",
  );
  assert.ok(request.includes("github.actor == 'AyobamiH'"));
  assert.ok(request.includes("github.repository == 'AyobamiH/poststeward'"));
  assert.ok(request.includes("github.ref == 'refs/heads/main'"));
});

test("staging request reuses the same commit with only explicitly named secret fallbacks", () => {
  assert.match(request, /uses: \.\/\.github\/workflows\/deploy\.yml\n/);
  assert.match(request, /with:\n      environment: staging\n/);
  assert.doesNotMatch(request, /secrets: inherit|contents: write|actions: write|workflow_dispatch|production|runs-on:/);
  assert.match(request, /permissions:\n  contents: read\n/);
  const forwarded = request.split("    secrets:\n")[1].trimEnd().split("\n");
  assert.deepEqual(
    forwarded,
    secretNames.map((name) => `      ${name}: \${{ secrets.${name} }}`),
  );
  for (const name of secretNames)
    assert.ok(deploy.includes(`      ${name}:\n        required: false`));
  assert.doesNotMatch(deploy, /secrets: inherit/);
});

test("reusable deployment keeps production manual and both jobs main-only", () => {
  assert.match(deploy, /workflow_call:\n    inputs:\n      environment:[\s\S]*?required: true\n        type: string/);
  assert.match(deploy, /workflow_dispatch:/);
  const guards = deploy.split("\n").filter((line) => line.startsWith("    if:"));
  assert.equal(guards.length, 2);
  for (const guard of guards) {
    assert.ok(guard.includes("github.ref == 'refs/heads/main'"));
    assert.ok(guard.includes("github.repository == 'AyobamiH/poststeward'"));
    assert.ok(guard.includes("(inputs.environment == 'staging' || (inputs.environment == 'production' && github.event_name == 'workflow_dispatch'))"));
  }
});

test("deployment still verifies before entering the protected environment", () => {
  const verification = deploy.split("  verify:\n")[1].split("  deploy:\n")[0];
  assert.ok(verification.includes("run: npm run verify"));
  assert.ok(verification.includes("npm audit --omit=dev --audit-level=high"));
  assert.doesNotMatch(verification, /secrets\.|environment:/);
  assert.ok(deploy.includes("  deploy:\n    needs: verify\n"));
  assert.ok(deploy.includes("environment:\n      name: ${{ inputs.environment }}"));
  assert.ok(deploy.includes("npm ci --ignore-scripts"));
  assert.ok(deploy.includes("run: node scripts/smoke.mjs"));
  assert.doesNotMatch(deploy, /contents: write|actions: write|secrets: inherit/);
});

test("provider application identifiers remain variables while provider secrets enter only the deploy step", () => {
  for (const name of ["X", "THREADS", "LINKEDIN"]) {
    assert.match(deploy, new RegExp(`${name}_OAUTH_CLIENT_ID: \\\${{ vars\\.${name}_OAUTH_CLIENT_ID }}`));
    assert.match(deploy, new RegExp(`${name}_OAUTH_CLIENT_SECRET: \\\${{ secrets\\.${name}_OAUTH_CLIENT_SECRET }}`));
  }
  const verification = deploy.split("  verify:\n")[1].split("  deploy:\n")[0];
  assert.doesNotMatch(verification, /OAUTH_CLIENT_SECRET/);
});
