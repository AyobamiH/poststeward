/** Create only a NEW private controlled fixture repo under the authenticated owner. */
import { readFile, writeFile, chmod } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { join, resolve } from "node:path";
const [directory, name] = process.argv.slice(2);
if (!directory || !/^poststeward-acceptance-[a-z0-9-]+$/.test(name || ""))
  throw new Error(
    "Use PRIVATE_BUNDLE poststeward-acceptance-NAME. No existing/shared repository is changed.",
  );
const root = resolve(directory),
  journalPath = join(root, "source-provisioning.json");
const call = (path, method = "GET", data) => {
  try {
    return JSON.parse(
      execFileSync(
        "gh",
        ["api", "--method", method, path, ...(data ? ["--input", "-"] : [])],
        {
          input: data ? JSON.stringify(data) : undefined,
          encoding: "utf8",
          stdio: ["pipe", "pipe", "pipe"],
          maxBuffer: 1048576,
        },
      ),
    );
  } catch {
    throw new Error(
      "GitHub fixture operation failed. Inspect the private write-ahead journal before retrying; no shared repo changes or automatic replay.",
    );
  }
};
const save = async (value) => {
  await writeFile(journalPath, JSON.stringify(value, null, 2) + "\n", {
    mode: 0o600,
  });
  await chmod(journalPath, 0o600);
};
try {
  await readFile(journalPath);
  throw new Error(
    "A provisioning checkpoint already exists. Inspect it; this command will not replay remote mutations.",
  );
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const user = call("user");
const repo = user.login + "/" + name;
const planPath = join(root, "plan.json"),
  plan = JSON.parse(await readFile(planPath, "utf8"));
if (plan.sourceRepository)
  throw new Error("Bundle already binds a source; preserve it.");
const cases = JSON.parse(await readFile(join(root, "fixtures.json"), "utf8"));
const journal = {
  repository: repo,
  visibility: "private",
  status: "creating",
  fixtures: [],
  customerModelRequests: 0,
  publicPosts: 0,
};
await save(journal);
const created = call("user/repos", "POST", {
  name,
  private: true,
  description:
    "Private PostSteward acceptance fixtures. Authored test material, not production capabilities.",
  has_issues: false,
  has_projects: false,
  has_wiki: false,
  auto_init: false,
});
if (created.private !== true || created.full_name !== repo)
  throw new Error("Unexpected fixture resource identity; stop and inspect.");
journal.repositoryId = created.id;
journal.status = "provisioning releases";
await save(journal);
let contentSha;
for (const item of cases) {
  const evidence =
    "# Acceptance fixture: " +
    item.id +
    "\n\nAuthored synthetic acceptance material; this does not describe a deployed PostSteward capability.\n\n" +
    item.source +
    "\n\nAudience: " +
    item.audience +
    "\nObjective: " +
    item.objective +
    "\n";
  const commit = call(`repos/${repo}/contents/release-evidence.md`, "PUT", {
    message: `Add controlled ${item.id} acceptance evidence`,
    content: Buffer.from(evidence).toString("base64"),
    ...(contentSha ? { sha: contentSha } : {}),
  });
  contentSha = commit.content.sha;
  const release = call(`repos/${repo}/releases`, "POST", {
    tag_name: "acceptance-" + item.id,
    target_commitish: commit.commit.sha,
    name: "Acceptance fixture — " + item.id,
    body: evidence,
    draft: false,
    prerelease: false,
    make_latest: "false",
  });
  journal.fixtures.push({
    case: item.id,
    revision: commit.commit.sha,
    tag: release.tag_name,
    releaseId: release.id,
    url: release.html_url,
  });
  await save(journal);
}
journal.status =
  "ready; private GitHub App access still requires owner selection";
await save(journal);
plan.sourceRepository = repo;
plan.sourcePrivacy = "private";
await writeFile(planPath, JSON.stringify(plan, null, 2) + "\n", {
  mode: 0o600,
});
await chmod(planPath, 0o600);
console.log(JSON.stringify(journal));
