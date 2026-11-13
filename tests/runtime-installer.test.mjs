import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
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
if not os.path.isabs(target) or not target.startswith(os.environ['FIXTURE_TMP_PREFIX']):
    sys.exit('download staging file must be in the private temporary directory')
with open(os.environ['FIXTURE_REQUESTS'], 'a') as log:
    log.write(url + '\\n')
if os.environ.get('FIXTURE_REQUIRE_BOUNDED_TRANSPORT') == '1' and (
    '--connect-timeout' not in args or '--max-time' not in args
):
    sys.exit('Installer transport has no connect/transfer time limit')
failure = os.environ.get('FIXTURE_FETCH_FAILURE', '')
counter = os.environ['FIXTURE_FAILURE_COUNTER']
if failure:
    previous = int(open(counter).read()) if os.path.exists(counter) else 0
    with open(counter, 'w') as stream:
        stream.write(str(previous + 1))
    if failure == 'always' or failure == 'http' or previous == 0:
        with open(target, 'wb') as stream:
            stream.write(b'incomplete transit data, must be removed')
        sys.exit(22 if failure == 'http' else 28)
if url in ('https://poststeward.example/releases/stable.json', 'https://poststeward.com/releases/stable.json'):
    shutil.copyfile(os.environ['FIXTURE_MANIFEST'], target)
elif url == 'https://github.com/AyobamiH/poststeward/archive/' + os.environ['FIXTURE_REVISION'] + '.tar.gz':
    shutil.copyfile(os.environ['FIXTURE_ARCHIVE'], target)
else:
    sys.exit('Unexpected network request: ' + url)
