import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { readFileSync } from "node:fs";

export const defaultHardLimits = Object.freeze({
  records: 20_000,
  bytes: 16 * 1024 * 1024,
  valueBytes: 128 * 1024,
  dailyDeliveries: 20,
  activeSchedules: 100,
  sourceProfiles: 10,
});

function finite(value, name, minimum = 0) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < minimum)
    throw new Error(`${name} must be a finite number >= ${minimum}.`);
  return number;
}

export function evaluateCapacity(observation, hard = defaultHardLimits) {
  const workspaces = finite(observation.workspaces, "workspaces", 1);
  const peakRecords = finite(observation.peakRecordsPerWorkspace, "peakRecordsPerWorkspace");
  const peakBytes = finite(observation.peakBytesPerWorkspace, "peakBytesPerWorkspace");
  const maxValueBytes = finite(observation.maxValueBytes, "maxValueBytes");
  const peakDailyDeliveries = finite(observation.peakDailyDeliveries, "peakDailyDeliveries");
  const peakActiveSchedules = finite(observation.peakActiveSchedules, "peakActiveSchedules");
  const peakProfiles = finite(observation.peakProfiles, "peakProfiles");
  const requestsPerWorkspaceDay = finite(
    observation.requestsPerWorkspaceDay,
    "requestsPerWorkspaceDay",
  );
  const providerPollsPerWorkspaceDay = finite(
    observation.providerPollsPerWorkspaceDay,
    "providerPollsPerWorkspaceDay",
  );

  const ratios = {
    records: peakRecords / hard.records,
    bytes: peakBytes / hard.bytes,
    valueBytes: maxValueBytes / hard.valueBytes,
    dailyDeliveries: peakDailyDeliveries / hard.dailyDeliveries,
    activeSchedules: peakActiveSchedules / hard.activeSchedules,
    sourceProfiles: peakProfiles / hard.sourceProfiles,
  };
  const highest = Math.max(...Object.values(ratios));
  const headroom = 1 - highest;
  return {
    workspaces,
    hardLimits: hard,
    observed: {
      peakRecordsPerWorkspace: peakRecords,
      peakBytesPerWorkspace: peakBytes,
      maxValueBytes,
      peakDailyDeliveries,
      peakActiveSchedules,
      peakProfiles,
      requestsPerWorkspaceDay,
      providerPollsPerWorkspaceDay,
    },
    ratios,
    minimumHeadroomFraction: headroom,
    projectedDailyRequests: workspaces * requestsPerWorkspaceDay,
    projectedDailyProviderPolls: workspaces * providerPollsPerWorkspaceDay,
    verdict: headroom >= 0.3 ? "calibrated_with_30pct_headroom" : "insufficient_headroom",
    note:
      "This evaluates retained-state and product ceilings only. Cloudflare and provider cost observations must come from a real staging/load run before customer-facing limits are published.",
  };
}

async function main() {
  const path = process.argv[2];
  if (!path)
    throw new Error("Usage: node scripts/capacity-calibration.mjs <observation.json>");
  const observation = JSON.parse(readFileSync(path, "utf8"));
  const result = evaluateCapacity(observation);
  console.log(JSON.stringify(result, null, 2));
  if (result.verdict !== "calibrated_with_30pct_headroom") process.exitCode = 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href)
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
