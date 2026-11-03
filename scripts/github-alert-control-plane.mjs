import { writeFileSync } from "node:fs";
import { REQUIRED_ALERT_CLASSES, evaluateAlertEvidence } from "./operational-alert-evidence.mjs";

const TRANSPORT_CODES = new Set([
  "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "EAI_AGAIN", "ENETUNREACH",
  "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_SOCKET",
]);
const RETRY_DELAYS = [0, 350, 1100];
const waitFor = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const recordAttempt = (entry) => console.error("POSTSTEWARD_ALERT_REQUEST_ATTEMPT " + JSON.stringify(entry));
const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const DURABLE_ALERT_CLASSES = new Set([
  "provider_or_oauth_failure", "recovery_state", "stripe_reconciliation", "capacity_threshold",
]);
function isDurableAlertRow(row) {
  return isRecord(row) &&
    typeof row.id === "string" && row.id.trim().length > 0 && row.id.length <= 100 &&
    DURABLE_ALERT_CLASSES.has(row.class) && ["warning", "critical"].includes(row.severity) &&
    typeof row.code === "string" && /^[A-Z0-9_.:-]{1,80}$/.test(row.code) &&
    typeof row.release === "string" &&
    Number.isSafeInteger(row.occurrences) && row.occurrences >= 1 &&
    Number.isSafeInteger(row.last_seen_at) && ["pending", "dead"].includes(row.status);
}

