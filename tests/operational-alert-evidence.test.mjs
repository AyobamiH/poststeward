import assert from "node:assert/strict";
import test from "node:test";
import {
  REQUIRED_ALERT_CLASSES,
  evaluateAlertEvidence,
} from "../scripts/operational-alert-evidence.mjs";

function evidence() {
  const testedAt = "2026-09-15T12:00:00.000Z";
  const receivedAt = "2026-09-15T12:00:10.000Z";
  return {
    schemaVersion: 1,
    evidenceClass: "hosted_observation",
    environment: "staging",
    release: "a".repeat(40),
    observedAt: "2026-09-15T12:05:00.000Z",
    configuredPaths: ["primary-webhook", "fallback-email"],
    deliveries: REQUIRED_ALERT_CLASSES.flatMap((alertClass, index) => [
      {
        alertClass,
        path: "primary-webhook",
        testedAt,
        receivedAt,
        acknowledged: true,
        result: `received-${index}`,
      },
      ...(index === 0
        ? [
            {
              alertClass,
              path: "fallback-email",
              testedAt,
              receivedAt,
              acknowledged: true,
              result: "received-fallback",
            },
          ]
        : []),
    ]),
    failedPath: {
      path: "primary-webhook-disabled-test",
      testedAt,
      escalationObserved: true,
      fallbackPath: "fallback-email",
    },
  };
}

test("complete hosted alert evidence is promotion-ready", () => {
  const result = evaluateAlertEvidence(evidence());
  assert.equal(result.ready, true);
  assert.deepEqual(result.missingClasses, []);
  assert.deepEqual(result.untestedPaths, []);
  assert.equal(result.failedPathEscalation.observed, true);
});

test("missing an alert class fails closed", () => {
  const value = evidence();
  value.deliveries = value.deliveries.filter(
    (delivery) => delivery.alertClass !== "stripe_reconciliation",
  );
  const result = evaluateAlertEvidence(value);
  assert.equal(result.ready, false);
  assert.deepEqual(result.missingClasses, ["stripe_reconciliation"]);
});

test("a configured but untested delivery path fails closed", () => {
  const value = evidence();
  value.configuredPaths.push("pager-path");
  const result = evaluateAlertEvidence(value);
  assert.equal(result.ready, false);
  assert.deepEqual(result.untestedPaths, ["pager-path"]);
});

test("failed notification evidence must prove a distinct fallback", () => {
  const value = evidence();
  value.failedPath.fallbackPath = value.failedPath.path;
  assert.throws(
    () => evaluateAlertEvidence(value),
    /Escalation fallback must be a different delivery path/,
  );
});
