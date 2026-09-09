import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { catalog, describe } from "../src/operations/catalog.ts";
const check = process.argv.includes("--check");
const text =
  "# PostSteward agent operation reference\n\nGenerated from src/operations/catalog.ts. Do not edit by hand.\n\n" +
  catalog
    .map(
      (o) =>
        `## ${o.name}\n\n${o.description}\n\n- Tier: ${o.tier}\n- Required scope: ${o.scope}\n- Effects: ${o.effects.join(", ")}\n- Inspect with: ${o.inspection}\n- Retry: ${o.retry}\n\nExample:\n\n\`\`\`json\n${JSON.stringify(o.example, null, 2)}\n\`\`\`\n`,
    )
    .join("\n");
const outputs: Record<string, string> = {
  "docs/operations.md": text,
  "public/docs/operations.md": text,
  "public/catalog.json": JSON.stringify(catalog.map(describe), null, 2) + "\n",
};
for (const [path, content] of Object.entries(outputs)) {
  if (check) {
    if (readFileSync(path, "utf8") !== content)
      throw new Error("Generated documentation drift: " + path);
  } else {
    mkdirSync(path.slice(0, path.lastIndexOf("/")), { recursive: true });
    writeFileSync(path, content);
  }
}
console.log(
  `${check ? "Checked" : "Generated"} ${Object.keys(outputs).length} files for ${catalog.length} operations.`,
);
