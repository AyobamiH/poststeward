import { readFileSync } from "node:fs";
import { validateConfiguration } from "./deployment-config.mjs";
try {
  validateConfiguration(
    JSON.parse(
      readFileSync(process.env.DEPLOY_CONFIG || "wrangler.jsonc", "utf8"),
    ),
  );
  console.log(
    "Deployment configuration validated. Resource identity and secrets are checked by deploy-environment.mjs before migration.",
  );
} catch (e) {
  console.error(e.message);
  process.exitCode = 1;
}
