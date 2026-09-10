import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

test("external gate document names exact staging callbacks and forbids evidence fabrication", () => {
  const text = readFileSync("docs/external-gates.md", "utf8");
  for (const provider of ["x", "threads", "linkedin"])
    assert.match(text, new RegExp(`https://poststeward-staging\\.woeinvests\\.workers\\.dev/connections/oauth/${provider}/callback`));
  assert.match(text, /does not reuse credentials from another product/);
  assert.match(text, /No CI workflow may insert an owner session, provider token or acceptance receipt/);
  assert.match(text, /must not initiate point-in-time restore/);
});
