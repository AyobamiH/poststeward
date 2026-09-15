import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { evaluateRulesets } from "../scripts/github-main-protection-check.mjs";
import { reconcileMainRuleset } from "../scripts/github-main-protection-apply.mjs";

const canonical = JSON.parse(
  readFileSync(".github/rulesets/main-protection.json", "utf8"),
);
const token = "github-test-token-" + "x".repeat(20);

function installed(overrides = {}) {
  return {
    id: 42,
    ...structuredClone(canonical),
    ...overrides,
  };
}

test("canonical ruleset is ready and pins the observed GitHub Actions check", () => {
  const result = evaluateRulesets([installed()]);
  assert.equal(result.ready, true);
  assert.equal(result.requiredCheckContext, "verify");
  assert.equal(result.requiredCheckIntegrationId, 15368);
  assert.equal(result.reviewApprovalsRequired, 0);
  assert.equal(result.signedCommitsRequired, true);
  assert.equal(result.linearHistoryRequired, true);
});

test("wrong check case, direct bypass or invented self-review all fail closed", () => {
  for (const mutation of [
    (value) => {
      value.rules.find((r) => r.type === "required_status_checks").parameters.required_status_checks[0].context = "Verify";
    },
    (value) => {
      value.bypass_actors = [
        { actor_id: 5, actor_type: "RepositoryRole", bypass_mode: "always" },
      ];
    },
    (value) => {
      value.rules.find((r) => r.type === "pull_request").parameters.required_approving_review_count = 1;
    },
    (value) => {
      value.rules = value.rules.filter((r) => r.type !== "required_signatures");
    },
  ]) {
    const value = installed();
    mutation(value);
    assert.equal(evaluateRulesets([value]).ready, false);
  }
});

test("ruleset apply refuses mutation without exact confirmation", async () => {
  await assert.rejects(
    reconcileMainRuleset({
      repository: "AyobamiH/poststeward",
      token,
      apply: "",
      send: async (path) => (path.includes("rulesets?") ? [] : null),
    }),
    /Refusing repository-admin mutation/,
  );
});

test("ruleset apply creates exactly the reviewed canonical policy and verifies response", async () => {
  const calls = [];
  const result = await reconcileMainRuleset({
    repository: "AyobamiH/poststeward",
    token,
    apply: "APPLY_POSTSTEWARD_MAIN_RULESET",
    send: async (path, _token, init = {}) => {
      calls.push({ path, method: init.method || "GET", body: init.body });
      if (path.includes("rulesets?")) return [];
      assert.equal(init.method, "POST");
      const body = JSON.parse(init.body);
      assert.deepEqual(body, canonical);
      return { id: 77, ...body };
    },
  });
  assert.deepEqual(result, {
    changed: true,
    ready: true,
    action: "created",
    rulesetId: 77,
    requiredCheck: "verify",
    requiredCheckIntegrationId: 15368,
    approvalsRequired: 0,
  });
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
});

test("already-correct policy performs no mutation", async () => {
  const current = installed();
  let mutations = 0;
  const result = await reconcileMainRuleset({
    repository: "AyobamiH/poststeward",
    token,
    apply: "APPLY_POSTSTEWARD_MAIN_RULESET",
    send: async (path, _token, init = {}) => {
      if (init.method) mutations++;
      if (path.includes("rulesets?")) return [{ id: 42, name: canonical.name }];
      return current;
    },
  });
  assert.equal(result.changed, false);
  assert.equal(result.ready, true);
  assert.equal(mutations, 0);
});
