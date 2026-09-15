import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const canonicalRuleset = JSON.parse(
  readFileSync(new URL("../.github/rulesets/main-protection.json", import.meta.url), "utf8"),
);

function appliesToMain(ruleset) {
  if (ruleset.target && ruleset.target !== "branch") return false;
  const include = ruleset.conditions?.ref_name?.include || [];
  return include.some((value) =>
    ["~DEFAULT_BRANCH", "refs/heads/main", "main"].includes(String(value)),
  );
}

function ruleOf(ruleset, type) {
  return (ruleset.rules || []).find((rule) => rule.type === type);
}

export function evaluateRulesets(rulesets, expected = canonicalRuleset) {
  const candidates = rulesets.filter(
    (ruleset) =>
      ruleset.name === expected.name &&
      ruleset.enforcement === "active" &&
      appliesToMain(ruleset),
  );
  const ruleset = candidates.length === 1 ? candidates[0] : undefined;
  const rules = new Set((ruleset?.rules || []).map((rule) => rule.type));
  const pullRequest = ruleOf(ruleset || {}, "pull_request")?.parameters || {};
  const status = ruleOf(ruleset || {}, "required_status_checks")?.parameters || {};
  const expectedStatus = ruleOf(expected, "required_status_checks")?.parameters
    ?.required_status_checks?.[0];
  const matchingCheck = (status.required_status_checks || []).find(
    (check) =>
      check.context === expectedStatus?.context &&
      Number(check.integration_id || 0) === Number(expectedStatus?.integration_id || 0),
  );
  const bypassActors = ruleset?.bypass_actors || [];
  const directBypass = bypassActors.filter(
    (actor) => actor.bypass_mode !== "pull_request",
  );

  const result = {
    canonicalRulesetMatches: candidates.length === 1,
    activeRulesetsForMain: rulesets.filter(
      (item) => item.enforcement === "active" && appliesToMain(item),
    ).length,
    pullRequestRequired: rules.has("pull_request"),
    deletionBlocked: rules.has("deletion"),
    forcePushBlocked: rules.has("non_fast_forward"),
    signedCommitsRequired: rules.has("required_signatures"),
    linearHistoryRequired: rules.has("required_linear_history"),
    requiredCheckPresent: Boolean(matchingCheck),
    requiredCheckContext: expectedStatus?.context || null,
    requiredCheckIntegrationId: expectedStatus?.integration_id || null,
    strictStatusChecks: status.strict_required_status_checks_policy === true,
    reviewApprovalsRequired:
      Number(pullRequest.required_approving_review_count || 0),
    reviewThreadResolutionRequired:
      pullRequest.required_review_thread_resolution === true,
    lastPushApprovalRequired: pullRequest.require_last_push_approval === true,
    allowedMergeMethods: Array.isArray(pullRequest.allowed_merge_methods)
      ? [...pullRequest.allowed_merge_methods].sort()
      : [],
    directBypassActors: directBypass.length,
  };

  return {
    ...result,
    ready:
      result.canonicalRulesetMatches &&
      result.pullRequestRequired &&
      result.deletionBlocked &&
      result.forcePushBlocked &&
      result.signedCommitsRequired &&
      result.linearHistoryRequired &&
      result.requiredCheckPresent &&
      result.strictStatusChecks &&
      result.reviewApprovalsRequired === 0 &&
      result.reviewThreadResolutionRequired &&
      result.lastPushApprovalRequired === false &&
      result.allowedMergeMethods.length === 1 &&
      result.allowedMergeMethods[0] === "squash" &&
      result.directBypassActors === 0,
  };
}

async function github(path, token) {
  const response = await fetch(`https://api.github.com${path}`, {
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2026-03-10",
      "User-Agent": "poststeward-governance-check",
    },
    redirect: "error",
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok)
    throw new Error(`GitHub governance query failed with HTTP ${response.status}.`);
  return response.json();
}

export async function inspectMainProtection(repository, token) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository || ""))
    throw new Error("GITHUB_REPOSITORY must be owner/name.");
  if (!token || token.length < 20)
    throw new Error(
      "GITHUB_TOKEN with repository Administration read access is required.",
    );
  const [owner, name] = repository.split("/");
  const listed = await github(
    `/repos/${owner}/${name}/rulesets?includes_parents=true`,
    token,
  );
  const details = [];
  for (const item of listed) {
    const detail = await github(`/repos/${owner}/${name}/rulesets/${item.id}`, token);
    details.push(detail);
  }
  return evaluateRulesets(details);
}

async function main() {
  const result = await inspectMainProtection(
    process.env.GITHUB_REPOSITORY || "AyobamiH/poststeward",
    process.env.GITHUB_TOKEN || "",
  );
  console.log(JSON.stringify(result, null, 2));
  if (!result.ready) process.exitCode = 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href)
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
