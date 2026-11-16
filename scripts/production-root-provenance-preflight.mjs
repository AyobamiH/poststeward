import { createHash, timingSafeEqual } from "node:crypto";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

// Explicitly pinned existing restricted production version; changes require reviewed source,
// not an operator-provided version or a silent switch to an unsafe rollback.
const EXPECTED_RELEASE = "20aa2987a347a3251573fc55c788b8d7d23f0ae6";
const EXPECTED_VERSION = "1e283bd2-802d-4ed2-ab92-ad5863057fab";
const EXPECTED_D1 = "6b06deb6-c0f1-437a-9846-72509a4ace28";
const uuid = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/;
const sha = /^[a-f0-9]{40}$/;
const hash = /^[a-f0-9]{64}$/;
const hold = () => { throw new Error("Protected production root metadata/provenance not established; no deployment authorised."); };

function protectedDigest(encoded) {
  if (typeof encoded !== "string" || !/^[A-Za-z0-9+/]{43}=$/.test(encoded)) hold();
  const bytes = Buffer.from(encoded, "base64");
  if (bytes.length !== 32 || bytes.toString("base64") !== encoded) hold();
  return createHash("sha256").update(bytes).digest();
}
function sameDigest(a, b) {
  return typeof b === "string" && hash.test(b) &&
    timingSafeEqual(a, Buffer.from(b, "hex"));
}
function protectedContext(env) {
  if (env.GITHUB_REPOSITORY !== "AyobamiH/poststeward" ||
      env.GITHUB_REF !== "refs/heads/main" ||
      env.GITHUB_ACTOR !== "AyobamiH" ||
      !sha.test(env.GITHUB_SHA || "") ||
      env.PRODUCTION_CREDENTIAL_MODE !== "shared_staging_bootstrap" ||
      env.PRODUCTION_ROOT_WRITE !== "legacy" ||
      env.EXPECTED_PRODUCTION_RELEASE !== EXPECTED_RELEASE ||
      env.EXPECTED_PRODUCTION_D1 !== EXPECTED_D1) hold();
  return protectedDigest(env.ENCRYPTION_KEY);
}

// Refuse traffic splits, ambiguous ordering and the most-recently-uploaded
// version when it is not the version actually serving 100% of requests.
export function activeProductionDeployment(payload) {
  if (payload?.success !== true ||
      (payload?.result_info?.page != null && payload.result_info.page !== 1)) hold();
  const deployments = payload?.result?.deployments;
  if (!Array.isArray(deployments) || !deployments.length) hold();
  const first = deployments[0];
  if (!uuid.test(first?.id || "") || !Number.isFinite(Date.parse(first?.created_on || "")) ||
      first.strategy !== "percentage" || !Array.isArray(first.versions) ||
      first.versions.length !== 1 || first.versions[0].percentage !== 100 ||
      first.versions[0].version_id !== EXPECTED_VERSION ||
      deployments.slice(1).some(d => !Number.isFinite(Date.parse(d?.created_on || "")) ||
        Date.parse(d.created_on) >= Date.parse(first.created_on))) hold();
  return { deployment_id: first.id, version_id: first.versions[0].version_id, created_on: first.created_on };
}

export function assertProtectedProductionMetadata(payload, active, env) {
  const derived = protectedContext(env);
  if (active?.version_id !== EXPECTED_VERSION ||
      payload?.success !== true || payload?.result?.id !== EXPECTED_VERSION ||
      !Array.isArray(payload.result?.resources?.bindings)) hold();

  const bindings = new Map();
  for (const binding of payload.result.resources.bindings) {
    if (typeof binding?.name !== "string" || bindings.has(binding.name)) hold();
    bindings.set(binding.name, binding);
  }
  const value = name => {
    const b = bindings.get(name);
    if (b?.type !== "plain_text" || typeof b.text !== "string") hold();
    return b.text;
  };
  if (value("DEPLOY_ENV") !== "production" ||
      value("RELEASE_SHA") !== EXPECTED_RELEASE ||
      value("SIGNUP_MODE") !== "restricted" ||
      value("ADVANCED_ENABLED") !== "false" ||
      value("ADVANCED_ROLLOUT_MODE") !== "disabled" ||
      value("ADVANCED_CANARY_BPS") !== "0" ||
      value("ENCRYPTION_ROOT_WRITE") !== "legacy" ||
      !sameDigest(derived, value("ENCRYPTION_LEGACY_ROOT_ID")) ||
      bindings.has("ENCRYPTION_NEXT_ROOT_ID") ||
      bindings.has("ENCRYPTION_KEY_NEXT") ||
      bindings.get("ENCRYPTION_KEY")?.type !== "secret_text") hold();

  const d1 = [...bindings.values()].filter(b => b.type === "d1");
  if (d1.length !== 1 || d1[0].name !== "IDENTITY" ||
      (d1[0].id ?? d1[0].database_id) !== EXPECTED_D1) hold();

  // This is metadata parity and a double-read deployment fence ONLY.
  // Cloudflare GET does not attest the bound secret_text bytes.
  return {
    scope: "production_read_only_declared_root_metadata",
    evidence: "protected_secret_digest_matches_active_version_declared_root_id",
    secret_byte_equality_attested: false,
    provisioning_provenance: "not_checked_by_pure_metadata_assertion",
    deployment_id: active.deployment_id,
    version_id: active.version_id,
    release_sha: EXPECTED_RELEASE,
    source_sha: env.GITHUB_SHA,
    database_writes: 0,
    root_changes: 0,
    deployments: 0,
    provider_effects: 0,
  };
}

