import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { verifyDeployProvenance } from "../scripts/verify-deploy-provenance.mjs";

const repository = "AyobamiH/poststeward";
const sha = "a".repeat(40);
const token = "test-read-only-token";
function response(value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}
function transport({ parents = 2, pr = true, committer = "GitHub", status = 200 } = {}) {
  return async (url, init) => {
    assert.equal(init.redirect, "manual");
    assert.match(init.headers.Authorization, /^Bearer /);
    const path = new URL(url).pathname;
    if (status !== 200) return response({}, status);
    if (path.endsWith(`/commits/${sha}`))
      return response({ parents: Array.from({ length: parents }, (_, i) => ({ sha: String(i) })), commit: { committer: { name: committer, email: committer === "GitHub" ? "noreply@github.com" : "owner@example.test" } } });
    if (path.endsWith(`/commits/${sha}/pulls`))
      return response(pr ? [{ number: 14, merged_at: "2026-09-10T09:00:00Z", merge_commit_sha: sha, base: { ref: "main", repo: { full_name: repository } }, head: { repo: { full_name: repository } } }] : []);
    throw new Error("unexpected URL");
  };
}

test("automatic staging deployment accepts only an exact same-repository merged PR commit", async () => {
  const result = await verifyDeployProvenance({ repository, sha, token, send: transport() });
  assert.equal(result.pullRequest, 14);
  for (const options of [{ parents: 1 }, { pr: false }, { committer: "Local Git" }])
    await assert.rejects(verifyDeployProvenance({ repository, sha, token, send: transport(options) }));
});

test("provenance lookup fails closed on GitHub API errors and hostile repository/SHA input", async () => {
  await assert.rejects(verifyDeployProvenance({ repository, sha, token, send: transport({ status: 503 }) }));
  await assert.rejects(verifyDeployProvenance({ repository: "attacker/repo", sha, token, send: transport() }));
  await assert.rejects(verifyDeployProvenance({ repository, sha: "main", token, send: transport() }));
});

test("staging request requires provenance before reusable deployment and has no write permissions", () => {
  const workflow = readFileSync(".github/workflows/deploy-staging-request.yml", "utf8");
  assert.match(workflow, /pull-requests: read/);
  assert.match(workflow, /Require exact merged-PR provenance/);
  assert.match(workflow, /needs: provenance/);
  assert.doesNotMatch(workflow, /contents: write|actions: write|pull-requests: write/);
});
