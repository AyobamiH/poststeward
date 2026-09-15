import assert from "node:assert/strict";
import test from "node:test";
import { assessObservation, evaluatedObservation, observationTime, promotionExitCode,
  OBSERVATION_MAX_AGE_MS, OBSERVATION_CLOCK_SKEW_MS } from "../scripts/promotion-evidence.mjs";
const now = Date.parse("2026-09-15T18:00:00Z");
const context = { release: "a".repeat(40), environment: "staging", origin: "https://staging.example.com" };
function evidence(overrides = {}) {
  return { evidenceClass: "hosted_observation", ...context, observedAt: now, ready: true, ...overrides };
}
test("only fresh contextual evidence is eligible for review", () => {
  assert.deepEqual(assessObservation(evidence(), context, now), { ready: true, blockers: [] });
});
for (const [name, overrides, blocker] of [
  ["wrong release", { release: "b".repeat(40) }, "evidence_release_mismatch"],
  ["wrong environment", { environment: "production" }, "evidence_environment_mismatch"],
  ["wrong origin", { origin: "https://other.example.com" }, "evidence_origin_mismatch"],
  ["fixture", { evidenceClass: "fixture" }, "evidence_class_invalid"],
  ["stale", { observedAt: now - OBSERVATION_MAX_AGE_MS - 1 }, "evidence_stale"],
  ["future", { observedAt: now + OBSERVATION_CLOCK_SKEW_MS + 1 }, "evidence_from_future"],
  ["missing timestamp", { observedAt: undefined }, "evidence_timestamp_missing"],
  ["empty timestamp", { observedAt: "" }, "evidence_timestamp_missing"],
  ["null timestamp", { observedAt: null }, "evidence_timestamp_missing"],
  ["failed evaluation", { ready: false }, "evidence_checks_incomplete"],
]) test(`${name} cannot count as promotion evidence`, () => {
  const result = assessObservation(evidence(overrides), context, now);
  assert.equal(result.ready, false);
  assert.ok(result.blockers.includes(blocker));
});
test("an unbound ready flag is not a successful observation", () => {
  assert.equal(assessObservation({ ready: true }, context, now).ready, false);
  assert.equal(assessObservation(undefined, context, now).ready, false);
});
test("SLO verdict is read from promotion.ready rather than a nonexistent top-level ready", () => {
  const raw = { ...evidence(), cohort: { observedAt: now } };
  delete raw.observedAt;
  const result = evaluatedObservation("advanced_rollout", raw, { promotion: { ready: true } });
  assert.equal(result.ready, true);
  assert.equal(result.observedAt, now);
  assert.equal(assessObservation(result, context, now).ready, true);
});
test("raw ready and metadata cannot overwrite an evaluator failure", () => {
  assert.equal(evaluatedObservation("advanced_rollout", evidence(), { promotion: { ready: false } }).ready, false);
  assert.equal(evaluatedObservation("operational_alert_delivery", evidence(), { ready: false }).ready, false);
});
test("explicit empty observation timestamp is not replaced with cohort time", () => {
  const result = evaluatedObservation("advanced_rollout", { ...evidence({ observedAt: "" }), cohort: { observedAt: now } }, { promotion: { ready: true } });
  assert.equal(result.observedAt, "");
  assert.equal(assessObservation(result, context, now).ready, false);
});
test("timestamp parsing is strict and supports hosted ISO or millisecond clocks", () => {
  assert.equal(observationTime("2026-09-15T18:00:00Z"), now);
  assert.equal(observationTime(now), now);
  for (const value of ["", "2026-09-15", null, undefined, true, Infinity, -1])
    assert.ok(Number.isNaN(observationTime(value)));
});
test("report mode is not a deployment gate; enforce mode exits non-zero for blocked stages", () => {
  const blocked = { runtimePolicy: { healthy: true }, promotion: { ready: false } };
  assert.equal(promotionExitCode(blocked, "report"), 0);
  assert.equal(promotionExitCode(blocked, "enforce"), 2);
  assert.equal(promotionExitCode({ runtimePolicy: { healthy: true }, promotion: { ready: true } }, "enforce"), 0);
  assert.equal(promotionExitCode({ ...blocked, runtimePolicy: { healthy: false } }, "report"), 2);
  for (const mode of ["", "automatic", "global"]) assert.throws(() => promotionExitCode(blocked, mode));
});
