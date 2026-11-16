import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { activeProductionDeployment, assertProtectedProductionMetadata, assertHistoricalProductionProvenance, readOnlyProductionRootMetadata } from "../scripts/production-root-provenance-preflight.mjs";

const versionId = "1e283bd2-802d-4ed2-ab92-ad5863057fab";
const release = "20aa2987a347a3251573fc55c788b8d7d23f0ae6";
const db = "6b06deb6-c0f1-437a-9846-72509a4ace28";
const key = Buffer.alloc(32, 0x31).toString("base64");
const hash = createHash("sha256").update(Buffer.from(key, "base64")).digest("hex");
const env = () => ({ GITHUB_REPOSITORY: "AyobamiH/poststeward", GITHUB_REF: "refs/heads/main",
  GITHUB_ACTOR: "AyobamiH", GITHUB_SHA: "b78d2965fb6814c9a28faf1ed372f0c125483045",
  CLOUDFLARE_ACCOUNT_ID: "a".repeat(32), CLOUDFLARE_API_TOKEN: "fixture", GITHUB_TOKEN: "fixture-gh",
  PRODUCTION_ROOT_WRITE: "legacy", PRODUCTION_CREDENTIAL_MODE: "shared_staging_bootstrap",
  EXPECTED_PRODUCTION_RELEASE: release, EXPECTED_PRODUCTION_D1: db, ENCRYPTION_KEY: key });
const deployment = () => ({ success: true, result_info: { page: 1 }, result: { deployments: [{
  id: "a16ba158-2f4a-443e-8dc8-14eae3de9e6b", created_on: "2026-11-03T20:57:03Z",
  strategy: "percentage", versions: [{ version_id: versionId, percentage: 100 }],
}] } });
const version = () => ({ success: true, result: { id: versionId, resources: { bindings: [
  ["DEPLOY_ENV", "production"], ["RELEASE_SHA", release], ["SIGNUP_MODE", "restricted"],
  ["ADVANCED_ENABLED", "false"], ["ADVANCED_ROLLOUT_MODE", "disabled"], ["ADVANCED_CANARY_BPS", "0"],
  ["ENCRYPTION_ROOT_WRITE", "legacy"], ["ENCRYPTION_LEGACY_ROOT_ID", hash],
].map(([name, text]) => ({ name, type: "plain_text", text })).concat([
  { name: "ENCRYPTION_KEY", type: "secret_text" }, { name: "IDENTITY", type: "d1", id: db },
]) } } });
const held = (v = version(), d = deployment(), e = env()) =>
  assert.throws(() => assertProtectedProductionMetadata(v, activeProductionDeployment(d), e), /no deployment authorised/);
const altered = (name, text) => {
  const v = version(); v.result.resources.bindings.find(b => b.name === name).text = text; return v;
};

test("protected metadata parity cannot claim active Worker secret-byte equality", () => {
  const p = assertProtectedProductionMetadata(version(), activeProductionDeployment(deployment()), env());
  assert.equal(p.secret_byte_equality_attested, false);
  assert.match(p.provisioning_provenance, /not_checked/);
  assert.equal(p.release_sha, release);
  assert.equal(p.deployments, 0);
  assert.ok(!JSON.stringify(p).includes(key) && !JSON.stringify(p).includes(hash));
});
test("reject incorrect production key, metadata, roots, signup, Advanced, database and actor", () => {
  for (const value of ["", "bad", key + "\n", Buffer.alloc(32, 0xff).toString("base64")])
    held(version(), deployment(), { ...env(), ENCRYPTION_KEY: value });
  for (const [name, val] of [
    ["DEPLOY_ENV","staging"], ["RELEASE_SHA","0".repeat(40)], ["SIGNUP_MODE","public"],
    ["ADVANCED_ENABLED","true"], ["ADVANCED_ROLLOUT_MODE","global"], ["ADVANCED_CANARY_BPS","100"],
    ["ENCRYPTION_ROOT_WRITE","next"], ["ENCRYPTION_LEGACY_ROOT_ID","0".repeat(64)],
  ]) held(altered(name, val));
  for (const patch of [{GITHUB_ACTOR:"other"}, {GITHUB_REF:"refs/heads/feature"},
    {GITHUB_SHA:"bad"}, {PRODUCTION_CREDENTIAL_MODE:"isolated"},
    {EXPECTED_PRODUCTION_RELEASE:"0".repeat(40)}, {PRODUCTION_ROOT_WRITE:"next"}])
    held(version(), deployment(), { ...env(), ...patch });
  const wrongDb = version(); wrongDb.result.resources.bindings.find(b=>b.name==="IDENTITY").id="0".repeat(36); held(wrongDb);
  const next = version(); next.result.resources.bindings.push({ name:"ENCRYPTION_NEXT_ROOT_ID",type:"plain_text",text:"0".repeat(64) }); held(next);
  const missing = version(); missing.result.resources.bindings.pop(); held(missing);
});
test("reject split traffic, changed version, invalid ordering and malformed API envelope", () => {
  const split = deployment(); split.result.deployments[0].versions[0].percentage = 99;
  assert.throws(() => activeProductionDeployment(split), /no deployment authorised/);
  const wrong = deployment(); wrong.result.deployments[0].versions[0].version_id = "384a5eb5-788d-40d3-a718-3321943f2a46";
  assert.throws(() => activeProductionDeployment(wrong), /no deployment authorised/);
  for(const d of [{success:false},{success:true,result:{deployments:[]}},
    {...deployment(),result_info:{page:2}}])
    assert.throws(() => activeProductionDeployment(d), /no deployment authorised/);
});
const historicalRun = () => ({ id:41714915342, head_sha:release, path:".github/workflows/deploy.yml",
  event:"workflow_dispatch",status:"completed",conclusion:"success",run_attempt:1,
  created_at:"2026-11-03T20:41:42Z",updated_at:"2026-11-03T20:58:29Z" });
