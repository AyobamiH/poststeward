import { appendFileSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { demand, validateConfiguration } from "./deployment-config.mjs";

export async function inspectPilot(origin, send = fetch) {
  const checks = [];
  async function check(name, path, expected, init = {}, inspect = async () => true) {
    let status = 0, passed = false;
    try {
      const response = await send(new URL(path, origin), { ...init, redirect: "manual", signal: AbortSignal.timeout(15000) });
      status = response.status;
      passed = status === expected && response.headers.get("x-frame-options") === "DENY" &&
        !!response.headers.get("strict-transport-security") && await inspect(response);
      void response.body?.cancel().catch(() => {});
    } catch { /* Do not record arbitrary network errors, response bodies or OAuth state. */ }
    checks.push({ name, status, expected, passed });
  }
  await check("owner acceptance HTML and explicit approval form", "/pilot", 200, {}, async (r) => {
    const text = await r.text(); return r.headers.get("content-type")?.includes("text/html") && text.includes('id="approve"') && text.includes('id="confirm"') && text.includes('type="password"');
  });
  for (const path of ["/pilot.js", "/pilot-client.js", "/pilot.css"])
    await check("acceptance asset " + path, path, 200);
  await check("pilot status requires an owner session", "/api/pilot/status", 401, {}, async (r) => r.headers.get("cache-control") === "no-store");
  for (const action of ["prepare", "confirm", "cancel", "recheck"])
    await check("unauthenticated pilot " + action + " rejected", "/api/pilot/" + action, 401, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin }, body: "{}",
    });
  await check("cross-origin owner acceptance rejected", "/api/pilot/status", 403, { headers: { Origin: "https://untrusted.example" } });
  await check("external post-login return rejected", "/auth/login?return=https%3A%2F%2Funtrusted.example", 400);
  await check("fixed pilot login starts Google code flow without following it", "/auth/login?return=%2Fpilot", 302, {}, async (r) => {
    const u = new URL(r.headers.get("location"));
    return u.origin === "https://accounts.google.com" && u.searchParams.get("redirect_uri") === origin + "/auth/callback" &&
      u.searchParams.get("response_type") === "code" && u.searchParams.get("code_challenge_method") === "S256" &&
      !!u.searchParams.get("nonce") && !!u.searchParams.get("state") &&
      /__Host-login=/.test(r.headers.get("set-cookie") || "") && r.headers.get("cache-control") === "no-store";
  });
  return { origin, observedAt: new Date().toISOString(), checks, passed: checks.every((c) => c.passed),
    ownerSignInCompleted: "not tested by deployment", publication: "not attempted by deployment",
    boundary: "The real owner must complete Google consent, choose the destination and approve the exact publication. No session was manufactured." };
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const config = JSON.parse(readFileSync("wrangler.jsonc", "utf8")); validateConfiguration(config);
  const report = { release: config.vars.RELEASE_SHA, ...await inspectPilot(config.vars.PUBLIC_ORIGIN) };
  console.log("POSTSTEWARD_PILOT_HOSTED_REPORT " + JSON.stringify(report));
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, "\nOwner acceptance surface checks:\n\n```json\n" + JSON.stringify(report, null, 2) + "\n```\n");
  demand(report.passed, "Owner acceptance surface checks failed. Inspect check names/statuses; no live owner or publication success is implied.");
}
