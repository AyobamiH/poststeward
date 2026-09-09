import { readFileSync, appendFileSync } from "node:fs";
import { demand, validateConfiguration } from "./deployment-config.mjs";
import { verifyHosted } from "./hosted-checks.mjs";
const c = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
validateConfiguration(c);
const report = await verifyHosted(c);
console.log("POSTSTEWARD_HOSTED_REPORT " + JSON.stringify(report));
if (process.env.GITHUB_STEP_SUMMARY)
  appendFileSync(process.env.GITHUB_STEP_SUMMARY, "PostSteward hosted acceptance (no social posts or payments):\n\n```json\n" + JSON.stringify(report, null, 2) + "\n```\n");
demand(report.passed, "Hosted acceptance failed. Inspect fixed check names/statuses in the report; do not infer success from upload alone.");