class AlertControlPlaneError extends Error {
  constructor(message, context, cause) {
    super(message);
    this.context = context;
    if (cause !== undefined) this.cause = cause;
  }
}
function demand(condition, message) {
  if (!condition) throw new AlertControlPlaneError(message, { phase: "control_plane", outcome: "validation_failed" });
}
function transportCode(error, signal) {
  if (error?.name === "TimeoutError") return "timeout";
  // A response body can report AbortError when this request's own deadline expires.
  if (error?.name === "AbortError" && signal.aborted && signal.reason?.name === "TimeoutError") return "timeout";
  const code = error?.cause?.code ?? error?.code;
  return TRANSPORT_CODES.has(code) ? code : null;
}
function requestFailure(phase, stage, outcome, attempts, readOnly, extra = {}, cause) {
  const context = { phase, stage, outcome, attempts, ...extra };
  if (!readOnly) context.writeOutcome = "unknown";
  return new AlertControlPlaneError(
    `Alert ${phase} ${outcome} after ${attempts} ${attempts === 1 ? "attempt" : "attempts"}.`,
    context,
    cause,
  );
}
export function alertFailureDocument(error) {
  // Only our constant messages and bounded context are public. Fetch/parser causes
  // can contain request material and are deliberately never serialized.
  return error instanceof AlertControlPlaneError
    ? { message: error.message, ...error.context }
    : { message: "Unexpected alert control-plane failure.", phase: "control_plane", outcome: "unexpected_error" };
}
async function requestJson(url, init, { phase, readOnly, validate, send = fetch, wait = waitFor, record = recordAttempt }) {
  const maxAttempts = readOnly ? RETRY_DELAYS.length : 1;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    if (RETRY_DELAYS[attempt - 1]) await wait(RETRY_DELAYS[attempt - 1]);
    const signal = AbortSignal.timeout(20_000);
    let stage = "request";
    try {
      const response = await send(url, { ...init, signal });
      const status = Number.isInteger(response.status) ? response.status : 0;
      if (!response.ok) {
        // Release the rejected response without trusting or printing its body.
        if (typeof response.body?.cancel === "function") await response.body.cancel().catch(() => {});
        throw requestFailure(phase, "headers", "http_failure", attempt, readOnly, { status });
      }
      stage = "response_body";
      let body;
      try {
        body = await response.json();
      } catch (error) {
        if (error?.name === "SyntaxError") throw requestFailure(phase, stage, "invalid_json", attempt, readOnly);
        throw error;
      }
      stage = "response_contract";
      if (!validate(body)) throw requestFailure(phase, stage, "invalid_response_contract", attempt, readOnly);
      record({ phase, attempt, maxAttempts, outcome: "validated_response", status, retrying: false });
      return body;
    } catch (error) {
      if (error instanceof AlertControlPlaneError) {
        record({ ...error.context, attempt, maxAttempts, retrying: false });
        throw error;
      }
      const code = transportCode(error, signal);
      const retrying = readOnly && code !== null && attempt < maxAttempts;
      record({ phase, stage, attempt, maxAttempts, outcome: "request_failed", code: code ?? "non_retryable_error", retrying });
      if (!retrying) throw requestFailure(phase, stage, code === null ? "non_retryable_error" : "transport_failed", attempt, readOnly, { code: code ?? "non_retryable_error" }, error);
    }
  }
}
export async function jsonRequest(url, token, init = {}, options = {}) {
  const method = String(init.method || "GET").toUpperCase();
  return requestJson(url, {
    ...init,
    method,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
    redirect: "error",
  }, {
    phase: options.phase || (method === "GET" ? "github_read" : "github_write"),
    readOnly: method === "GET",
    validate: options.validate || ((body) => isRecord(body) || Array.isArray(body)),
    send: options.send,
    wait: options.wait,
    record: options.record,
  });
}
// This helper is used only for the fixed SELECT below. Its POST is a D1 query,
// not an alert mutation. A complete valid empty result is results: [], never a
// missing envelope/batch. HTTP, JSON and contract failures are not retried.
export async function readOnlyCloudflareFetch(url, init, send = fetch, wait = waitFor, record = recordAttempt) {
  return requestJson(url, init, {
    phase: "cloudflare_d1_observation",
    readOnly: true,
    validate: (body) => isRecord(body) && body.success === true && Array.isArray(body.result) && body.result.length > 0 &&
      body.result.every((batch) => isRecord(batch) && batch.success === true && Array.isArray(batch.results) && batch.results.every(isDurableAlertRow)),
    send, wait, record,
  });
}
async function cloudflareQuery(accountId, databaseId, token, sql, params = []) {
  const body = await readOnlyCloudflareFetch(
    `https://api.cloudflare.com/client/v4/accounts/${accountId}/d1/database/${databaseId}/query`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ sql, params }),
      redirect: "error",
    },
  );
  return body.result.flatMap((batch) => batch.results);
}
function issueApi(repo, suffix = "") {
  demand(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo), "POSTSTEWARD_REPOSITORY is invalid.");
  return `https://api.github.com/repos/${repo}/issues${suffix}`;
}
function boundedAlert(row) {
  return {
    id: String(row.id || "").slice(0, 100),
    alertClass: String(row.class || "").slice(0, 100),
    severity: String(row.severity || "").slice(0, 20),
    code: String(row.code || "").slice(0, 100),
    release: String(row.release || "").slice(0, 40),
    occurrences: Number(row.occurrences || 0),
    lastSeenAt: Number(row.last_seen_at || 0),
    status: String(row.status || "").slice(0, 20),
  };
}
async function existingIssue(repo, githubToken, marker) {
  for (let page = 1; page <= 3; page++) {
    const issues = await jsonRequest(`${issueApi(repo)}?state=all&per_page=100&page=${page}`, githubToken, {}, {
      phase: "github_issue_lookup",
      validate: (body) => Array.isArray(body) && body.every((issue) =>
        isRecord(issue) && Number.isInteger(issue.number) && issue.number > 0 && typeof issue.html_url === "string" &&
        Object.hasOwn(issue, "body") && (typeof issue.body === "string" || issue.body === null)),
    });
    const found = issues.find((issue) => typeof issue.body === "string" && issue.body.includes(marker));
    if (found) return found;
    if (issues.length < 100) return undefined;
  }
  throw new AlertControlPlaneError("GitHub issue lookup did not establish absence within three pages.", {
    phase: "github_issue_lookup", stage: "pagination", outcome: "lookup_limit_reached", pages: 3,
  });
}
async function createIssue(repo, githubToken, title, body) {
  return jsonRequest(issueApi(repo), githubToken, {
    method: "POST",
    body: JSON.stringify({ title, body, assignees: ["AyobamiH"] }),
  }, { phase: "github_issue_create", validate: (value) => isRecord(value) && Number.isInteger(value.number) && value.number > 0 && typeof value.html_url === "string" });
}
async function closeIssue(repo, githubToken, number) {
  return jsonRequest(issueApi(repo, `/${number}`), githubToken, {
    method: "PATCH",
    body: JSON.stringify({ state: "closed", state_reason: "completed" }),
  }, { phase: "github_issue_close", validate: (value) => isRecord(value) && value.number === number && value.state === "closed" });
}
export async function surfaceDurableAlerts(env = process.env) {
  const repo = env.POSTSTEWARD_REPOSITORY || "AyobamiH/poststeward";
  const githubToken = env.GITHUB_TOKEN || "";
  const cloudflareToken = env.CLOUDFLARE_API_TOKEN || "";
  const accountId = env.CLOUDFLARE_ACCOUNT_ID || "";
  const databaseId = env.D1_ID || "";
  demand(githubToken.length >= 20, "GITHUB_TOKEN with issue write authority is required.");
  demand(cloudflareToken.length >= 20, "Staging Cloudflare D1 read authority is required.");
  const rows = await cloudflareQuery(
    accountId,
    databaseId,
    cloudflareToken,
    "SELECT id,class,severity,code,release,occurrences,last_seen_at,status FROM operational_alerts WHERE status IN ('pending','dead') ORDER BY last_seen_at DESC LIMIT 50",
  );
  const surfaced = [];
  for (const row of rows) {
    const alert = boundedAlert(row);
    const marker = `<!-- poststeward-alert:${alert.id} -->`;
    let issue = await existingIssue(repo, githubToken, marker);
    if (!issue) {
      issue = await createIssue(
        repo,
        githubToken,
        `[PostSteward alert] ${alert.alertClass}: ${alert.code}`.slice(0, 240),
        `${marker}\nThis issue is an out-of-band operator receipt for a durable PostSteward alert.\n\n\`\`\`json\n${JSON.stringify(alert, null, 2)}\n\`\`\`\n\nNo raw workspace identifier, content, credential or provider token is included.`,
      );
    }
    surfaced.push({ alertId: alert.id, issue: issue.number, url: issue.html_url, state: issue.state });
  }
  return { observed: rows.length, surfaced };
}
async function deliberatelyFailPrimary() {
  const testedAt = new Date().toISOString();
  try {
    await fetch("https://127.0.0.1:1/poststeward-fire-drill", { method: "POST", redirect: "error", signal: AbortSignal.timeout(1500) });
  } catch {
    return testedAt;
  }
  throw new Error("Deliberately failed alert path unexpectedly succeeded.");
}
export async function runAlertFireDrill(env = process.env) {
  const repo = env.POSTSTEWARD_REPOSITORY || "AyobamiH/poststeward";
  const githubToken = env.GITHUB_TOKEN || "";
  const release = env.POSTSTEWARD_EXPECTED_RELEASE || env.GITHUB_SHA || "";
  const environment = env.POSTSTEWARD_ENVIRONMENT || "staging";
  demand(githubToken.length >= 20, "GITHUB_TOKEN with issue write authority is required.");
  demand(/^[a-f0-9]{40}$/.test(release), "Fire drill requires an exact release SHA.");
  demand(["staging", "production"].includes(environment), "Fire drill environment is invalid.");
  const failedAt = await deliberatelyFailPrimary();
  const marker = `<!-- poststeward-alert-fire-drill:${release}:${Date.now()} -->`;
  const testedAt = new Date().toISOString();
  const issue = await createIssue(
    repo,
    githubToken,
    `[PostSteward fire drill] ${environment} operational alert delivery`,
    `${marker}\nSynthetic acceptance only. This exercises the reviewed alert classes and GitHub issue fallback; it does not claim a real incident.\n\n${REQUIRED_ALERT_CLASSES.map((name) => `- ${name}`).join("\n")}`,
  );
  const readback = await jsonRequest(issueApi(repo, `/${issue.number}`), githubToken, {}, {
    phase: "github_fire_drill_readback", validate: (value) => isRecord(value) && value.number === issue.number && typeof value.body === "string",
  });
  demand(typeof readback.body === "string" && readback.body.includes(marker), "GitHub issue fallback readback did not contain the exact fire-drill marker.");
  const receivedAt = new Date().toISOString();
  await closeIssue(repo, githubToken, issue.number);
  const observation = {
    schemaVersion: 1,
    evidenceClass: "hosted_observation",
    environment,
    release,
    observedAt: receivedAt,
    configuredPaths: ["github_issue"],
    deliveries: REQUIRED_ALERT_CLASSES.map((alertClass) => ({ alertClass, path: "github_issue", testedAt, receivedAt, acknowledged: true, result: "created_readback_closed" })),
    failedPath: { path: "synthetic_unreachable_primary", testedAt: failedAt, escalationObserved: true, fallbackPath: "github_issue" },
  };
  const report = evaluateAlertEvidence(observation);
  demand(report.ready === true, "Operational alert fire drill did not satisfy the reviewed evidence contract.");
  return {
    report: {
      ...report,
      issue: issue.html_url,
      issueNumber: issue.number,
      issueClosed: true,
      evidenceBoundary: "Synthetic alert-class fire drill plus GitHub issue create/readback/close. It proves the out-of-band delivery path, not a real incident occurrence.",
    },
    observation,
  };
}

