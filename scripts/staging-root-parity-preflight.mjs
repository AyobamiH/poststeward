import { createHash, timingSafeEqual } from "node:crypto";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const uuid = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/;
const sha = /^[a-f0-9]{40}$/;
const digest = /^[a-f0-9]{64}$/;
const fail = () => { throw new Error("Restricted staging root parity not established; no deployment authorised."); };

function canonicalRoot(encoded) {
  if (typeof encoded !== "string" || !/^[A-Za-z0-9+/]{43}=$/.test(encoded)) fail();
  const raw = Buffer.from(encoded, "base64");
  if (raw.length !== 32 || raw.toString("base64") !== encoded) fail();
  return createHash("sha256").update(raw).digest("hex");
}

function equalDigest(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !digest.test(a) || !digest.test(b)) return false;
  return timingSafeEqual(Buffer.from(a, "hex"), Buffer.from(b, "hex"));
}

// Cloudflare documents the first listed deployment as the deployment serving traffic.
// Never infer the deployed version from latest-uploaded script settings.
export function activeStagingDeployment(payload) {
  if (payload?.success !== true || payload?.result_info?.page && payload.result_info.page !== 1) fail();
  const deployments = payload?.result?.deployments;
  if (!Array.isArray(deployments) || !deployments.length) fail();
  const first = deployments[0];
  if (!uuid.test(first?.id || "") || !Number.isFinite(Date.parse(first?.created_on || "")) ||
      first.strategy !== "percentage" || !Array.isArray(first.versions) || first.versions.length !== 1 ||
      first.versions[0].percentage !== 100 || !uuid.test(first.versions[0].version_id || "")) fail();
  // Defensive: do not accept an out-of-order or ambiguous first page.
  if (deployments.slice(1).some(item => !Number.isFinite(Date.parse(item?.created_on || "")) ||
      Date.parse(item.created_on) >= Date.parse(first.created_on))) fail();
  return { deployment_id: first.id, version_id: first.versions[0].version_id };
}

export function assertProtectedStagingRootParity(payload, active, env) {
  if (env.GITHUB_REPOSITORY !== "AyobamiH/poststeward" || env.GITHUB_REF !== "refs/heads/main" ||
      !sha.test(env.GITHUB_SHA || "") || env.STAGING_ROOT_WRITE !== "next" ||
      !uuid.test(active?.version_id || "")) fail();
  const legacy = canonicalRoot(env.ENCRYPTION_KEY);
  const next = canonicalRoot(env.ENCRYPTION_KEY_NEXT);
  if (equalDigest(legacy, next)) fail();
  const version = payload?.result;
  if (payload?.success !== true || version?.id !== active.version_id ||
      !Array.isArray(version?.resources?.bindings)) fail();
  const vars = new Map();
  for (const b of version.resources.bindings) {
    if (typeof b?.name !== "string" || vars.has(b.name)) fail();
    vars.set(b.name, b);
  }
  const value = name => {
    const binding = vars.get(name);
    if (binding?.type !== "plain_text" || typeof binding?.text !== "string") fail();
    return binding.text;
  };
  if (value("DEPLOY_ENV") !== "staging" || value("ENCRYPTION_ROOT_WRITE") !== "next" ||
      value("SIGNUP_MODE") !== "restricted" || value("ADVANCED_ENABLED") !== "false" ||
      value("ADVANCED_ROLLOUT_MODE") !== "disabled" || !sha.test(value("RELEASE_SHA")) ||
      !equalDigest(value("ENCRYPTION_LEGACY_ROOT_ID"), legacy) ||
      !equalDigest(value("ENCRYPTION_NEXT_ROOT_ID"), next)) fail();
  return {
    scope: "staging_read_only_root_parity",
    parity: "verified",
    worker_version_id: active.version_id,
    worker_release_sha: value("RELEASE_SHA"),
    source_sha: env.GITHUB_SHA,
    database_writes: 0,
    root_cutover_effects: 0,
    deployments: 0,
  };
}

async function getCloudflareJson(path, env, send) {
  const response = await send(`https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/poststeward-staging/${path}`, {
    method: "GET",
    headers: { Authorization: `Bearer ${env.CLOUDFLARE_API_TOKEN}` },
    redirect: "error",
    signal: AbortSignal.timeout(15000),
  }).catch(fail);
  if (!response?.ok) fail();
  return response.json().catch(fail);
}

// Two deployment reads fence a concurrent Worker change during metadata inspection.
// The only network calls are Cloudflare GET requests; secrets remain in memory.
export async function readOnlyStagingRootParity(env, send = fetch) {
  if (env.GITHUB_REPOSITORY !== "AyobamiH/poststeward" || env.GITHUB_REF !== "refs/heads/main" ||
      !sha.test(env.GITHUB_SHA || "") || env.STAGING_ROOT_WRITE !== "next" ||
      !/^[a-f0-9]{32}$/.test(env.CLOUDFLARE_ACCOUNT_ID || "") ||
      !env.CLOUDFLARE_API_TOKEN) fail();
  // Pre-validate credentials before making even a read-only network request.
  canonicalRoot(env.ENCRYPTION_KEY);
  canonicalRoot(env.ENCRYPTION_KEY_NEXT);
  const first = activeStagingDeployment(await getCloudflareJson("deployments?per_page=100", env, send));
  const version = await getCloudflareJson(`versions/${first.version_id}`, env, send);
  const proof = assertProtectedStagingRootParity(version, first, env);
  const second = activeStagingDeployment(await getCloudflareJson("deployments?per_page=100", env, send));
  if (second.deployment_id !== first.deployment_id || second.version_id !== first.version_id) fail();
  return proof;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  try {
    console.log("POSTSTEWARD_PROTECTED_STAGING_ROOT_PARITY " + JSON.stringify(await readOnlyStagingRootParity(process.env)));
  } catch {
    console.error("Restricted staging root parity not established; no deployment authorised.");
    process.exitCode = 1;
  }
}
