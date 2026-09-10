import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

test("release readiness keeps external gates separate from implementation", () => {
  const text = readFileSync("docs/release-readiness.md", "utf8");
  for (const phrase of [
    "real owner completion still required",
    "one real provider write + separate readback still required",
    "provider app approval + consent required",
    "one deliberate owner rehearsal still required",
    "supported-browser authenticated execution required",
    "Stripe sandbox lifecycle required",
    "account-level branch rule still required",
  ]) assert.match(text, new RegExp(phrase.replace(/[+]/g, "\\+")));
  assert.match(text, /purchases disabled/);
  assert.match(text, /Public signup[\s\S]*restricted/);
});
