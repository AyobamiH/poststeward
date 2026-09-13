import { readFile, writeFile } from "node:fs/promises";
import { rehearseRootInventory } from "../src/root-rotation-inventory.ts";

try {
  const [input, privateOutput] = process.argv.slice(2);
  if (!input || !privateOutput || process.argv.length !== 4)
    throw new Error("USAGE");
  const oldRoot = process.env.POSTSTEWARD_CURRENT_ROOT;
  const nextRoot = process.env.POSTSTEWARD_NEXT_ROOT;
  if (!oldRoot || !nextRoot) throw new Error("PROTECTED_KEYS_REQUIRED");
  const bytes = await readFile(input);
  if (bytes.length > 64 * 1024 * 1024) throw new Error("INVENTORY_TOO_LARGE");
  const snapshot = JSON.parse(bytes.toString("utf8"));
  const result = await rehearseRootInventory(snapshot, oldRoot, nextRoot);
  // Never overwrite a previous migration plan. Ciphertexts stay in a private
  // operator file; stdout contains only the non-secret rehearsal evidence.
  await writeFile(privateOutput, JSON.stringify(result, null, 2), { mode: 0o600, flag: "wx" });
  console.log(JSON.stringify(result.evidence, null, 2));
} catch {
  console.error("Root inventory rehearsal failed. No live credentials or protected runtime keys were changed.");
  process.exitCode = 1;
} finally {
  delete process.env.POSTSTEWARD_CURRENT_ROOT;
  delete process.env.POSTSTEWARD_NEXT_ROOT;
}