async function cloudflareGet(path, env, send) {
  const url = `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/poststeward/${path}`;
  const response = await send(url, {
    method: "GET",
    headers: { Authorization: `Bearer ${env.CLOUDFLARE_API_TOKEN}` },
    redirect: "error",
    signal: AbortSignal.timeout(15000),
  }).catch(hold);
  if (!response?.ok) hold();
  return response.json().catch(hold);
}

// GitHub run and exact self-hosted deploy job corroborate historical provisioning.
// A chronological link does NOT cryptographically attest the Worker secret bytes.
export function assertHistoricalProductionProvenance(run, jobs, active) {
  if (run?.id !== 41714915342 || run?.head_sha !== EXPECTED_RELEASE ||
      run?.path !== ".github/workflows/deploy.yml" ||
      run?.event !== "workflow_dispatch" || run?.status !== "completed" ||
      run?.conclusion !== "success" || run?.run_attempt !== 1 ||
      !Number.isFinite(Date.parse(run?.created_at || "")) ||
      !Number.isFinite(Date.parse(run?.updated_at || "")) ||
      jobs?.total_count !== 2 || !Array.isArray(jobs.jobs)) hold();
  const deploymentJobs = jobs.jobs.filter(j => j?.name === "deploy");
  if (deploymentJobs.length !== 1) hold();
  const job = deploymentJobs[0];
  if (job?.status !== "completed" || job?.conclusion !== "success" ||
      !/^poststeward-release-/.test(job?.runner_name || "") ||
      !Number.isFinite(Date.parse(job?.started_at || "")) ||
      !Number.isFinite(Date.parse(job?.completed_at || "")) ||
      !Array.isArray(job.steps)) hold();
  for (const name of ["Validate secrets and database; migrate and deploy",
                      "Verify deployed revision and access boundary"]) {
    if (job.steps.filter(s => s?.name === name && s.conclusion === "success").length !== 1) hold();
  }
  const deployed = Date.parse(active.created_on);
  if (!(Date.parse(run.created_at) <= Date.parse(job.started_at) &&
        Date.parse(job.started_at) <= deployed &&
        deployed <= Date.parse(job.completed_at) &&
        Date.parse(job.completed_at) <= Date.parse(run.updated_at))) hold();
  return "historical_GitHub_run_and_deploy_job_temporally_correlated_not_cryptographic_secret_attestation";
}
async function githubRead(path, env, send) {
  const response = await send(`https://api.github.com/repos/AyobamiH/poststeward/${path}`, {
    method: "GET",
    headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json" },
    redirect: "error",
    signal: AbortSignal.timeout(15000),
  }).catch(hold);
  if (!response?.ok) hold();
  return response.json().catch(hold);
}

export async function readOnlyProductionRootMetadata(env, send = fetch) {
  protectedContext(env);
  if (!/^[a-f0-9]{32}$/.test(env.CLOUDFLARE_ACCOUNT_ID || "") ||
      !env.CLOUDFLARE_API_TOKEN || !env.GITHUB_TOKEN) hold();
  const first = activeProductionDeployment(await cloudflareGet("deployments?per_page=100", env, send));
  const version = await cloudflareGet(`versions/${first.version_id}`, env, send);
  const proof = assertProtectedProductionMetadata(version, first, env);
  const run = await githubRead("actions/runs/41714915342", env, send);
  const jobs = await githubRead("actions/runs/41714915342/jobs?per_page=100", env, send);
  const provenance = assertHistoricalProductionProvenance(run, jobs, first);
  const second = activeProductionDeployment(await cloudflareGet("deployments?per_page=100", env, send));
  if (first.deployment_id !== second.deployment_id || first.version_id !== second.version_id) hold();
  return { ...proof, provisioning_provenance: provenance };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  try {
    console.log("POSTSTEWARD_PROTECTED_PRODUCTION_ROOT_METADATA " +
      JSON.stringify(await readOnlyProductionRootMetadata(process.env)));
  } catch {
    console.error("Protected production root metadata/provenance not established; no deployment authorised.");
    process.exitCode = 1;
  }
}
