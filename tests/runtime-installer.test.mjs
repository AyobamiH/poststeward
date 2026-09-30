import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test, { after, before } from "node:test";

// Exercise the real installer and adopted runtime. Only HTTPS transport is
// replaced: fixtures are a Git archive of this exact head and its generated
// channel metadata. No pairing, provider writes, or user timers are started.
let fixture, revision, archive, manifest;
const installer = resolve("public/install.sh");
before(() => {
  fixture = mkdtempSync(join(tmpdir(), "poststeward-installer-fixture-"));
  revision = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
  archive = join(fixture, "release.tar.gz");
  execFileSync("git", ["archive", "--format=tar.gz", `--prefix=poststeward-${revision}/`, "-o", archive,
    revision, "runtime", "wrangler.jsonc", "scripts/generate-runtime-release-manifest.mjs"]);
  execFileSync("tar", ["-xzf", archive, "-C", fixture]);
  const root = join(fixture, `poststeward-${revision}`);
  execFileSync(process.execPath, ["scripts/generate-runtime-release-manifest.mjs"], {
    cwd: root, env: { ...process.env, POSTSTEWARD_RELEASE_SHA: revision, POSTSTEWARD_RELEASE_CHANNEL: "stable" },
  });
  manifest = JSON.parse(readFileSync(join(root, "public/releases/stable.json"), "utf8"));
});
after(() => rmSync(fixture, { recursive: true, force: true }));

function machine(t, metadata = manifest) {
  const root = mkdtempSync(join(tmpdir(), "poststeward-clean-machine-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const tools = join(root, "tools");
  mkdirSync(tools);
  const transport = join(tools, "curl");
  writeFileSync(transport, `#!/usr/bin/env python3
import json, os, shutil, sys
args = sys.argv[1:]
url = args[args.index('-o') - 1]
target = args[args.index('-o') + 1]
if url == 'https://poststeward.example/releases/stable.json':
    shutil.copyfile(os.environ['FIXTURE_MANIFEST'], target)
elif url == 'https://github.com/AyobamiH/poststeward/archive/' + os.environ['FIXTURE_REVISION'] + '.tar.gz':
    shutil.copyfile(os.environ['FIXTURE_ARCHIVE'], target)
else:
    sys.exit('Unexpected network request: ' + url)
with open(os.environ['FIXTURE_REQUESTS'], 'a') as log:
    log.write(url + '\\n')
`);
  chmodSync(transport, 0o755);
  const metadataPath = join(root, "manifest.json");
  writeFileSync(metadataPath, JSON.stringify(metadata));
  const prefix = join(root, "data/poststeward");
  const bin = join(root, "bin");
  const env = { ...process.env, PATH: tools + ":" + process.env.PATH,
    XDG_CONFIG_HOME: join(root, "config"), XDG_STATE_HOME: join(root, "state"), XDG_DATA_HOME: join(root, "data"),
    POSTSTEWARD_INSTALL_PREFIX: prefix, POSTSTEWARD_BIN_DIR: bin, POSTSTEWARD_ORIGIN: "https://poststeward.example",
    FIXTURE_MANIFEST: metadataPath, FIXTURE_REVISION: revision, FIXTURE_ARCHIVE: archive,
    FIXTURE_REQUESTS: join(root, "requests.txt"),
  };
  for (const key of Object.keys(env)) {
    if (/^(OCPF_POST_|POST_ONCE_|POSTSTEWARD_RUNTIME_|POSTSTEWARD_SETUP_|POSTSTEWARD_RELEASES_)/.test(key)) delete env[key];
  }
  const run = (...args) => spawnSync("bash", [installer, ...args, "--no-onboard"], { env, encoding: "utf8", timeout: 30000 });
  return { root, prefix, bin, env, run, receipt: join(root, "state/poststeward/install.json") };
}

test("installer dry run resolves a pinned channel without creating an installation", (t) => {
  const m = machine(t);
  const result = m.run("--dry-run");
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, new RegExp(revision));
  assert.equal(existsSync(m.prefix), false);
  assert.equal(existsSync(m.bin), false);
  assert.equal(existsSync(m.receipt), false);
});

test("fresh channel install runs the full CLI and records exact runtime provenance", (t) => {
  const m = machine(t);
  const result = m.run();
  assert.equal(result.status, 0, result.stderr);
  const receipt = JSON.parse(readFileSync(m.receipt, "utf8"));
  assert.equal(receipt.resolved_revision, revision);
  assert.equal(receipt.runtime_tree_sha256, manifest.runtime_tree_sha256);
  assert.equal(receipt.runtime_provenance.source_revision, manifest.runtime_source_revision);
  assert.equal(receipt.runtime_provenance.original_post_once_mutation_allowed, false);
  assert.equal(statSync(m.receipt).mode & 0o777, 0o600);
  const cli = join(m.bin, "poststeward");
  const help = spawnSync(cli, ["help", "--json"], { env: m.env, encoding: "utf8", timeout: 30000 });
  assert.equal(help.status, 0, help.stderr);
  assert.ok(JSON.parse(help.stdout).commands.length);
  const doctor = spawnSync(cli, ["doctor", "--json"], { env: m.env, encoding: "utf8", timeout: 30000 });
  assert.equal(doctor.status, 0, doctor.stderr || doctor.stdout);
  assert.equal(JSON.parse(doctor.stdout).status, "ATTENTION");
  assert.equal(existsSync(join(m.root, "state/poststeward/runtime/publish-receipts.jsonl")), false);
});

test("exact revision reinstall retains local state and downloads the archive only once", (t) => {
  const m = machine(t);
  assert.equal(m.run("--version", revision).status, 0);
  const state = join(m.root, "state/poststeward/owner-state.json");
  writeFileSync(state, '{"retained":true}\n');
  const result = m.run("--version", revision);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(readFileSync(state, "utf8"), '{"retained":true}\n');
  assert.equal(readFileSync(m.env.FIXTURE_REQUESTS, "utf8").trim().split("\n").length, 1);
});

test("installer refuses to replace an unrelated command", (t) => {
  const m = machine(t);
  mkdirSync(m.bin);
  const command = join(m.bin, "poststeward");
  writeFileSync(command, "unrelated command\n");
  const result = m.run("--version", revision);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /refusing to overwrite unrelated command/);
  assert.equal(readFileSync(command, "utf8"), "unrelated command\n");
  assert.equal(existsSync(m.receipt), false);
});

test("installer rejects expired channel metadata before staging a release", (t) => {
  const m = machine(t, { ...manifest, expires_at: "2000-01-01T00:00:00Z" });
  const result = m.run();
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /release manifest expired/);
  assert.equal(existsSync(m.prefix), false);
});

test("installer rejects a runtime digest mismatch before promoting a release", (t) => {
  const m = machine(t, { ...manifest, runtime_tree_sha256: "0".repeat(64) });
  const result = m.run();
  assert.equal(result.status, 2);
  assert.match(result.stderr, /does not match/);
  assert.equal(existsSync(join(m.prefix, "current")), false);
  assert.equal(existsSync(m.receipt), false);
});

test("channel reinstall detects alteration of a retained runtime", (t) => {
  const m = machine(t);
  assert.equal(m.run().status, 0);
  writeFileSync(join(m.prefix, "releases", revision, "src/ocpf_post/__init__.py"), "altered\n");
  const result = m.run();
  assert.equal(result.status, 2);
  assert.match(result.stderr, /existing embedded runtime does not match/);
});
