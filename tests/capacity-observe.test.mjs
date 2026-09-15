import assert from "node:assert/strict";
import test from "node:test";
import { buildCapacityObservation } from "../scripts/capacity-observe.mjs";

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
