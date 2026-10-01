import test from "node:test";
import assert from "node:assert/strict";
import { workspaceRoute } from "../public/workspace-layout.js";

test("legacy task links select their focused view and publishing step", () => {
  for (const [hash, view, step] of [
    ["", "overview"],
    ["#destinations", "accounts"],
    ["#publishing", "publishing", "destination"],
    ["#project", "publishing", "destination"],
    ["#release-preparation", "publishing", "prepare"],
    ["#campaign", "publishing", "review"],
    ["#delivery", "publishing", "schedule"],
    ["#evidence-panel", "results"],
    ["#sources", "sources"],
    ["#preparation-model", "sources"],
    ["#agent-access", "agents"],
    ["#advanced", "billing"],
    ["#profile", "automation"],
    ["#runtime-authority-panel", "runtime"],
    ["#runtime-pairing-panel", "runtime"],
    ["#recovery-safety", "recovery"],
  ]) {
    assert.equal(workspaceRoute(hash).view, view, hash);
    assert.equal(workspaceRoute(hash).step, step, hash);
  }
});

test("unknown and inherited property names cannot select unintended views", () => {
  for (const hash of [
    "#constructor",
    "#__proto__",
    "#toString",
    "#unknown",
    "#main-content",
  ])
    assert.deepEqual(workspaceRoute(hash), { view: "overview" });
});
