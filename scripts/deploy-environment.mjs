import { readFileSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import {
  demand,
  deploymentSecrets,
  validateConfiguration,
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
} finally {
  rmSync(dir, { recursive: true, force: true });
}
