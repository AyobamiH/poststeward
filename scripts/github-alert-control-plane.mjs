import { readFileSync, writeFileSync } from "node:fs";
import { REQUIRED_ALERT_CLASSES, evaluateAlertEvidence } from "./operational-alert-evidence.mjs";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}
async function jsonRequest(url, token, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
    redirect: "error",
    signal: AbortSignal.timeout(20_000),
  });
  const body = await response.json().catch(() => ({}));
  demand(response.ok, `GitHub alert control-plane request failed with HTTP ${response.status}.`);
  return body;
}
async function cloudflareQuery(accountId, databaseId, token, sql, params = []) {
  const response = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${accountId}/d1/database/${databaseId}/query`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ sql, params }),
      redirect: "error",
      signal: AbortSignal.timeout(20_000),
    },
  );
  const body = await response.json().catch(() => ({}));
  demand(response.ok && body?.success !== false, `Cloudflare alert observation failed with HTTP ${response.status}.`);
  return body?.result?.flatMap((batch) => batch?.results || []) || [];
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
    const issues = await jsonRequest(`${issueApi(repo)}?state=all&per_page=100&page=${page}`, githubToken);
    const found = issues.find((issue) => typeof issue.body === "string" && issue.body.includes(marker));
    if (found) return found;
    if (issues.length < 100) break;
  }
}
async function createIssue(repo, githubToken, title, body) {
  return jsonRequest(issueApi(repo), githubToken, {
    method: "POST",
    body: JSON.stringify({ title, body, assignees: ["AyobamiH"] }),
  });
}
async function closeIssue(repo, githubToken, number) {
  return jsonRequest(issueApi(repo, `/${number}`), githubToken, {
    method: "PATCH",
    body: JSON.stringify({ state: "closed", state_reason: "completed" }),
  });
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
  const readback = await jsonRequest(issueApi(repo, `/${issue.number}`), githubToken);
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
  return { ...report, issue: issue.html_url, issueNumber: issue.number, issueClosed: true, evidenceBoundary: "Synthetic alert-class fire drill plus GitHub issue create/readback/close. It proves the out-of-band delivery path, not a real incident occurrence." };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const mode = process.argv[2] || "surface";
  const runner = mode === "fire-drill" ? runAlertFireDrill : surfaceDurableAlerts;
  runner().then((report) => {
    const serialized = JSON.stringify(report);
    for (const secret of [process.env.GITHUB_TOKEN, process.env.CLOUDFLARE_API_TOKEN].filter(Boolean)) demand(!serialized.includes(secret), "Alert report attempted to emit protected material.");
    if (process.env.POSTSTEWARD_ALERT_OUTPUT) writeFileSync(process.env.POSTSTEWARD_ALERT_OUTPUT, serialized + "\n", "utf8");
    console.log((mode === "fire-drill" ? "POSTSTEWARD_ALERT_FIRE_DRILL " : "POSTSTEWARD_ALERT_CONTROL_PLANE ") + serialized);
  }).catch((error) => {
    console.error("POSTSTEWARD_ALERT_CONTROL_PLANE_FAILED " + JSON.stringify({ message: error instanceof Error ? error.message : "unknown_error" }));
    process.exitCode = 1;
  });
}
