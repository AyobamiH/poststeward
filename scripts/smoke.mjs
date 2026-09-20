import { readFileSync, appendFileSync } from "node:fs";
import { demand, validateConfiguration } from "./deployment-config.mjs";
import { verifyHosted } from "./hosted-checks.mjs";
import { verifyReleaseControl } from "./release-control-smoke.mjs";
const c = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
validateConfiguration(c);
const report = await verifyHosted(c);
console.log("POSTSTEWARD_HOSTED_REPORT " + JSON.stringify(report));
const control = await verifyReleaseControl(
  c.vars.PUBLIC_ORIGIN,
  c.vars.RELEASE_SHA,
  fetch,
  {
    enabled: c.vars.ADVANCED_ENABLED === "true",
    mode: c.vars.ADVANCED_ROLLOUT_MODE || "disabled",
    bps: Number(c.vars.ADVANCED_CANARY_BPS || "0"),
  },
);
console.log("POSTSTEWARD_RELEASE_CONTROL_REPORT " + JSON.stringify(control));
if (process.env.GITHUB_STEP_SUMMARY)
  appendFileSync(
    process.env.GITHUB_STEP_SUMMARY,
    "PostSteward hosted acceptance (no social posts or payments):\n\n```json\n" +
      JSON.stringify(report, null, 2) +
      "\n```\n\nRelease/capability control plane:\n\n```json\n" +
      JSON.stringify(control, null, 2) +
      "\n```\n",
  );
demand(
  report.passed,
  "Hosted acceptance failed. Inspect fixed check names/statuses in the report; do not infer success from upload alone.",
);
demand(
  control.passed,
  "Release/capability control-plane verification failed. Do not broaden product claims or policy from configuration alone.",
);
