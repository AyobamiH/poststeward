import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const workflowPaths = [
  ".github/workflows/deploy.yml",
  ".github/workflows/deploy-staging-request.yml",
  ".github/workflows/staging-external-preflight.yml",
  ".github/workflows/staging-disposable-pitr-erasure.yml",
];

const workflows = Object.fromEntries(
  workflowPaths.map(path => [path, readFileSync(path, "utf8")]),
);

test("GitHub App protected storage never uses GitHub's reserved GITHUB_ prefix", () => {
  for (const [path, source] of Object.entries(workflows)) {
    assert.doesNotMatch(
      source,
      /\$\{\{\s*vars\.GITHUB_APP_/,
      `${path} must not read a protected variable whose name starts GITHUB_`,
    );
    assert.doesNotMatch(
      source,
      /\$\{\{\s*secrets\.GITHUB_APP_/,
      `${path} must not read a protected secret whose name starts GITHUB_`,
    );
  }
});

test("protected names are mapped back to the existing runtime contract", () => {
  const deploy = workflows[".github/workflows/deploy.yml"];
  assert.match(
    deploy,
    /GITHUB_APP_CLIENT_ID: \$\{\{ vars\.POSTSTEWARD_GITHUB_APP_CLIENT_ID \}\}/,
  );
  assert.match(
    deploy,
    /GITHUB_APP_SLUG: \$\{\{ vars\.POSTSTEWARD_GITHUB_APP_SLUG \}\}/,
  );
  assert.match(
    deploy,
    /GITHUB_APP_CLIENT_SECRET: \$\{\{ secrets\.POSTSTEWARD_GITHUB_APP_CLIENT_SECRET \}\}/,
  );
});
