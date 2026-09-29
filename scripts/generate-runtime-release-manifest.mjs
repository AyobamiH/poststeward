#!/usr/bin/env node
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join, relative, resolve } from "node:path";

const sha40 = /^[0-9a-f]{40}$/;
const root = resolve(".");
const runtime = join(root, "runtime");
const wrangler = JSON.parse(readFileSync(join(root, "wrangler.jsonc"), "utf8"));

function releaseSha() {
  const candidates = [
    process.env.POSTSTEWARD_RELEASE_SHA,
    wrangler?.vars?.RELEASE_SHA,
    process.env.GITHUB_SHA,
  ];
  for (const candidate of candidates) {
    const value = String(candidate || "").trim().toLowerCase();
    if (sha40.test(value)) return value;
  }
  try {
    const value = execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim().toLowerCase();
    if (sha40.test(value)) return value;
  } catch {}
  throw new Error("Cannot resolve one exact PostSteward release SHA.");
}

function filesUnder(dir) {
  const rows = [];
  function walk(current) {
    for (const name of readdirSync(current).sort()) {
      if (["__pycache__", ".pytest_cache", ".git"].includes(name)) continue;
      const path = join(current, name);
      const stat = statSync(path, { throwIfNoEntry: true });
      if (stat.isDirectory()) walk(path);
      else if (stat.isFile() && !name.endsWith(".pyc")) rows.push(path);
      else throw new Error(`Unsupported runtime filesystem entry: ${path}`);
    }
  }
  walk(dir);
  return rows;
}

function treeDigest(dir) {
  const digest = createHash("sha256");
  for (const path of filesUnder(dir)) {
    const content = readFileSync(path);
    const fileHash = createHash("sha256").update(content).digest("hex");
    const name = relative(dir, path).replaceAll("\\", "/");
    digest.update(name, "utf8");
    digest.update("\0");
    digest.update(String(content.length), "utf8");
    digest.update("\0");
    digest.update(fileHash, "ascii");
    digest.update("\n");
  }
  return digest.digest("hex");
}

const revision = releaseSha();
const provenance = JSON.parse(
  readFileSync(join(runtime, "POSTSTEWARD_RUNTIME_PROVENANCE.json"), "utf8"),
);
const channel = process.env.POSTSTEWARD_RELEASE_CHANNEL ||
  (wrangler?.vars?.DEPLOY_ENV === "staging" ? "beta" : "stable");
if (!["stable", "beta"].includes(channel))
  throw new Error("POSTSTEWARD_RELEASE_CHANNEL must be stable or beta.");

const generated = process.env.SOURCE_DATE_EPOCH
  ? new Date(Number(process.env.SOURCE_DATE_EPOCH) * 1000)
  : new Date();
if (!Number.isFinite(generated.getTime()))
  throw new Error("SOURCE_DATE_EPOCH is invalid.");
const expires = new Date(generated.getTime() + 30 * 86400_000);

const manifest = {
  schema_version: 1,
  product: "poststeward",
  channel,
  revision,
  runtime_version: "0.28.29",
  runtime_tree_sha256: treeDigest(runtime),
  runtime_source_repository: provenance.source_repository,
  runtime_source_revision: provenance.source_revision,
  runtime_source_tree: provenance.source_tree,
  generated_at: generated.toISOString(),
  expires_at: expires.toISOString(),
  archive: `https://github.com/AyobamiH/poststeward/archive/${revision}.tar.gz`,
  boundary:
    "Release-channel metadata pins one exact repository revision and embedded-runtime tree digest. It is TUF-informed metadata, not a full TUF implementation.",
};

const out = join(root, "public", "releases");
mkdirSync(out, { recursive: true });
writeFileSync(join(out, `${channel}.json`), JSON.stringify(manifest, null, 2) + "\n");
console.log(
  `POSTSTEWARD_RELEASE_MANIFEST channel=${channel} revision=${revision} runtime_tree_sha256=${manifest.runtime_tree_sha256}`,
);
