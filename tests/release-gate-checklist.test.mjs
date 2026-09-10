import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

test("release checklist does not allow billing/public release before external acceptance", () => {
  const text = readFileSync("docs/release-gate-checklist.md", "utf8");
  const controlled = text.indexOf("controlled publication");
  const stripe = text.indexOf("Stripe sandbox lifecycle");
  const publicRelease = text.indexOf("public signup or production deployment");
  assert.ok(controlled > 0 && stripe > controlled && publicRelease > stripe);
  assert.match(text, /remain `restricted staging`/);
});
