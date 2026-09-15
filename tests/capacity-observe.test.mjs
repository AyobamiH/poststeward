import assert from "node:assert/strict";
import test from "node:test";
import {
  buildCapacityObservation,
  capacityAnalyticsRequest,
  insufficientCapacityObservation,
} from "../scripts/capacity-observe.mjs";

function highWater(overrides = {}) {
  return {
    workspaces: 6,
    peakRecordsPerWorkspace: 1000,
    peakBytesPerWorkspace: 1024 * 1024,
    maxValueBytes: 4096,
    peakDailyDeliveries: 5,
    peakActiveSchedules: 20,
    peakProfiles: 3,
    requestsPerWorkspaceDay: 500,
    alarmCyclesPerWorkspaceDay: 24,
    ...overrides,
  };
}

const worker = { requests: 1000, subrequests: 100, errors: 0, cpuTimeP50: 2, cpuTimeP99: 10 };
const d1 = { readQueries: 100, writeQueries: 20, rowsRead: 1000, rowsWritten: 100 };

function providerQuota() {
  return {
    evidenceClass: "provider_observation",
    observedAt: Date.UTC(2026, 8, 15),
    providers: [{ provider: "threads", observed: true }],
  };
}
function pricing() {
  return {
    evidenceClass: "reviewed_pricing",
    observedAt: Date.UTC(2026, 8, 15),
    monthlyEstimate: 12.5,
  };
}

test("complete hosted observation can satisfy calibration and cost evidence", () => {
  const report = buildCapacityObservation({
    highWater: highWater(),
    workerAnalytics: worker,
    d1Analytics: d1,
    providerQuota: providerQuota(),
    pricing: pricing(),
    windowDays: 7,
  });
  assert.equal(report.calibration.verdict, "calibrated_with_30pct_headroom");
  assert.equal(report.costEvidence.cloudflareObserved, true);
  assert.equal(report.costEvidence.providerQuotaObserved, true);
  assert.equal(report.costEvidence.estimateRecorded, true);
  assert.equal(report.ready, true);
  assert.equal(
    report.providerPollObservation.method,
    "workspace_alarm_cycles_upper_bound",
  );
});

test("Cloudflare metrics alone never invent provider quota or reviewed pricing", () => {
  const report = buildCapacityObservation({
    highWater: highWater(),
    workerAnalytics: worker,
    d1Analytics: d1,
    providerQuota: null,
    pricing: null,
    windowDays: 7,
  });
  assert.equal(report.costEvidence.cloudflareObserved, true);
  assert.equal(report.costEvidence.providerQuotaObserved, false);
  assert.equal(report.costEvidence.estimateRecorded, false);
  assert.equal(report.costEvidence.monthlyEstimate, null);
  assert.equal(report.ready, false);
});

test("product headroom failure blocks readiness even with complete cost evidence", () => {
  const report = buildCapacityObservation({
    highWater: highWater({ peakActiveSchedules: 99 }),
    workerAnalytics: worker,
    d1Analytics: d1,
    providerQuota: providerQuota(),
    pricing: pricing(),
    windowDays: 7,
  });
  assert.notEqual(report.calibration.verdict, "calibrated_with_30pct_headroom");
  assert.equal(report.ready, false);
});

test("capacity observations reject negative or fractional high-water values", () => {
  assert.throws(
    () =>
      buildCapacityObservation({
        highWater: highWater({ peakProfiles: -1 }),
        workerAnalytics: worker,
        d1Analytics: d1,
        providerQuota: providerQuota(),
        pricing: pricing(),
        windowDays: 7,
      }),
    /peakProfiles/,
  );
});

test("Cloudflare Workers and D1 analytics use their distinct time filter contracts", () => {
  const request = capacityAnalyticsRequest(
    "a".repeat(32),
    "poststeward-staging",
    "00000000-0000-4000-8000-000000000001",
    "2026-09-09T12:30:00.000Z",
    "2026-09-15T12:30:00.000Z",
  );
  assert.match(
    request.query,
    /workersInvocationsAdaptive[\s\S]*datetime_geq: \$start, datetime_leq: \$end/,
  );
  assert.match(
    request.query,
    /d1AnalyticsAdaptiveGroups[\s\S]*date_geq: \$dateStart, date_leq: \$dateEnd/,
  );
  assert.match(request.query, /\$dateStart: Date, \$dateEnd: Date/);
  assert.equal(request.variables.dateStart, "2026-09-09");
  assert.equal(request.variables.dateEnd, "2026-09-15");
  assert.doesNotMatch(
    request.query,
    /d1AnalyticsAdaptiveGroups[\s\S]*datetime_geq/,
  );
});

test("absence of release-bound capacity samples is insufficient evidence, not an infrastructure failure", () => {
  const context = {
    release: "b".repeat(40),
    environment: "staging",
    origin: "https://poststeward-staging.example.com",
  };
  const report = insufficientCapacityObservation(context, 123456);
  assert.equal(report.evidenceClass, "insufficient_observation");
  assert.equal(report.release, context.release);
  assert.equal(report.environment, "staging");
  assert.equal(report.workspaces, 0);
  assert.equal(report.ready, false);
  assert.deepEqual(report.blockers, [
    "no_hosted_workspace_capacity_samples_for_release",
  ]);
});
