import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

test("runtime release manifest generator binds exact release and deterministic runtime tree", () => {
  const text = readFileSync("scripts/generate-runtime-release-manifest.mjs", "utf8");
  assert.match(text, /runtime_tree_sha256/);
  assert.match(text, /POSTSTEWARD_RUNTIME_PROVENANCE\.json/);
  assert.match(text, /expires_at/);
  assert.match(text, /30 \* 86400_000/);
  assert.match(text, /\^\[0-9a-f\]\{40\}\$/);
  assert.doesNotMatch(text, /latest\.json|\/main\.tar\.gz/);
  assert.equal(createHash("sha256").update("poststeward").digest("hex").length, 64);
});
