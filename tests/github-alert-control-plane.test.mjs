import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { alertOutputDocument } from "../scripts/github-alert-control-plane.mjs";
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
  assert.match(workflow, /cron: "\*\/5 \* \* \* \*"/);
  assert.doesNotMatch(workflow, /contents: write|actions: write|secrets: inherit|wrangler deploy|publish_now|schedule_create/);
});
