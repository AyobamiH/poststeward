import { readFileSync, writeFileSync } from "node:fs";
import { configureRootRotation } from "./root-rotation-deploy-config.mjs";

const path = "wrangler.jsonc";
const configured = configureRootRotation(
  JSON.parse(readFileSync(path, "utf8")),
  process.env,
);
writeFileSync(path, JSON.stringify(configured, null, 2) + "\n");
console.log(
  `Root rotation deployment mode ${configured.vars.ROOT_ROTATION_MODE}; publishing paused ${configured.vars.PUBLISHING_PAUSED}.`,
);
