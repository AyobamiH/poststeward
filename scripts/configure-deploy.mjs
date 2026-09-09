import { readFileSync, writeFileSync } from "node:fs";
import {
  buildConfiguration,
  resolveCloudflareConfiguration,
} from "./deployment-config.mjs";
const c = buildConfiguration(
  JSON.parse(readFileSync("wrangler.jsonc", "utf8")),
  await resolveCloudflareConfiguration(process.env),
);
writeFileSync("wrangler.jsonc", JSON.stringify(c, null, 2) + "\n");
console.log(
  `Configured ${c.name}; origin ${c.vars.PUBLIC_ORIGIN}; database ${c.d1_databases[0].database_name}.`,
);
