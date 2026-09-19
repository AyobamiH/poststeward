import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { PUBLIC_LAUNCH_RESPONSIBILITIES } from "../scripts/public-launch-ops-drill.mjs";

const script = readFileSync("scripts/public-launch-ops-drill.mjs", "utf8");
const workflow = readFileSync(".github/workflows/public-launch-operations.yml", "utf8");

test("public launch drill covers every reviewed operator responsibility", () => {
  assert.deepEqual(PUBLIC_LAUNCH_RESPONSIBILITIES, [
    "support_intake",
    "abuse_escalation",
    "privacy_data_request",
    "incident_command",
    "provider_outage",
    "emergency_publishing_pause",
  ]);
  assert.match(script, /assignees: \["AyobamiH"\]/);
  assert.match(script, /created_readback_assigned_closed/);
  assert.match(script, /publicAdmissionChanged: false/);
  assert.match(script, /providerEffectAttempted: false/);
});

test("public launch drill is production-bound and cannot open signup", () => {
  assert.match(workflow, /environment:\n      name: production/);
  assert.match(workflow, /POSTSTEWARD_ORIGIN: https:\/\/app\.poststeward\.com/);
  assert.match(workflow, /j\.access\?\.signupMode!=="restricted"/);
  assert.match(workflow, /issues: write/);
  assert.match(workflow, /contents: read/);
  assert.doesNotMatch(
    workflow,
    /wrangler deploy|SIGNUP_MODE: public|publish_now|schedule_create|secrets: inherit/,
  );
});
