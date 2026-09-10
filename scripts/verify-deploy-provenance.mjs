import { demand } from "./deployment-config.mjs";

export async function verifyDeployProvenance({
  repository = process.env.GITHUB_REPOSITORY,
  sha = process.env.GITHUB_SHA,
  token = process.env.GITHUB_TOKEN,
  send = fetch,
} = {}) {
  demand(repository === "AyobamiH/poststeward", "Deployment provenance requires the canonical repository.");
  demand(/^[a-f0-9]{40}$/.test(sha || ""), "Deployment provenance requires an exact commit SHA.");
  demand(token && token.length > 10, "Deployment provenance requires the read-only GitHub workflow token.");
  const headers = {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "poststeward-deploy-provenance",
  };
  async function json(path) {
    const response = await send(`https://api.github.com/repos/${repository}${path}`, {
      headers,
      redirect: "manual",
      signal: AbortSignal.timeout(10000),
    });
    demand(response.status === 200, `GitHub provenance lookup failed with HTTP ${response.status}.`);
    return response.json();
  }
  const commit = await json(`/commits/${sha}`);
  demand(Array.isArray(commit.parents) && commit.parents.length === 2,
    "Automatic staging deployment accepts only a two-parent merge commit.");
  demand(commit.commit?.committer?.name === "GitHub" || commit.commit?.committer?.email === "noreply@github.com",
    "Automatic staging deployment requires a GitHub-created merge commit.");
  const pulls = await json(`/commits/${sha}/pulls`);
  const match = Array.isArray(pulls) && pulls.find((pr) =>
    pr?.merged_at && pr?.merge_commit_sha === sha && pr?.base?.ref === "main" &&
    pr?.base?.repo?.full_name === repository && pr?.head?.repo?.full_name === repository);
  demand(match, "Automatic staging deployment requires a merged pull request into main for this exact SHA.");
  return { repository, sha, pullRequest: match.number, mergedAt: match.merged_at };
}

if (import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  const result = await verifyDeployProvenance();
  console.log(`Deployment provenance verified for PR #${result.pullRequest} at ${result.sha}.`);
}
