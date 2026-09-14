import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { evaluateRulesets } from "./github-main-protection-check.mjs";

const canonical = JSON.parse(
  readFileSync(new URL("../.github/rulesets/main-protection.json", import.meta.url), "utf8"),
);
const confirmation = "APPLY_POSTSTEWARD_MAIN_RULESET";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

async function github(path, token, init = {}) {
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2026-03-10",
      "User-Agent": "poststeward-governance-apply",
      ...(init.headers || {}),
    },
    redirect: "error",
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    await response.body?.cancel().catch(() => {});
    throw new Error(`GitHub ruleset request failed with HTTP ${response.status}.`);
  }
  if (response.status === 204) return null;
  return response.json();
}

export async function reconcileMainRuleset({
  repository,
  token,
  apply,
  send = github,
}) {
  demand(
    repository === "AyobamiH/poststeward",
    "Ruleset apply is intentionally restricted to AyobamiH/poststeward.",
  );
  demand(
    typeof token === "string" && token.length >= 20,
    "GITHUB_TOKEN with repository Administration write access is required.",
  );
  const [owner, name] = repository.split("/");
  const listed = await send(
    `/repos/${owner}/${name}/rulesets?includes_parents=false`,
    token,
  );
  const details = [];
  for (const item of listed)
    details.push(await send(`/repos/${owner}/${name}/rulesets/${item.id}`, token));

  const existing = details.filter((item) => item.name === canonical.name);
  demand(existing.length <= 1, "More than one canonical PostSteward ruleset exists; reconcile manually before mutation.");
  const current = evaluateRulesets(details);
  if (current.ready)
    return {
      changed: false,
      ready: true,
      action: "already_matches",
      rulesetId: existing[0]?.id || null,
    };

  demand(
    apply === confirmation,
    `Refusing repository-admin mutation without POSTSTEWARD_APPLY_MAIN_RULESET=${confirmation}.`,
  );

  // Never silently create a second independently active main policy. Unknown
  // rulesets can interact in non-obvious ways and must be reviewed first.
  const competing = details.filter(
    (item) =>
      item.name !== canonical.name &&
      item.enforcement === "active" &&
      (item.conditions?.ref_name?.include || []).some((value) =>
        ["~DEFAULT_BRANCH", "refs/heads/main", "main"].includes(String(value)),
      ),
  );
  demand(
    competing.length === 0,
    "Another active ruleset already targets main; inspect it before applying the canonical PostSteward policy.",
  );

  let applied;
  let action;
  if (existing.length === 1) {
    applied = await send(
      `/repos/${owner}/${name}/rulesets/${existing[0].id}`,
      token,
      { method: "PUT", body: JSON.stringify(canonical) },
    );
    action = "updated";
  } else {
    applied = await send(`/repos/${owner}/${name}/rulesets`, token, {
      method: "POST",
      body: JSON.stringify(canonical),
    });
    action = "created";
  }

  const verified = evaluateRulesets([applied]);
  demand(
    verified.ready,
    "GitHub accepted the ruleset but its returned policy does not match the reviewed canonical contract.",
  );
  return {
    changed: true,
    ready: true,
    action,
    rulesetId: applied.id,
    requiredCheck: verified.requiredCheckContext,
    requiredCheckIntegrationId: verified.requiredCheckIntegrationId,
    approvalsRequired: verified.reviewApprovalsRequired,
  };
}

async function main() {
  const result = await reconcileMainRuleset({
    repository: process.env.GITHUB_REPOSITORY || "AyobamiH/poststeward",
    token: process.env.GITHUB_TOKEN || "",
    apply: process.env.POSTSTEWARD_APPLY_MAIN_RULESET || "",
  });
  console.log("POSTSTEWARD_MAIN_RULESET " + JSON.stringify(result));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href)
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
