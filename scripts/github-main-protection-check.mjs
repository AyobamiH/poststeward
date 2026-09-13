import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

function appliesToMain(ruleset) {
  if (ruleset.target && ruleset.target !== "branch") return false;
  const include = ruleset.conditions?.ref_name?.include || [];
  return include.some((value) =>
    ["~DEFAULT_BRANCH", "refs/heads/main", "main"].includes(String(value)),
  );
}

export function evaluateRulesets(rulesets, requiredCheck = "Verify") {
  const active = rulesets.filter(
    (ruleset) => ruleset.enforcement === "active" && appliesToMain(ruleset),
  );
  const ruleTypes = new Set(active.flatMap((ruleset) => (ruleset.rules || []).map((rule) => rule.type)));
  const statusContexts = new Set(
    active.flatMap((ruleset) =>
      (ruleset.rules || [])
        .filter((rule) => rule.type === "required_status_checks")
        .flatMap((rule) => rule.parameters?.required_status_checks || [])
        .map((check) => check.context),
    ),
  );
  const reviewRules = active
    .flatMap((ruleset) => ruleset.rules || [])
    .filter((rule) => rule.type === "pull_request");
  const reviewRequired = reviewRules.some(
    (rule) => Number(rule.parameters?.required_approving_review_count || 0) >= 1,
  );
  const result = {
    activeRulesetsForMain: active.length,
    deletionBlocked: ruleTypes.has("deletion"),
    forcePushBlocked: ruleTypes.has("non_fast_forward"),
    requiredCheckPresent: statusContexts.has(requiredCheck),
    reviewRequired,
  };
  return {
    ...result,
    ready:
      result.activeRulesetsForMain > 0 &&
      result.deletionBlocked &&
      result.forcePushBlocked &&
      result.requiredCheckPresent &&
      result.reviewRequired,
  };
}

async function github(path, token) {
  const response = await fetch(`https://api.github.com${path}`, {
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
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
    throw new Error("GITHUB_TOKEN with repository metadata/ruleset read access is required.");
  const [owner, name] = repository.split("/");
  const listed = await github(`/repos/${owner}/${name}/rulesets?includes_parents=true`, token);
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
