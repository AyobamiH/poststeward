import { pathToFileURL } from "node:url";

export const PUBLIC_LAUNCH_RESPONSIBILITIES = Object.freeze([
  "support_intake",
  "abuse_escalation",
  "privacy_data_request",
  "incident_command",
  "provider_outage",
  "emergency_publishing_pause",
]);

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

async function github(url, token, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
    redirect: "error",
    signal: AbortSignal.timeout(20_000),
  });
  const body = await response.json().catch(() => ({}));
  demand(response.ok, `Public-launch operator drill failed with HTTP ${response.status}.`);
  return body;
}

function issueApi(repo, suffix = "") {
  demand(
    /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo),
    "POSTSTEWARD_REPOSITORY is invalid.",
  );
  return `https://api.github.com/repos/${repo}/issues${suffix}`;
}

export async function runPublicLaunchOpsDrill(env = process.env) {
  const repo = env.POSTSTEWARD_REPOSITORY || "AyobamiH/poststeward";
  const token = env.GITHUB_TOKEN || "";
  const origin = env.POSTSTEWARD_ORIGIN || "https://app.poststeward.com";
  const release = env.POSTSTEWARD_EXPECTED_RELEASE || "";
  demand(token.length >= 20, "GitHub issue authority is required.");
  demand(/^https:\/\/app\.poststeward\.com$/.test(origin), "Public-launch drill requires the production origin.");
  demand(/^[a-f0-9]{40}$/.test(release), "Public-launch drill requires the exact production release.");

  const readiness = await fetch(origin + "/readiness.json", {
    redirect: "error",
    cache: "no-store",
    headers: { "Cache-Control": "no-cache" },
    signal: AbortSignal.timeout(15_000),
  });
  demand(readiness.ok, "Production readiness could not be observed.");
  const runtime = await readiness.json();
  demand(
    runtime.environment === "production" &&
      runtime.release === release &&
      runtime.policy?.healthy === true &&
      runtime.access?.signupMode === "restricted",
    "Public-launch drill must run against the exact healthy restricted production release.",
  );

  const marker = `<!-- poststeward-public-launch-ops:${release}:${Date.now()} -->`;
  const testedAt = new Date().toISOString();
  const issue = await github(issueApi(repo), token, {
    method: "POST",
    body: JSON.stringify({
      title: "[PostSteward launch drill] Public onboarding operator readiness",
      body:
        marker +
        "\nSynthetic launch-operations exercise only. No customer incident, provider effect or public admission is created.\n\n" +
        PUBLIC_LAUNCH_RESPONSIBILITIES.map((name) => `- ${name}`).join("\n") +
        "\n\nOperator: @AyobamiH",
      assignees: ["AyobamiH"],
    }),
  });
  const readback = await github(issueApi(repo, `/${issue.number}`), token);
  demand(
    typeof readback.body === "string" &&
      readback.body.includes(marker) &&
      readback.assignee?.login === "AyobamiH",
    "Public-launch operator issue was not independently read back with the expected owner.",
  );
  const acknowledgedAt = new Date().toISOString();
  await github(issueApi(repo, `/${issue.number}`), token, {
    method: "PATCH",
    body: JSON.stringify({ state: "closed", state_reason: "completed" }),
  });

  return {
    schemaVersion: 1,
    evidenceClass: "hosted_observation",
    environment: "production",
    release,
    observedAt: acknowledgedAt,
    operator: "AyobamiH",
    responsibilities: PUBLIC_LAUNCH_RESPONSIBILITIES,
    exercise: {
      path: "github_issue",
      issueNumber: issue.number,
      issueUrl: issue.html_url,
      testedAt,
      acknowledgedAt,
      result: "created_readback_assigned_closed",
    },
    publicAdmissionChanged: false,
    providerEffectAttempted: false,
    customerDataUsed: false,
    ready: true,
  };
}

async function main() {
  const report = await runPublicLaunchOpsDrill();
  const serialized = JSON.stringify(report);
  if (process.env.GITHUB_TOKEN)
    demand(
      !serialized.includes(process.env.GITHUB_TOKEN),
      "Public-launch evidence attempted to emit protected material.",
    );
  console.log("POSTSTEWARD_PUBLIC_LAUNCH_OPS " + serialized);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch((error) => {
    console.error(
      "POSTSTEWARD_PUBLIC_LAUNCH_OPS_FAILED " +
        JSON.stringify({
          message: error instanceof Error ? error.message : "Unknown failure.",
        }),
    );
    process.exitCode = 1;
  });
