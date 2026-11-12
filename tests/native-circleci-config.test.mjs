import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const workflow = readFileSync(new URL("../.circleci/config.yml", import.meta.url), "utf8");

test("native macOS acceptance uses executable shell here-documents", () => {
  assert.doesNotMatch(workflow, /\\<<['"]?PYMAC\b/, "Backslash-escaped << is not a shell here-document");
  assert.equal((workflow.match(/<<'PYMAC'/g) ?? []).length, 4);
});

test("native macOS acceptance exercises real isolated launchd control-plane lifecycle", () => {
  assert.match(workflow, /test_native_launchd_cycle\.py/);
  assert.match(workflow, /Attest genuine Apple Silicon and exact source/);
  assert.match(workflow, /--no-onboard/);
});
