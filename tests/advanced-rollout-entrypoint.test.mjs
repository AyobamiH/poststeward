import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const wrangler = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
const entry = readFileSync("src/canary-edge.ts", "utf8");

test("Worker entrypoint scopes Advanced without mutating the shared environment", () => {
  assert.equal(wrangler.main, "src/canary-edge.ts");
  assert.match(entry, /new Proxy\(env/);
  assert.match(entry, /property !== "ADVANCED_ENABLED"/);
  assert.match(entry, /target\.ADVANCED_ENABLED !== "true"/);
  assert.match(entry, /advancedRolloutDecision\(target, workspace\)\.eligible/);
  assert.doesNotMatch(entry, /target\.ADVANCED_ENABLED\s*=/);
});

test("rollout defaults remain a complete kill switch", () => {
  assert.equal(wrangler.vars.ADVANCED_ENABLED, "false");
  assert.equal(wrangler.vars.ADVANCED_ROLLOUT_MODE, "disabled");
  assert.equal(wrangler.vars.ADVANCED_CANARY_BPS, "0");
  assert.equal(wrangler.vars.ADVANCED_CANARY_SEED, "");
});
