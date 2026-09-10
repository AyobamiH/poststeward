import { appendFileSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { demand, validateConfiguration } from "./deployment-config.mjs";

export async function inspectRecoveryBoundary(origin, expectedRelease, send = fetch) {
  const checks = [];
  async function check(name, path, expected, init = {}, inspect = async () => true) {
    let status = 0;
    let passed = false;
    try {
      const response = await send(new URL(path, origin), {
        ...init,
        redirect: "manual",
        signal: AbortSignal.timeout(15000),
      });
      status = response.status;
      passed = status === expected &&
        response.headers.get("x-frame-options") === "DENY" &&
        Boolean(response.headers.get("strict-transport-security")) &&
        await inspect(response);
      void response.body?.cancel().catch(() => {});
    } catch {
      /* Never expose upstream bodies, cookies or authorization material. */
    }
    checks.push({ name, status, expected, passed });
  }
  await check("exact runtime still healthy before recovery checks", "/health", 200, {}, async (response) => {
    const data = await response.json();
    return data.release === expectedRelease && data.advancedEnabled === false &&
      data.providerOAuth && ["x", "threads", "linkedin"].every((provider) => typeof data.providerOAuth[provider] === "boolean");
  });
  await check("unauthenticated recovery status rejected", "/api/recovery/status", 401, {}, async (response) => response.headers.get("cache-control") === "no-store");
  await check("cross-origin recovery status rejected", "/api/recovery/status", 403, { headers: { Origin: "https://untrusted.example" } });
  for (const action of ["prepare", "execute", "reconcile", "resume", "undo", "cancel"])
    await check(`unauthenticated recovery ${action} rejected`, `/api/recovery/${action}`, 401, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: origin },
      body: "{}",
    });
  await check("unauthenticated provider OAuth status rejected", "/api/connections/oauth/status", 401);
  for (const provider of ["x", "threads", "linkedin"])
    await check(`unauthenticated ${provider} OAuth start rejected`, `/api/connections/oauth/${provider}/start`, 401, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: origin },
      body: '{"alias":"smoke"}',
    });
  return {
    origin,
    release: expectedRelease,
    observedAt: new Date().toISOString(),
    checks,
    passed: checks.every((item) => item.passed),
    destructiveRecoveryAttempted: false,
    ownerSessionCreated: false,
    providerAuthorizationAttempted: false,
    boundary: "Hosted checks exercise rejection and runtime identity only. They never prepare or execute an authenticated restore, provider consent, publication or payment.",
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const config = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
  validateConfiguration(config);
  const report = await inspectRecoveryBoundary(config.vars.PUBLIC_ORIGIN, config.vars.RELEASE_SHA);
  console.log("POSTSTEWARD_RECOVERY_BOUNDARY_REPORT " + JSON.stringify(report));
  if (process.env.GITHUB_STEP_SUMMARY)
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, "\nRecovery/OAuth hosted boundary:\n\n```json\n" + JSON.stringify(report, null, 2) + "\n```\n");
  demand(report.passed, "Recovery/OAuth hosted boundary checks failed. No destructive restore was attempted.");
}
