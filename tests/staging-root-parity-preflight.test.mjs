import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { activeStagingDeployment, assertProtectedStagingRootParity, readOnlyStagingRootParity } from "../scripts/staging-root-parity-preflight.mjs";

const deploymentId = "dd1e2b0d-2e46-4afd-a745-af553339ab52";
const versionId = "74239e0b-61f1-4089-b369-c4fcbf107b83";
const otherVersionId = "a6e4d1df-73a4-4241-ac6f-41f69e2b077b";
const legacy = Buffer.alloc(32, 0x11).toString("base64");
const next = Buffer.alloc(32, 0x22).toString("base64");
const hash = k => createHash("sha256").update(Buffer.from(k, "base64")).digest("hex");
const env = () => ({
  GITHUB_REPOSITORY: "AyobamiH/poststeward", GITHUB_REF: "refs/heads/main",
  GITHUB_SHA: "f2c98d5886bf3df58624c6fc4399a314543df793",
  CLOUDFLARE_ACCOUNT_ID: "a".repeat(32), CLOUDFLARE_API_TOKEN: "fixture-token",
  STAGING_ROOT_WRITE: "next", ENCRYPTION_KEY: legacy, ENCRYPTION_KEY_NEXT: next,
});
const deploy = () => ({ success: true, result: { deployments: [
  { id: deploymentId, created_on: "2026-11-11T07:06:19.983428Z", strategy: "percentage", versions: [{ version_id: versionId, percentage: 100 }] },
  { id: "c03cddae-d2f8-4c7e-9fce-8ffa1d49506e", created_on: "2026-11-07T22:32:38Z", strategy: "percentage", versions: [{ version_id: otherVersionId, percentage: 100 }] },
] }, result_info: { page: 1 } });
const version = () => ({ success: true, result: { id: versionId, resources: { bindings: [
  ["DEPLOY_ENV", "staging"], ["ENCRYPTION_ROOT_WRITE", "next"],
  ["ENCRYPTION_LEGACY_ROOT_ID", hash(legacy)], ["ENCRYPTION_NEXT_ROOT_ID", hash(next)],
  ["SIGNUP_MODE", "restricted"], ["ADVANCED_ENABLED", "false"],
  ["ADVANCED_ROLLOUT_MODE", "disabled"], ["RELEASE_SHA", "a6b4631346555f2ef6a2045606094b902ce7b0d3"],
].map(([name, text]) => ({ name, type: "plain_text", text })) } } });
const replace = (v, name, text) => { v.result.resources.bindings.find(b => b.name === name).text = text; return v; };
const expectHeld = (d, v, e = env()) => assert.throws(() => assertProtectedStagingRootParity(v, activeStagingDeployment(d), e), /no deployment authorised/);

test("exact active Worker must match both protected roots without disclosing key material", () => {
  const proof = assertProtectedStagingRootParity(version(), activeStagingDeployment(deploy()), env());
  assert.equal(proof.parity, "verified");
  assert.equal(proof.worker_version_id, versionId);
  assert.equal(proof.source_sha, env().GITHUB_SHA);
  assert.equal(proof.database_writes, 0);
  assert.equal(proof.root_cutover_effects, 0);
  assert.equal(proof.deployments, 0);
  assert.ok(!JSON.stringify(proof).includes(legacy) && !JSON.stringify(proof).includes(next));
});

test("refuse traffic split, uploaded-only version and ambiguous latest deployment", () => {
  const d = deploy(); d.result.deployments[0].versions[0].percentage = 99;
  d.result.deployments[0].versions.push({ version_id: otherVersionId, percentage: 1 });
  assert.throws(() => activeStagingDeployment(d), /no deployment authorised/);
  const v = version(); v.result.id = otherVersionId; expectHeld(deploy(), v);
  const stale = deploy(); stale.result.deployments[1].created_on = "2026-11-12T22:32:38Z";
  assert.throws(() => activeStagingDeployment(stale), /no deployment authorised/);
  for (const payload of [{success:false,result:deploy().result},{success:true,result:{deployments:[]}},{...deploy(),result_info:{page:2}}]) {
    assert.throws(() => activeStagingDeployment(payload), /no deployment authorised/);
  }
});

test("reject rotated, missing, identical, malformed or mismatched protected root secrets", () => {
  for (const patch of [
    { ENCRYPTION_KEY: Buffer.alloc(32, 0x33).toString("base64") },
    { ENCRYPTION_KEY_NEXT: Buffer.alloc(32, 0x44).toString("base64") },
    { ENCRYPTION_KEY: "" }, { ENCRYPTION_KEY_NEXT: "" },
    { ENCRYPTION_KEY_NEXT: legacy }, { ENCRYPTION_KEY: "abc" },
    { ENCRYPTION_KEY: legacy + "\n" }
  ]) expectHeld(deploy(), version(), { ...env(), ...patch });
});

