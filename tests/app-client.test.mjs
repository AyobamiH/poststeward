import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  oauthHosts,
  recoveryActionPayload,
  recoveryControlState,
  trustedExternal,
} from "../public/app-client.js";

test("external browser navigation accepts only exact HTTPS provider and Stripe hosts", () => {
  assert.equal(trustedExternal("https://x.com/i/oauth2/authorize?x=1", oauthHosts("x")), "https://x.com/i/oauth2/authorize?x=1");
  assert.equal(trustedExternal("https://threads.net/oauth/authorize", oauthHosts("threads")), "https://threads.net/oauth/authorize");
  assert.equal(trustedExternal("https://www.linkedin.com/oauth/v2/authorization", oauthHosts("linkedin")), "https://www.linkedin.com/oauth/v2/authorization");
  for (const value of [
    "https://x.com.evil.example/i/oauth2/authorize",
    "http://x.com/i/oauth2/authorize",
    "https://user:secret@x.com/i/oauth2/authorize",
    "javascript:alert(1)",
  ]) assert.equal(trustedExternal(value, oauthHosts("x")), undefined);
  assert.equal(trustedExternal("https://checkout.stripe.com/c/pay/test", ["checkout.stripe.com"]), "https://checkout.stripe.com/c/pay/test");
  assert.equal(trustedExternal("https://stripe.example/c/pay/test", ["checkout.stripe.com"]), undefined);
});

test("recovery controls follow the server plan state and never infer authority from a digest alone", () => {
  const digest = "a".repeat(64);
  assert.deepEqual(recoveryControlState({ control: { quarantined: false }, plan: null }, 100), {
    cancel: false, execute: false, reconcile: false, resume: false, undo: false,
  });
  assert.deepEqual(recoveryControlState({ control: { quarantined: true }, plan: { state: "prepared", expiresAt: 200, digest } }, 100), {
    cancel: true, execute: true, reconcile: false, resume: false, undo: false,
  });
  assert.equal(recoveryControlState({ control: { quarantined: true }, plan: { state: "prepared", expiresAt: 100, digest } }, 100).execute, false);
  assert.equal(recoveryControlState({ control: { quarantined: true }, plan: { state: "armed", expiresAt: 200, digest } }, 100).reconcile, true);
  assert.equal(recoveryControlState({ control: { quarantined: true }, plan: { state: "reconciled", undoAvailable: true, digest } }, 100).resume, true);
  assert.equal(recoveryControlState({ control: { quarantined: false }, plan: { state: "reconciled", undoAvailable: true, digest } }, 100).resume, false);
});

test("recovery action payload is digest-bound and rejects incomplete plans", () => {
  const plan = { id: "00000000-0000-4000-8000-000000000001", digest: "b".repeat(64) };
  assert.deepEqual(recoveryActionPayload(plan, "execute"), { id: plan.id, digest: plan.digest, execute: true });
  assert.throws(() => recoveryActionPayload({ id: plan.id, digest: "wrong" }, "execute"));
});

test("workspace source clears failed manual tokens, uses no browser storage, and requires explicit restore confirmation", () => {
  const source = readFileSync("public/app.js", "utf8");
  assert.doesNotMatch(source, /localStorage|sessionStorage|document\.cookie|innerHTML/);
  assert.match(source, /field\.value = ""/);
  assert.match(source, /window\.confirm/);
  assert.match(source, /\/api\/recovery\/prepare/);
  assert.match(source, /\/api\/connections\/oauth\//);
  const html = readFileSync("public/app.html", "utf8");
  assert.match(html, /Manual token import fallback/);
  assert.match(html, /Workspace recovery/);
  assert.match(html, /data-provider="threads" disabled/);
});
