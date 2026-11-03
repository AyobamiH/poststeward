import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { alertOutputDocument, readOnlyCloudflareFetch } from "../scripts/github-alert-control-plane.mjs";
const script = readFileSync("scripts/github-alert-control-plane.mjs", "utf8");
const workflow = readFileSync(".github/workflows/operational-alert-control-plane.yml", "utf8");
test("GitHub issue fallback emits only bounded durable alert metadata", () => {
  assert.match(script, /SELECT id,class,severity,code,release,occurrences,last_seen_at,status FROM operational_alerts/);
  assert.match(script, /No raw workspace identifier, content, credential or provider token is included/);
  assert.match(script, /existingIssue/);
  assert.match(script, /assignees: \["AyobamiH"\]/);
  assert.doesNotMatch(script, /SELECT \* FROM operational_alerts|subject_fingerprint.*body/);
});
test("fire drill exercises all reviewed classes and a distinct failed-path fallback", () => {
  assert.match(script, /REQUIRED_ALERT_CLASSES\.map/);
  assert.match(script, /synthetic_unreachable_primary/);
  assert.match(script, /fallbackPath: "github_issue"/);
  assert.match(script, /evaluateAlertEvidence\(observation\)/);
  assert.match(script, /closeIssue/);
});
test("fire drill writes the raw hosted observation for independent re-evaluation", () => {
  const observation = {
    schemaVersion: 1,
    evidenceClass: "hosted_observation",
    environment: "staging",
    release: "a".repeat(40),
    deliveries: [],
    configuredPaths: [],
    failedPath: {},
  };
  const result = { report: { ready: true }, observation };
  assert.equal(alertOutputDocument("fire-drill", result), observation);
  assert.deepEqual(alertOutputDocument("surface", { observed: 0 }), { observed: 0 });
  assert.match(script, /writeFileSync\(process\.env\.POSTSTEWARD_ALERT_OUTPUT, output/);
});
test("workflow is out-of-band across staging and production, issue-write only and never deploys", () => {
  assert.match(workflow, /contents: read/);
  assert.match(workflow, /issues: write/);
  assert.match(workflow, /environment:\n      name: \$\{\{ matrix\.environment \}\}/);
  assert.match(workflow, /- environment: staging/);
  assert.match(workflow, /- environment: production/);
  assert.match(workflow, /secrets\.CLOUDFLARE_API_TOKEN/);
  assert.match(workflow, /secrets\.GITHUB_TOKEN/);
  assert.ok(workflow.includes("types: [poststeward-monitor-alerts]"));
  assert.ok(readFileSync("ops/monitor-clock/poststeward-clock-alerts.timer", "utf8").includes("OnCalendar=*-*-* *:00/5:00 UTC"));
  assert.doesNotMatch(workflow, /contents: write|actions: write|secrets: inherit|wrangler deploy|publish_now|schedule_create/);
});

test("routine monitor dispatch cannot run or evaluate a fire drill", () => {
  for (const name of [
    "Exercise all reviewed alert classes and failed-path fallback",
    "Evaluate fire-drill evidence",
  ]) {
    const step = workflow.split(`- name: ${name}\n`)[1]?.split("\n      - ")[0];
    assert.ok(step, `Missing fire-drill step: ${name}`);
    const condition = step.match(/^        if: (.+)$/m)?.[1];
    assert.equal(condition, "github.event_name == 'push' || github.event_name == 'workflow_dispatch'");
  }
});
test("monitor clock durably fences interrupted submissions and reconciles without replay", () => {
  const result = spawnSync("python3", ["-B", "-m", "unittest", "discover", "-s", "ops/monitor-clock", "-p", "test_dispatch.py"], {
    encoding: "utf8",
    timeout: 20_000,
  });
  assert.equal(result.status, 0, result.error?.message || `${result.stdout}\n${result.stderr}`);
});

test("read-only Cloudflare transport retries a transient failure and then succeeds", async () => {
  let calls = 0;
  const response = { ok: true };
  const result = await readOnlyCloudflareFetch("https://api.cloudflare.com/test", { method: "POST" }, async () => {
    calls++;
    if (calls === 1) throw new TypeError("fetch failed");
    return response;
  }, async () => {});
  assert.equal(result, response);
  assert.equal(calls, 2);
});
test("Cloudflare HTTP authorization failures are never retried", async () => {
  let calls = 0;
  const result = await readOnlyCloudflareFetch("https://api.cloudflare.com/test", { method: "POST" }, async () => {
    calls++;
    return { ok: false, status: 403 };
  }, async () => {});
  assert.equal(result.status, 403);
  assert.equal(calls, 1);
});
test("persistent read-only transport failure stops after a bounded number of attempts", async () => {
  let calls = 0;
  await assert.rejects(readOnlyCloudflareFetch("https://api.cloudflare.com/test", { method: "POST" }, async () => {
    calls++;
    throw new TypeError("fetch failed");
  }, async () => {}), /after 3 attempts/);
  assert.equal(calls, 3);
});
