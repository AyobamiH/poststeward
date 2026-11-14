import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const workflow = readFileSync(
  new URL("../.github/workflows/release-promotion-controller.yml", import.meta.url),
  "utf8",
);

test("scheduled promotion reporting uses the existing self-hosted verification fleet", () => {
  assert.match(workflow, /^    runs-on: poststeward-verification$/m);
  assert.doesNotMatch(workflow, /^    runs-on: (?:ubuntu|windows|macos)-(?:latest|\d+).*$/m);
  assert.match(workflow, /^    - cron: "23 6 \* \* \*"$/m);
  assert.match(workflow, /^  workflow_dispatch:$/m);
});

test("promotion observation remains read-only and bound to protected main", () => {
  assert.match(workflow, /^permissions:\n  contents: read$/m);
  assert.match(workflow, /github\.repository == 'AyobamiH\/poststeward' && github\.ref == 'refs\/heads\/main'/);
  assert.match(workflow, /^          POSTSTEWARD_PROMOTION_MODE: report$/m);
  assert.match(workflow, /^          POSTSTEWARD_PROMOTION_TARGET:/m);
  assert.match(workflow, /^        run: node scripts\/release-promotion-controller\.mjs$/m);
  assert.doesNotMatch(workflow, /\b(?:wrangler deploy|migrations apply|gh workflow run|npm run deploy)\b/);
});