test("reject duplicate, secret, missing, rollback and overwritten Worker root bindings", () => {
  const duplicate = version(); duplicate.result.resources.bindings.push({ ...duplicate.result.resources.bindings[1] });
  expectHeld(deploy(), duplicate);
  const secret = version(); secret.result.resources.bindings.find(b=>b.name==="ENCRYPTION_NEXT_ROOT_ID").type = "secret_text";
  expectHeld(deploy(), secret);
  const absent = version(); absent.result.resources.bindings = absent.result.resources.bindings.filter(b=>b.name!=="ENCRYPTION_LEGACY_ROOT_ID");
  expectHeld(deploy(), absent);
  for (const [name, text] of [["ENCRYPTION_ROOT_WRITE", "legacy"], ["ENCRYPTION_NEXT_ROOT_ID", "0".repeat(64)],
    ["ENCRYPTION_LEGACY_ROOT_ID", "0".repeat(64)]]) expectHeld(deploy(), replace(version(),name,text));
});

test("reject production context, public admission, Advanced start and invalid release identity", () => {
  for (const [name, value] of [["DEPLOY_ENV","production"],["SIGNUP_MODE","public"],
    ["ADVANCED_ENABLED","true"],["ADVANCED_ROLLOUT_MODE","started"],["RELEASE_SHA","invalid"]]) {
    expectHeld(deploy(), replace(version(),name,value));
  }
  for (const patch of [{GITHUB_REF:"refs/heads/unreviewed"},{GITHUB_REPOSITORY:"other/repo"},
    {STAGING_ROOT_WRITE:"legacy"},{GITHUB_SHA:"not-a-sha"}]) expectHeld(deploy(),version(),{...env(),...patch});
});

test("only three Cloudflare GETs with a fresh, matching deployment readback", async () => {
  const requests = [];
  const send = async (url, options) => {
    requests.push({url,method:options.method,redirect:options.redirect});
    return {ok:true,json:async()=>url.includes("/versions/") ? version() : deploy()};
  };
  const proof = await readOnlyStagingRootParity(env(), send);
  assert.equal(proof.parity, "verified");
  assert.equal(requests.length, 3);
  assert.ok(requests.every(x=>x.method==="GET" && x.redirect==="error" && x.url.startsWith("https://api.cloudflare.com/client/v4/accounts/")));
  assert.equal(requests[0].url, requests[2].url);
  assert.match(requests[1].url,/\/workers\/scripts\/poststeward-staging\/versions\//);
});

test("concurrent deployment change fails closed, no writes and no retries", async () => {
  let count = 0;
  const send = async (url, options) => {
    count++;
    assert.equal(options.method, "GET");
    const d = deploy();
    if(count===3) d.result.deployments[0].id="12345678-1234-1234-1234-123456789abc";
    return {ok:true,json:async()=>url.includes("/versions/") ? version() : d};
  };
  await assert.rejects(readOnlyStagingRootParity(env(),send),/no deployment authorised/);
  assert.equal(count,3);
});

test("unapproved branch, missing root or invalid account blocks before network", async () => {
  for(const patch of [{GITHUB_REF:"refs/heads/feature"},{ENCRYPTION_KEY_NEXT:""},{CLOUDFLARE_ACCOUNT_ID:"bad"}]) {
    let called=false;
    await assert.rejects(readOnlyStagingRootParity({...env(),...patch},async()=>{called=true;throw Error("never");}),/no deployment authorised/);
    assert.equal(called,false);
  }
});

test("HTTP denial, unsuccessful API or corrupted JSON never expose request context", async () => {
  await assert.rejects(readOnlyStagingRootParity(env(),async()=>({ok:false,status:403})),/no deployment authorised/);
  await assert.rejects(readOnlyStagingRootParity(env(),async()=>({ok:true,json:async()=>({success:false})})),/no deployment authorised/);
  await assert.rejects(readOnlyStagingRootParity(env(),async()=>({ok:true,json:async()=>{throw Error("fixture-secret");}})),/no deployment authorised/);
});

test("manual staging-only workflow uses existing runner, no schedule or deployment", () => {
  const wf=readFileSync(new URL("../.github/workflows/staging-root-parity-preflight.yml",import.meta.url),"utf8");
  assert.match(wf,/workflow_dispatch:/);
  assert.doesNotMatch(wf,/schedule:|push:|ubuntu-latest|npm ci|wrangler|secrets: inherit/i);
  assert.match(wf,/runs-on: poststeward-release/);
  assert.match(wf,/environment: staging/);
  assert.match(wf,/github.actor == 'AyobamiH'/);
  assert.match(wf,/persist-credentials: false/);
  assert.match(wf,/node scripts\/staging-root-parity-preflight.mjs/);
});
