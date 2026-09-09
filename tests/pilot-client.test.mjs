import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { confirmation, mayApprove, mayReadback, receiptLabel, safePostLink, createClient } from "../public/pilot-client.js";
const now = 1000000;
const snapshot = { owner: { id: "owner-proof", authenticatedAt: now, expiresAt: now + 86400000 }, record: {
  id: "review", state: "prepared", reviewDigest: "a".repeat(64), expiresAt: now + 600000,
  owner: { id: "owner-proof" }, readbackAttempts: 0,
} };
test("client approval requires the exact pending review, explicit consent and a fresh owner proof", () => {
  assert.equal(mayApprove(snapshot, false, now), false); assert.equal(mayApprove(snapshot, true, now), true);
  assert.equal(mayApprove(snapshot, true, now + 600001), false);
  assert.equal(mayApprove({ ...snapshot, owner: { ...snapshot.owner, id: "changed" } }, true, now), false);
  assert.deepEqual(confirmation(snapshot.record), { reviewId: "review", reviewDigest: "a".repeat(64), approve: true });
  assert.throws(() => confirmation({ ...snapshot.record, deliveryId: "existing" }));
});
test("UI never represents a reservation or creation ID as completed independent verification", () => {
  const result = { ...snapshot, record: { ...snapshot.record, state: "reserved" }, delivery: { postId: "42", status: "published_unverified" } };
  assert.match(receiptLabel(result), /not yet passed/); assert.equal(mayReadback(result, now), true);
  assert.equal(mayReadback({ ...result, record: { ...result.record, readbackAttempts: 8 } }, now), false);
  assert.match(receiptLabel({ ...result, delivery: { status: "ambiguous_effect" } }), /Do not republish/);
  assert.doesNotMatch(receiptLabel({ ...result, completed: true }), /^Verified:/);
  assert.match(receiptLabel({ ...result, completed: true, record: { ...result.record, firstVerified: { at: now } } }), /^Verified:/);
});
test("browser transport uses CSRF, same-origin credentials, no-store and never retries a failed confirmation", async () => {
  let calls = 0;
  const client = createClient(async (path, options) => {
    calls++; assert.equal(path, "/api/pilot/confirm"); assert.equal(options.headers["X-CSRF-Token"], "csrf-test");
    assert.equal(options.credentials, "same-origin"); assert.equal(options.mode, "same-origin");
    assert.equal(options.cache, "no-store"); assert.equal(options.redirect, "error"); assert.ok(options.signal);
    throw new Error("Network outcome unknown");
  }, () => "csrf-test");
  await assert.rejects(client("/api/pilot/confirm", confirmation(snapshot.record))); assert.equal(calls, 1);
});
test("receipt URLs and browser secret handling remain constrained", () => {
  assert.equal(safePostLink("https://x.com/i/web/status/123"), "https://x.com/i/web/status/123");
  assert.equal(safePostLink("https://x.com.evil.example/i/web/status/123"), undefined);
  assert.equal(safePostLink("javascript:alert(1)"), undefined);
  const browser = readFileSync("public/pilot.js", "utf8");
  assert.doesNotMatch(browser, /localStorage|sessionStorage|innerHTML|document\.cookie/);
  assert.ok(browser.includes('$("connection-token").value = ""'));
  assert.ok(browser.includes('dirty = true'));
  assert.ok(browser.includes('!dirty'));
  const html = readFileSync("public/pilot.html", "utf8");
  assert.match(html, /type="password"/); assert.match(html, /id="approve" type="checkbox" required/);
  assert.match(html, /<option value="">Choose a connected account<\/option>/);
});
