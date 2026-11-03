import { pathToFileURL } from "node:url";

const TRANSPORT_CODES = new Set([
  "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "EAI_AGAIN", "ENETUNREACH",
  "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_SOCKET",
]);

function transportCode(error) {
  if (error?.name === "TimeoutError") return "timeout";
  const code = error?.cause?.code ?? error?.code;
  return TRANSPORT_CODES.has(code) ? code : null;
}

// This retry boundary can only GET the public readiness document. HTTP and
// document validation remain with the caller; no response is retried.
export async function fetchMonitorReadiness(origin, {
  send = fetch,
  wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  record = (entry) => console.error("POSTSTEWARD_READINESS_ATTEMPT " + JSON.stringify(entry)),
  now = () => new Date().toISOString(),
} = {}) {
  const url = new URL("/readiness.json", origin);
  const maxAttempts = 3;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const startedAt = now();
    let response;
    try {
      response = await send(url, {
        method: "GET", redirect: "manual", cache: "no-store",
        headers: { "Cache-Control": "no-cache" },
        signal: AbortSignal.timeout(20_000),
      });
    } catch (error) {
      const code = transportCode(error);
      const retrying = code !== null && attempt < maxAttempts;
      record({ attempt, maxAttempts, startedAt, finishedAt: now(), outcome: "request_failed", code: code ?? "non_retryable_error", retrying });
      if (!retrying) throw error;
      await wait(attempt * 1000);
      continue;
    }
    record({ attempt, maxAttempts, startedAt, finishedAt: now(), outcome: "http_response", status: response.status });
    return response;
  }
}

export async function resolveMonitorRelease(origin, environment, options) {
  if (!["staging", "production"].includes(environment))
    throw new Error("Monitor environment must be staging or production.");
  const response = await fetchMonitorReadiness(origin, options);
  if (!response.ok) throw new Error(`Hosted readiness returned HTTP ${response.status}.`);
  const value = await response.json();
  if (value?.environment !== environment || !/^[a-f0-9]{40}$/.test(value?.release || ""))
    throw new Error("Hosted readiness does not identify the exact expected environment and release.");
  return value.release;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  resolveMonitorRelease(process.env.POSTSTEWARD_ORIGIN, process.env.POSTSTEWARD_ENVIRONMENT || process.env.MATRIX_ENVIRONMENT)
    .then((release) => process.stdout.write(release))
    .catch(() => {
      console.error("POSTSTEWARD_READINESS_FAILED: no validated release was resolved.");
      process.exitCode = 1;
    });
}
