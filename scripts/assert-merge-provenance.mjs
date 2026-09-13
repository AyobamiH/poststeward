export async function assertMergeProvenance(env = process.env, send = fetch) {
  if (env.REQUIRE_MERGED_PR !== "true") return { required: false };
  const repository = env.GITHUB_REPOSITORY || "";
  const sha = env.GITHUB_SHA || "";
  const token = env.GITHUB_TOKEN || "";
  if (
    !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) ||
    !/^[a-f0-9]{40}$/.test(sha) ||
    token.length < 10
  )
    throw new Error("Merged-PR provenance inputs are incomplete.");
  let response;
  try {
    response = await send(
      `https://api.github.com/repos/${repository}/commits/${sha}/pulls`,
      {
        redirect: "manual",
        signal: AbortSignal.timeout(10000),
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${token}`,
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "poststeward-release-gate",
        },
      },
    );
  } catch {
    throw new Error(
      "Merged-PR provenance could not be verified; deployment is blocked.",
    );
  }
  if (!response.ok) {
    void response.body?.cancel().catch(() => {});
    throw new Error(
      `Merged-PR provenance API returned HTTP ${response.status}; deployment is blocked.`,
    );
  }
  const rows = await response.json();
  if (!Array.isArray(rows))
    throw new Error(
      "Merged-PR provenance response was invalid; deployment is blocked.",
    );
  const match = rows.find(
    (pr) =>
      pr?.merged_at && pr?.base?.ref === "main" && pr?.merge_commit_sha === sha,
  );
  if (!match)
    throw new Error(
      "Staging deployment requires this exact main revision to be the merge commit of a reviewed pull request.",
    );
  return { required: true, pullRequest: match.number, sha };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const result = await assertMergeProvenance();
  console.log(`POSTSTEWARD_MERGE_PROVENANCE ${JSON.stringify(result)}`);
}
