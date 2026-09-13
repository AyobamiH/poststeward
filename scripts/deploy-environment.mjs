import { readFileSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { assertRetainedRoots, runRootCutover } from "./root-cutover.mjs";
import {
  demand,
  deploymentSecrets,
  validateConfiguration,
  verifySandboxPrice,
} from "./deployment-config.mjs";

const c = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
validateConfiguration(c);
demand(
  process.env.GITHUB_REF === "refs/heads/main" &&
    process.env.GITHUB_REPOSITORY === "AyobamiH/poststeward" &&
    process.env.GITHUB_SHA === c.vars.RELEASE_SHA,
  "Deploy only the reviewed main revision through this repository's environment workflow.",
);
demand(
  process.env.CLOUDFLARE_ACCOUNT_ID === c.account_id &&
    process.env.CLOUDFLARE_API_TOKEN,
  "Deployment account and token are required.",
);
const secrets = deploymentSecrets(process.env);
const rootHash = value => createHash("sha256").update(Buffer.from(value, "base64")).digest("hex");
c.vars.ENCRYPTION_LEGACY_ROOT_ID = rootHash(secrets.ENCRYPTION_KEY);
if (secrets.ENCRYPTION_KEY_NEXT) c.vars.ENCRYPTION_NEXT_ROOT_ID = rootHash(secrets.ENCRYPTION_KEY_NEXT);
// A redeployment must not strand mixed-root or recoverable historical data.
const currentResponse = await fetch(`https://api.cloudflare.com/client/v4/accounts/${c.account_id}/workers/scripts/${c.name}/settings`, {
  headers: { Authorization: `Bearer ${process.env.CLOUDFLARE_API_TOKEN}` },
  redirect: "error", signal: AbortSignal.timeout(15000),
});
demand(currentResponse.ok || currentResponse.status === 404, "Cannot inspect current Worker root bindings; deployment stopped.");
if (currentResponse.ok) {
  const current = await currentResponse.json();
  demand(current.success && Array.isArray(current.result?.bindings), "Current Worker settings are incomplete.");
  const vars = Object.fromEntries(current.result.bindings.filter(b => b.type === "plain_text").map(b => [b.name, b.text]));
  assertRetainedRoots(vars, c.vars);
}
if (c.vars.ENCRYPTION_ROOT_WRITE === "next") {
  secrets.ROOT_ROTATION_TOKEN = randomBytes(32).toString("hex");
  c.vars.ROOT_ROTATION_EXPIRES_AT = String(Date.now() + 45 * 60 * 1000);
} else {
  c.vars.ROOT_ROTATION_EXPIRES_AT = "0";
}
writeFileSync("wrangler.jsonc", JSON.stringify(c, null, 2) + "\n");
await verifySandboxPrice(process.env);
const db = c.d1_databases[0];
const response = await fetch(
  `https://api.cloudflare.com/client/v4/accounts/${c.account_id}/d1/database/${db.database_id}`,
  {
    headers: { Authorization: `Bearer ${process.env.CLOUDFLARE_API_TOKEN}` },
    signal: AbortSignal.timeout(15000),
    redirect: "error",
  },
);
demand(
  response.ok,
  `Cloudflare database identity check failed (HTTP ${response.status}).`,
);
const metadata = await response.json();
demand(
  metadata.success &&
    metadata.result?.name === db.database_name &&
    metadata.result?.uuid === db.database_id,
  "D1 database identity does not match the deployment environment. No migration or deployment performed.",
);
demand(
  metadata.result.read_replication?.mode !== "auto",
  "Keep identity read replication disabled for this deployment.",
);
const dir = mkdtempSync(join(tmpdir(), "poststeward-secrets-"));
try {
  const file = join(dir, "secrets.json");
  writeFileSync(file, JSON.stringify(secrets), { mode: 0o600 });
  function wrangler(args) {
    const result = spawnSync(
      process.execPath,
      ["node_modules/wrangler/bin/wrangler.js", ...args],
      {
        stdio: "inherit",
        env: { ...process.env, WRANGLER_SEND_METRICS: "false" },
      },
    );
    demand(
      !result.error && result.status === 0,
      "Cloudflare command failed; inspect the preceding operation status before retrying.",
    );
  }
  wrangler(["d1", "migrations", "apply", db.database_name, "--remote"]);
  wrangler(["deploy", "--secrets-file", file]);
  if (secrets.ROOT_ROTATION_TOKEN) {
    // Allow bounded requests from the prior deployment to finish before the
    // current writer's inventory is traversed. No provider request is retried.
    await new Promise(resolve => setTimeout(resolve, 120000));
    const report = await runRootCutover({ origin: c.vars.PUBLIC_ORIGIN, release: c.vars.RELEASE_SHA,
      token: secrets.ROOT_ROTATION_TOKEN });
    console.log("POSTSTEWARD_ROOT_CUTOVER " + JSON.stringify(report));
  }
} finally {
  rmSync(dir, { recursive: true, force: true });
}