`);
  chmodSync(transport, 0o755);
  const metadataPath = join(root, "manifest.json");
  writeFileSync(metadataPath, JSON.stringify(metadata));
  const prefix = join(root, "data/poststeward");
  const bin = join(root, "bin");
  const env = { ...process.env, PATH: tools + ":" + process.env.PATH,
    XDG_CONFIG_HOME: join(root, "config"), XDG_STATE_HOME: join(root, "state"), XDG_DATA_HOME: join(root, "data"),
    POSTSTEWARD_INSTALL_PREFIX: prefix, POSTSTEWARD_BIN_DIR: bin, POSTSTEWARD_ORIGIN: "https://poststeward.example",
    POSTSTEWARD_DISTRIBUTION_ORIGIN: "https://poststeward.example",
    FIXTURE_MANIFEST: metadataPath, FIXTURE_REVISION: revision, FIXTURE_ARCHIVE: archive,
    FIXTURE_REQUESTS: join(root, "requests.txt"), FIXTURE_FAILURE_COUNTER: join(root, "failure-counter.txt"), FIXTURE_TMP_PREFIX: process.env.TMPDIR || "/tmp",
  };
  for (const key of Object.keys(env)) {
    if (/^(OCPF_POST_|POST_ONCE_|POSTSTEWARD_RUNTIME_|POSTSTEWARD_SETUP_|POSTSTEWARD_RELEASES_)/.test(key)) delete env[key];
  }
  const run = (...args) => spawnSync("bash", [installer, ...args, "--no-onboard"], { env, encoding: "utf8", timeout: 30000 });
  return { root, prefix, bin, env, run, receipt: join(root, "state/poststeward/install.json") };
}

test("inherited Cloud origin never changes the public release distribution", (t) => {
  const m = machine(t);
  delete m.env.POSTSTEWARD_DISTRIBUTION_ORIGIN;
  m.env.POSTSTEWARD_ORIGIN = "https://app.poststeward.com";
  const result = m.run("--dry-run");
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, new RegExp(revision));
  const requests = readFileSync(m.env.FIXTURE_REQUESTS, "utf8");
  assert.match(requests, /https:\/\/poststeward\.com\/releases\/stable\.json/);
  assert.doesNotMatch(requests, /https:\/\/app\.poststeward\.com\/releases\//);
  assert.equal(existsSync(m.prefix), false);
});

// Cloud API overrides must never redirect release manifests to the OAuth host.
// The mocked transport rejects any unexpected endpoint, so this proves the
// selected distribution endpoint was actually used, not merely parsed.
for (const explicitDistribution of [true, false]) {
  test(`Cloud CLI --origin preserves ${explicitDistribution ? "explicit" : "default"} distribution`, (t) => {
    const m = machine(t);
    if (!explicitDistribution) delete m.env.POSTSTEWARD_DISTRIBUTION_ORIGIN;
    const distribution = explicitDistribution ? "https://poststeward.example" : "https://poststeward.com";
    const cloud = "https://customer-cloud.example";
    const preview = m.run("--origin", cloud, "--dry-run");
    assert.equal(preview.status, 0, preview.stderr);
    assert.equal(readFileSync(m.env.FIXTURE_REQUESTS, "utf8").trim(),
      `${distribution}/releases/stable.json`);
    assert.equal(existsSync(m.prefix), false, "dry run must not write installed state");
    const install = m.run("--origin", cloud);
    assert.equal(install.status, 0, install.stderr);
    assert.equal(JSON.parse(readFileSync(m.receipt, "utf8")).origin, cloud);
    assert.match(readFileSync(join(m.bin, "poststeward"), "utf8"),
      /export POSTSTEWARD_ORIGIN=https:\/\/customer-cloud\.example/);
    const requests = readFileSync(m.env.FIXTURE_REQUESTS, "utf8");
    assert.ok(!requests.includes("customer-cloud.example/releases/"), requests);
  });
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
  const original = join(m.root, "original-post-once");
  mkdirSync(original);
  writeFileSync(join(original, "owner-state.json"), '{"untouched":true}\n');
  m.env.OCPF_POST_CONFIG_DIR = original;
  m.env.OCPF_POST_STATE_DIR = original;
  m.env.POST_ONCE_CONFIG_DIR = original;
  m.env.POST_ONCE_STATE_DIR = original;
  const help = spawnSync(cli, ["help", "--json"], { env: m.env, encoding: "utf8", timeout: 30000 });
  assert.equal(help.status, 0, help.stderr);
  assert.ok(JSON.parse(help.stdout).commands.length);
  const doctor = spawnSync(cli, ["doctor", "--json"], { env: m.env, encoding: "utf8", timeout: 30000 });
  assert.equal(doctor.status, 0, doctor.stderr || doctor.stdout);
  const diagnosis = JSON.parse(doctor.stdout);
  assert.equal(diagnosis.status, "ATTENTION");
  assert.equal(diagnosis.runtime.paths.config, join(m.root, "config/poststeward/runtime"));
  assert.equal(diagnosis.runtime.paths.state, join(m.root, "state/poststeward/runtime"));
  assert.equal(readFileSync(join(original, "owner-state.json"), "utf8"), '{"untouched":true}\n');
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

test("channel reinstall preserves identity after local analyzer caches are created", (t) => {
  const m = machine(t);
  assert.equal(m.run().status, 0);
  for (const name of [".ruff_cache", ".mypy_cache"]) {
    const cache = join(m.prefix, "releases", revision, name);
    mkdirSync(cache);
    writeFileSync(join(cache, "generated-cache"), "local analyzer output\n");
  }
  const result = m.run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(readFileSync(m.receipt, "utf8")).runtime_tree_sha256, manifest.runtime_tree_sha256);
});

test("release manifest digest is independent of local analyzer caches", (t) => {
  const root = join(fixture, `poststeward-${revision}`);
  for (const name of [".ruff_cache", ".mypy_cache"]) {
    const cache = join(root, "runtime", name);
    mkdirSync(cache);
    writeFileSync(join(cache, "generated-cache"), "local analyzer output\n");
    t.after(() => rmSync(cache, { recursive: true, force: true }));
  }
  execFileSync(process.execPath, ["scripts/generate-runtime-release-manifest.mjs"], {
    cwd: root, env: { ...process.env, POSTSTEWARD_RELEASE_SHA: revision, POSTSTEWARD_RELEASE_CHANNEL: "stable" },
  });
  const regenerated = JSON.parse(readFileSync(join(root, "public/releases/stable.json"), "utf8"));
  assert.equal(regenerated.runtime_tree_sha256, manifest.runtime_tree_sha256);
});

for (const marker of ['{"status":"active"}', '{"status":"unknown"}', 'broken json']) {
  test(`release change refuses active or unverified local authority (${marker})`, (t) => {
    const m = machine(t);
    assert.equal(m.run("--version", revision).status, 0);
    const state = join(m.root, "state/poststeward/runtime");
    mkdirSync(state, { recursive: true });
    writeFileSync(join(state, "automation-authority.json"), marker);
    const before = readFileSync(m.receipt, "utf8");
    const result = m.run("--version", "a".repeat(40));
    assert.equal(result.status, 2);
    assert.match(result.stderr, /local publishing authority is active or cannot be verified/);
    assert.equal(readFileSync(m.receipt, "utf8"), before);
    assert.equal(readFileSync(m.env.FIXTURE_REQUESTS, "utf8").trim().split("\n").length, 1);
  });
}


test("transient download failure retries with bounded transport and never consumes partial bytes", (t) => {
  const m = machine(t);
  m.env.FIXTURE_FETCH_FAILURE = "once";
  m.env.FIXTURE_REQUIRE_BOUNDED_TRANSPORT = "1";
  const result = m.run("--dry-run");
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(readFileSync(m.env.FIXTURE_FAILURE_COUNTER, "utf8"), "2");
  const requests = readFileSync(m.env.FIXTURE_REQUESTS, "utf8").trim().split("\n");
  assert.equal(requests.length, 2);
  assert.equal(requests[0], requests[1]);
  assert.equal(existsSync(m.prefix), false);
  assert.equal(existsSync(m.receipt), false);
});

test("exhausted retry budget preserves previously installed revision and user state", (t) => {
  const m = machine(t);
  assert.equal(m.run().status, 0);
  const receipt = readFileSync(m.receipt, "utf8");
  const before = readFileSync(join(m.prefix, "current", "poststeward"), "utf8");
  const sentinel = join(m.root, "state", "poststeward", "customer-preserve.txt");
  writeFileSync(sentinel, "do-not-lose\n");
  const existingRequests = readFileSync(m.env.FIXTURE_REQUESTS, "utf8").trim().split("\n").length;
  m.env.FIXTURE_FETCH_FAILURE = "always";
  m.env.FIXTURE_REQUIRE_BOUNDED_TRANSPORT = "1";
  const result = m.run("--version", "f".repeat(40));
  assert.notEqual(result.status, 0, "failed download must not promote anything");
  const requests = readFileSync(m.env.FIXTURE_REQUESTS, "utf8").trim().split("\n");
  assert.equal(requests.length - existingRequests, 3, "retry budget must be finite");
  assert.equal(readFileSync(m.receipt, "utf8"), receipt);
  assert.equal(readFileSync(join(m.prefix, "current", "poststeward"), "utf8"), before);
  assert.equal(readFileSync(sentinel, "utf8"), "do-not-lose\n");
  assert.equal(existsSync(join(m.prefix, "releases", "f".repeat(40))), false);
});


test("permanent HTTP failure does not retry or leave an installation", (t) => {
  const m = machine(t);
  m.env.FIXTURE_FETCH_FAILURE = "http";
  const result = m.run("--dry-run");
  assert.notEqual(result.status, 0);
  assert.equal(readFileSync(m.env.FIXTURE_FAILURE_COUNTER, "utf8"), "1");
  assert.equal(readFileSync(m.env.FIXTURE_REQUESTS, "utf8").trim().split("\n").length, 1);
  assert.equal(existsSync(m.receipt), false);
  assert.equal(existsSync(m.prefix), false);
});


test("wget-only fallback retries transient transport errors with private staging", (t) => {
  const m = machine(t);
  const toolDir = join(m.root, "wget-only");
  mkdirSync(toolDir);
  // Present only utilities required for a dry-run, with no curl.
  for (const name of ["bash", "python3", "tar", "mktemp", "rm", "sed", "tr", "grep", "dirname", "basename", "cat", "sleep", "mv", "cp", "mkdir", "find", "ln", "chmod", "date", "id", "readlink", "touch"]) {
    const found = execFileSync("which", [name], { encoding: "utf8" }).trim();
    symlinkSync(found, join(toolDir, name));
  }
  const wget = join(toolDir, "wget");
  writeFileSync(wget, [
    "#!/usr/bin/env python3",
    "import os, shutil, sys",
    "args = sys.argv[1:]",
    "assert '--https-only' in args and '--timeout=60' in args and '--tries=1' in args",
    "target = args[args.index('-O') + 1]",
    "url = args[-1]",
    "assert url == 'https://poststeward.example/releases/stable.json'",
    "assert os.path.isabs(target) and target.startswith(os.environ['FIXTURE_TMP_PREFIX'])",
    "counter = os.environ['FIXTURE_FAILURE_COUNTER']",
    "count = int(open(counter).read()) if os.path.exists(counter) else 0",
    "with open(counter, 'w') as out: out.write(str(count + 1))",
    "if count == 0:",
    "    with open(target, 'wb') as out: out.write(b'corrupt partial')",
    "    sys.exit(4)",
    "shutil.copyfile(os.environ['FIXTURE_MANIFEST'], target)",
  ].join("\n") + "\n");
  chmodSync(wget, 0o755);
  m.env.PATH = toolDir;
  const result = m.run("--dry-run");
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(readFileSync(m.env.FIXTURE_FAILURE_COUNTER, "utf8"), "2");
  assert.equal(existsSync(m.receipt), false);
});