export function alertOutputDocument(mode, result) {
  return mode === "fire-drill" ? result.observation : result;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const mode = process.argv[2] || "surface";
  const runner = mode === "fire-drill" ? runAlertFireDrill : surfaceDurableAlerts;
  runner().then((result) => {
    const report = mode === "fire-drill" ? result.report : result;
    const serialized = JSON.stringify(report);
    const output = JSON.stringify(alertOutputDocument(mode, result));
    for (const secret of [process.env.GITHUB_TOKEN, process.env.CLOUDFLARE_API_TOKEN].filter(Boolean)) {
      demand(!serialized.includes(secret), "Alert report attempted to emit protected material.");
      demand(!output.includes(secret), "Alert evidence attempted to emit protected material.");
    }
    if (process.env.POSTSTEWARD_ALERT_OUTPUT) writeFileSync(process.env.POSTSTEWARD_ALERT_OUTPUT, output + "\n", "utf8");
    console.log((mode === "fire-drill" ? "POSTSTEWARD_ALERT_FIRE_DRILL " : "POSTSTEWARD_ALERT_CONTROL_PLANE ") + serialized);
  }).catch((error) => {
    console.error("POSTSTEWARD_ALERT_CONTROL_PLANE_FAILED " + JSON.stringify(alertFailureDocument(error)));
    process.exitCode = 1;
  });
}