const historicalJobs = () => ({total_count:2,jobs:[
  {name:"verify",conclusion:"success"},
  {name:"deploy",status:"completed",conclusion:"success",runner_name:"poststeward-release-1793738388",
   started_at:"2026-11-03T20:52:49Z",completed_at:"2026-11-03T20:58:28Z",
   steps:[{name:"Validate secrets and database; migrate and deploy",conclusion:"success"},
          {name:"Verify deployed revision and access boundary",conclusion:"success"}]},
]});
const fixtureResponse = url => url.includes("/versions/") ? version() :
  url.includes("actions/runs/41714915342/jobs") ? historicalJobs() :
  url.includes("actions/runs/41714915342") ? historicalRun() : deployment();

test("historical provenance must be exact source, completed job and plausible chronology",()=>{
  const active=activeProductionDeployment(deployment());
  assert.match(assertHistoricalProductionProvenance(historicalRun(),historicalJobs(),active),/temporally_correlated/);
  const bad=historicalRun(); bad.head_sha="0".repeat(40);
  assert.throws(()=>assertHistoricalProductionProvenance(bad,historicalJobs(),active),/no deployment authorised/);
  const skipped=historicalJobs(); skipped.jobs[1].steps[0].conclusion="skipped";
  assert.throws(()=>assertHistoricalProductionProvenance(historicalRun(),skipped,active),/no deployment authorised/);
  const time={...active,created_on:"2026-11-04T00:00:00Z"};
  assert.throws(()=>assertHistoricalProductionProvenance(historicalRun(),historicalJobs(),time),/no deployment authorised/);
});

test("five GETs, double-read deployment fence, failure before effect", async () => {
  const calls=[];
  const fetcher = async (url, opt) => {
    calls.push([url,opt.method,opt.redirect]);
    return {ok:true,json:async()=>fixtureResponse(url)};
  };
  assert.equal((await readOnlyProductionRootMetadata(env(),fetcher)).deployments,0);
  assert.equal(calls.length,5);
  assert.ok(calls.every(c=>c[1]==="GET" && c[2]==="error" && (c[0].includes("/workers/scripts/poststeward/") || c[0].includes("api.github.com/repos/AyobamiH/poststeward/"))));
  let n=0;
  await assert.rejects(readOnlyProductionRootMetadata(env(),async (url,opt)=>{
    n++; assert.equal(opt.method,"GET");
    const d=deployment(); if(n===5)d.result.deployments[0].id="11111111-2222-3333-4444-555555555555";
    return {ok:true,json:async()=>url.includes("deployments?")?d:fixtureResponse(url)};
  }), /no deployment authorised/);
  assert.equal(n,5);
  let called=false;
  await assert.rejects(readOnlyProductionRootMetadata({...env(),GITHUB_REF:"refs/heads/unreviewed"},async()=>{called=true;}),/no deployment authorised/);
  assert.equal(called,false);
  await assert.rejects(readOnlyProductionRootMetadata(env(),async()=>({ok:false})),/no deployment authorised/);
});
test("existing manual workflow uses protected environment, local runner and no deploy trigger",()=>{
  const wf=readFileSync(new URL("../.github/workflows/staging-root-parity-preflight.yml",import.meta.url),"utf8");
  assert.match(wf,/inputs.target == 'production'/);
  assert.match(wf,/environment: production/);
  assert.match(wf,/node scripts\/production-root-provenance-preflight.mjs/);
  assert.match(wf,/runs-on: poststeward-release/);
  assert.doesNotMatch(wf,/schedule:|push:|ubuntu-latest|npm ci|wrangler|secrets: inherit/i);
});
