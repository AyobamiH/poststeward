import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const wrangler = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
const canary = readFileSync("src/canary-edge.ts", "utf8");
const presented = readFileSync("src/presented-edge.ts", "utf8");

test("presentation entrypoint delegates to the unchanged canary authority", () => {
  assert.equal(wrangler.main, "src/presented-edge.ts");
  assert.match(presented, /import handler, \{ Workspace \} from "\.\/canary-edge\.ts"/);
  assert.match(presented, /handler\.fetch\(request, env, ctx\)/);
  assert.match(presented, /presentBrowserResponse\(request, response, env\)/);
  assert.match(presented, /handler\.scheduled\(controller, env, ctx\)/);
  assert.match(presented, /export \{ Workspace \}/);
});

test("canary workspace still scopes Advanced without mutating the shared environment", () => {
  assert.match(canary, /new Proxy\(env/);
  assert.match(canary, /property !== "ADVANCED_ENABLED"/);
  assert.match(canary, /target\.ADVANCED_ENABLED !== "true"/);
  assert.match(canary, /advancedRolloutDecision\(target, workspace\)\.eligible/);
  assert.match(canary, /super\(ctx, workspaceEnvironment\(ctx, env\)\)/);
  assert.doesNotMatch(canary, /target\.ADVANCED_ENABLED\s*=/);
});

test("rollout defaults remain a complete kill switch", () => {
  assert.equal(wrangler.vars.ADVANCED_ENABLED, "false");
  assert.equal(wrangler.vars.ADVANCED_ROLLOUT_MODE, "disabled");
  assert.equal(wrangler.vars.ADVANCED_CANARY_BPS, "0");
  assert.equal(wrangler.vars.ADVANCED_CANARY_SEED, "");
});
