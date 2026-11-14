import { readFileSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { assertRetainedRoots } from "./root-cutover.mjs";
import { assertNoImplicitRootCutover, assertCompleteRemoteMigrations } from "./deployment-effect-preflight.mjs";
import { operationalAlertSecrets } from "./operational-alert-config.mjs";
import {
  demand,
  deploymentSecrets,
  validateConfiguration,
  verifySandboxPrice,
  verifyProductionPrice,
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
const secrets = {
  ...deploymentSecrets(process.env),
  ...operationalAlertSecrets(process.env),
};
const rootHash = value => createHash("sha256").update(Buffer.from(value, "base64")).digest("hex");
c.vars.ENCRYPTION_LEGACY_ROOT_ID = rootHash(secrets.ENCRYPTION_KEY);
if (secrets.ENCRYPTION_KEY_NEXT) c.vars.ENCRYPTION_NEXT_ROOT_ID = rootHash(secrets.ENCRYPTION_KEY_NEXT);
// A redeployment must not strand mixed-root or recoverable historical data.
const currentResponse = await fetch(`https://api.cloudflare.com/client/v4/accounts/${c.account_id}/workers/scripts/${c.name}/settings`, {
  headers: { Authorization: `Bearer ${process.env.CLOUDFLARE_API_TOKEN}` },
  redirect: "error", signal: AbortSignal.timeout(15000),
});
demand(currentResponse.ok || currentResponse.status === 404, "Cannot inspect current Worker root bindings; deployment stopped.");
let currentRootBindings = null;
if (currentResponse.ok) {
  const current = await currentResponse.json();
  demand(current.success && Array.isArray(current.result?.bindings), "Current Worker settings are incomplete.");
  currentRootBindings = Object.fromEntries(current.result.bindings.filter(b => b.type === "plain_text").map(b => [b.name, b.text]));
  assertRetainedRoots(currentRootBindings, c.vars);
}
// Existing next-root writes can be retained only on the exact same roots.
// An initial cutover or rotation is never started by an ordinary release.
assertNoImplicitRootCutover(currentRootBindings, c.vars);
c.vars.ROOT_ROTATION_EXPIRES_AT = "0";
writeFileSync("wrangler.jsonc", JSON.stringify(c, null, 2) + "\n");
await verifySandboxPrice(process.env);
await verifyProductionPrice(process.env);
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
// D1 schema writes belong to a separately authorised migration operation.
// Read the exact database ledger and refuse a deploy if any migration is pending.
const migrationResponse = await fetch(
  `https://api.cloudflare.com/client/v4/accounts/${c.account_id}/d1/database/${db.database_id}/query`,
  {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.CLOUDFLARE_API_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ sql: "SELECT name FROM d1_migrations ORDER BY name" }),
    signal: AbortSignal.timeout(15000),
    redirect: "error",
  },
);
demand(migrationResponse.ok, "Cannot inspect remote D1 migrations; deployment stopped.");
const migrationProof = assertCompleteRemoteMigrations(await migrationResponse.json(), db);
console.log("POSTSTEWARD_D1_PREFLIGHT " + JSON.stringify({
  release: c.vars.RELEASE_SHA,
  database_id: db.database_id,
  applied: migrationProof.applied,
  remote_migration_effects: 0,
}));
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
  // No implicit migration apply: the read-only ledger must match source above.
  wrangler(["deploy", "--secrets-file", file]);

} finally {
  rmSync(dir, { recursive: true, force: true });
}
