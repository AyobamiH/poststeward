import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
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

test("current-main native macOS CI is manual, exact-source and non-automatic", () => {
  assert.match(workflow, /^parameters:\n  native_macos_main_acceptance:\n    type: boolean\n    default: false/m);
  assert.match(workflow, /^  native_macos_main_revision:\n    type: string\n    default: ""/m);
  assert.match(workflow, /^  native-macos-current-main-acceptance:\n    when: << pipeline\.parameters\.native_macos_main_acceptance >>\n    jobs:\n      - native-macos-local-lifecycle:\n          expected_revision: << pipeline\.parameters\.native_macos_main_revision >>\n          filters:\n            branches:\n              only:\n                - main/m);
  assert.match(workflow, /^      POSTSTEWARD_EXPECTED_MAIN_SHA: "<< parameters\.expected_revision >>"/m);
  assert.match(workflow, /test "\$CIRCLE_SHA1" = "\$POSTSTEWARD_EXPECTED_MAIN_SHA"/);
});


test("native main exact-SHA guard refuses missing, changed or malformed approvals", () => {
  const guardBlock = workflow.match(/            if \[ "\$\{CIRCLE_BRANCH:-\}" = "main" \]; then\n[\s\S]+?            fi/);
  assert.ok(guardBlock, "real CircleCI main SHA guard must exist");
  const command = guardBlock[0].split("\n").map(line => line.replace(/^ {12}/, "")).join("\n");
  const sha = "a".repeat(40);
  const run = (branch, source, expected) => spawnSync("bash", ["-euo", "pipefail", "-c", command], {
    env: { ...process.env, CIRCLE_BRANCH: branch, CIRCLE_SHA1: source, POSTSTEWARD_EXPECTED_MAIN_SHA: expected },
    encoding: "utf8", timeout: 5000,
  });
  assert.equal(run("main", sha, sha).status, 0);
  assert.notEqual(run("main", sha, "").status, 0);
  assert.notEqual(run("main", sha, "b".repeat(40)).status, 0);
  assert.notEqual(run("main", sha, "not-a-sha").status, 0);
  assert.equal(run("agent/native-customer-lifecycle-20261112", sha, "").status, 0);
  assert.notEqual(run("agent/native-customer-lifecycle-20261112", sha, sha).status, 0);
});
