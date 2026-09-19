import assert from "node:assert/strict";
import test from "node:test";
import {
  buildCapacityObservation,
  capacityAnalyticsRequest,
  capacityFailureCode,
  classifyCapacitySampleAbsence,
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
    providers: [{
      provider: "threads",
      observed: true,
      quotaTotalPerUser: 250,
      poststewardDailyDeliveryCeilingPerWorkspace: 20,
    }],
  };
}
function pricing() {
  return {
    evidenceClass: "reviewed_pricing",
    observedAt: Date.UTC(2026, 8, 15),
    monthlyEstimate: 5,
    model: {
      includedWorkerRequestsPerMonth: 10_000_000,
      includedWorkerCpuMsPerMonth: 30_000_000,
      includedD1RowsReadPerMonth: 25_000_000_000,
      includedD1RowsWrittenPerMonth: 50_000_000,
    },
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
  assert.equal(report.costEvidence.providerQuotaHeadroomSatisfied, true);
  assert.equal(report.costEvidence.estimateRecorded, true);
  assert.equal(report.costEvidence.pricingEnvelopeSatisfied, true);
  assert.equal(report.ready, true);
  assert.equal(
    report.providerPollObservation.method,
    "workspace_alarm_cycles_upper_bound",
  );
  assert.equal(report.sampledWorkspaces, 6);
  assert.equal(report.projection.targetWorkspaces, 6);
});
test("hosted per-workspace high-water can be projected to the reviewed first-100 target", () => {
  const report = buildCapacityObservation({
    highWater: highWater({ workspaces: 3 }),
    workerAnalytics: worker,
    d1Analytics: d1,
    providerQuota: providerQuota(),
    pricing: pricing(),
    windowDays: 7,
    targetWorkspaces: 100,
  });
  assert.equal(report.sampledWorkspaces, 3);
  assert.equal(report.workspaces, 100);
  assert.equal(report.projection.targetWorkspaces, 100);
  assert.equal(
    report.projection.method,
    "hosted_per_workspace_high_water_projected_to_target",
  );
  assert.equal(report.calibration.workspaces, 100);
  assert.equal(report.ready, true);
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
  assert.equal(report.costEvidence.providerQuotaHeadroomSatisfied, false);
  assert.equal(report.costEvidence.estimateRecorded, false);
  assert.equal(report.costEvidence.pricingEnvelopeSatisfied, false);
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
  assert.match(
    request.query,
    /d1AnalyticsAdaptiveGroups[\s\S]*sum \{[^}]*queryBatchResponseBytes[^}]*\}[\s\S]*quantiles \{ queryBatchTimeMsP90 \}/,
  );
  assert.doesNotMatch(
    request.query,
    /sum \{[^}]*queryBatchTimeMs(?:\s|\})/,
  );
});

test("capacity observer emits bounded failure codes without reflecting exception text", () => {
  assert.equal(
    capacityFailureCode(new Error("Cloudflare observation failed with HTTP 403.")),
    "cloudflare_http_403",
  );
  assert.equal(
    capacityFailureCode(
      new Error("Cloudflare GraphQL capacity query returned errors."),
    ),
    "cloudflare_graphql_query_rejected",
  );
  assert.equal(
    capacityFailureCode(new Error("protected-value-must-not-be-reflected")),
    "invalid_input_or_observation",
  );
});

test("zero-sample capacity evidence distinguishes registry, sweep and release drift", () => {
  const context = {
    release: "b".repeat(40),
    environment: "staging",
    origin: "https://poststeward-staging.example.com",
  };
  assert.equal(
    classifyCapacitySampleAbsence(context, {
      registeredWorkspaces: 0,
      totalObservationRows: 0,
    }).blocker,
    "no_registered_workspaces_for_capacity_observation",
  );
  assert.equal(
    classifyCapacitySampleAbsence(context, {
      registeredWorkspaces: 2,
      totalObservationRows: 0,
    }).blocker,
    "no_capacity_sweep_rows_despite_registered_workspaces",
  );
  assert.equal(
    classifyCapacitySampleAbsence(context, {
      registeredWorkspaces: 2,
      totalObservationRows: 4,
      latestObservedAt: 123,
      latestRelease: "a".repeat(40),
    }).blocker,
    "capacity_rows_only_for_older_release",
  );
  assert.equal(
    classifyCapacitySampleAbsence(context, {
      registeredWorkspaces: 2,
      totalObservationRows: 4,
      latestObservedAt: 123,
      latestRelease: context.release,
    }).blocker,
    "no_current_release_capacity_samples_in_window",
  );
});

test("absence report exposes only bounded operational counts and release timing", () => {
  const context = {
    release: "b".repeat(40),
    environment: "staging",
    origin: "https://poststeward-staging.example.com",
  };
  const report = insufficientCapacityObservation(
    context,
    {
      registeredWorkspaces: 3,
      totalObservationRows: 8,
      latestObservedAt: 654321,
      latestRelease: "a".repeat(40),
    },
    700000,
  );
  assert.equal(report.evidenceClass, "insufficient_observation");
  assert.equal(report.release, context.release);
  assert.equal(report.environment, "staging");
  assert.equal(report.workspaces, 0);
  assert.equal(report.ready, false);
  assert.deepEqual(report.diagnostics, {
    registeredWorkspaces: 3,
    totalObservationRows: 8,
    latestObservedAt: 654321,
    latestRelease: "a".repeat(40),
  });
  assert.deepEqual(report.blockers, ["capacity_rows_only_for_older_release"]);
  assert.equal(JSON.stringify(report).includes("workspace_fingerprint"), false);
});
