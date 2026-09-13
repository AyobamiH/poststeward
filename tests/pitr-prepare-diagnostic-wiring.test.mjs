import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const request = readFileSync(
  ".github/workflows/deploy-staging-request.yml",
  "utf8",
);
const deploy = readFileSync(".github/workflows/deploy.yml", "utf8");
const diagnostic = readFileSync(
  "scripts/staging-pitr-prepare-diagnostic.mjs",
  "utf8",
);

test("PITR diagnostic remains explicit opt-in and the normal staging request keeps it disabled", () => {
  assert.match(
    request,
    /with:\n      environment: staging\n      run_pitr_diagnostic: false\n/,
  );
  assert.match(
    deploy,
    /if: inputs\.environment == 'staging' && inputs\.run_pitr_diagnostic == true/,
  );
  assert.match(deploy, /run: node scripts\/staging-pitr-prepare-diagnostic\.mjs/);
});

test("automatic PITR diagnostic can prepare, cancel and erase but can never execute restore", () => {
  assert.match(diagnostic, /"\/api\/recovery\/prepare"/);
  assert.match(diagnostic, /"\/api\/recovery\/cancel"/);
  assert.match(diagnostic, /"\/api\/lifecycle\/delete"/);
  assert.doesNotMatch(diagnostic, /\/api\/recovery\/execute/);
  assert.doesNotMatch(diagnostic, /RESTORE \$\{workspace\}/);
  assert.match(diagnostic, /restoreExecuted: false/);
  assert.match(diagnostic, /providerEffectAttempted: false/);
  assert.match(diagnostic, /paymentAttempted: false/);
});

test("automatic PITR diagnostic accepts only bounded primitive failure codes", () => {
  for (const code of [
    "RECOVERY_PITR_SYNC_FAILED",
    "RECOVERY_PITR_CURRENT_BOOKMARK_FAILED",
    "RECOVERY_PITR_TARGET_BOOKMARK_FAILED",
    "RECOVERY_PITR_UNAVAILABLE",
  ])
    assert.ok(diagnostic.includes(`"${code}"`));
  assert.match(
    diagnostic,
    /!status\?\.plan && status\?\.control\?\.quarantined === false/,
  );
});
