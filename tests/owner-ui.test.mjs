import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  grantState,
  receiptState,
  operationSummary,
  accountReadbackLabel,
  providerConnectionCapabilityLabel,
} from "../public/owner-ui.js";

const ui = readFileSync("public/owner-ui.js", "utf8");
const css = readFileSync("public/production-ux.css", "utf8");
const presented = readFileSync("src/presented-edge.ts", "utf8");

test("owner presentation consumes structured snapshots without taking operational authority", () => {
  assert.match(ui, /renderOwnerSnapshot/);
  assert.match(ui, /ux-provider-grid/);
  assert.match(ui, /ux-receipt-filter/);
  assert.doesNotMatch(ui, /fetch\(|\/api\/operations\/|publish_now|schedule_create|automation_enable/);
  assert.doesNotMatch(ui, /localStorage|sessionStorage|document\.cookie|innerHTML/);
  assert.match(
    readFileSync("public/app.js", "utf8"),
    /renderOwnerSnapshot\(\{ accounts, receipts, profiles, grants, oauthInfo, recovery \}\)/,
  );
});

test("expired and unproven grants never appear active", () => {
  assert.deepEqual(grantState({ expires_at: 99 }, 100), ["Expired", "paused"]);
  assert.deepEqual(grantState({}, 100), ["Expiry unknown", "warning"]);
  assert.deepEqual(grantState({ expires_at: 101 }, 100), ["Active", "connected"]);
  assert.deepEqual(grantState({ revoked_at: 50, expires_at: 101 }, 100), ["Revoked", "revoked"]);
});

test("unknown server receipt states remain unproven", () => {
  for (const state of ["future_state", "constructor", "toString", undefined])
    assert.deepEqual(receiptState(state), ["Unknown state", "warning"]);
  assert.deepEqual(receiptState("published_verified"), ["Verified", "verified"]);
  assert.deepEqual(receiptState("published_unverified"), ["Needs readback", "warning"]);
  assert.deepEqual(receiptState("ambiguous_effect"), ["Ambiguous effect", "ambiguous"]);
});

test("Threads baseline readback omission is labelled as unconfirmed scope evidence, not generic unknown", () => {
  assert.equal(
    accountReadbackLabel(
      {
        provider: "threads",
        capabilities: { readback: true },
      },
      {
        capabilities: {
          readback: {
            state: "unknown",
            reason: "scope_response_absent",
            requiredScopes: ["threads_basic"],
          },
        },
      },
    ),
    "Baseline requested; provider did not echo scope",
  );
  assert.equal(
    accountReadbackLabel(
      { provider: "threads", capabilities: { readback: true } },
      { capabilities: { readback: { state: "available" } } },
    ),
    "Available to this grant",
  );
});

test("provider cards distinguish application setup from active connection capability", () => {
  const account = {
    alias: "primary",
    active: true,
    capabilities: { readback: true, refresh: true, metrics: false },
  };
  const connection = {
    alias: "primary",
    capabilities: {
      publish: { state: "available" },
      readback: { state: "unknown", reason: "scope_response_absent" },
      refresh: { state: "available" },
      metrics: { state: "unavailable" },
    },
  };
  assert.equal(providerConnectionCapabilityLabel("publish", [account], [connection]), "Available on 1 active connection");
  assert.equal(providerConnectionCapabilityLabel("readback", [account], [connection]), "Available on 1 active connection");
  assert.equal(providerConnectionCapabilityLabel("metrics", [account], [connection]), "Unavailable on 1 active connection");
  assert.equal(providerConnectionCapabilityLabel("publish", [], []), undefined);
});

test("response summaries do not promote reservation or uncertainty to success", () => {
  assert.doesNotMatch(operationSummary({ id: "delivery" }), /completed|published successfully/i);
  assert.match(operationSummary([{ status: "scheduled" }]), /Scheduled/);
  assert.match(operationSummary({ status: "ambiguous_effect" }), /Ambiguous effect/);
  assert.match(operationSummary({ accepted: true, restartInProgress: true }), /still in progress/);
});

test("production UX keeps semantic status roles and hidden filtering intact", () => {
  for (const token of ["success", "warning", "danger"])
    assert.ok(css.includes(`var(--${token})`));
  assert.match(readFileSync("public/accessibility.css", "utf8"), /\[hidden\]\s*\{\s*display:\s*none\s*!important/);
  assert.match(ui, /latest 50, not the complete history/);
});

test("presentation wrapper delegates runtime authority instead of replacing it", () => {
  assert.match(presented, /import handler, \{ Workspace \} from "\.\/canary-edge\.ts"/);
  assert.match(presented, /handler\.fetch\(request, env, ctx\)/);
  assert.match(presented, /handler\.scheduled\(controller, env, ctx\)/);
  assert.match(presented, /presentBrowserResponse\(request, response, env\)/);
});
