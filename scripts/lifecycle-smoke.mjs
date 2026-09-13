import { appendFileSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { demand, validateConfiguration } from "./deployment-config.mjs";

export async function inspectLifecycleBoundary(origin, send = fetch) {
  const checks = [];
  async function check(name, path, expected, init = {}, predicate = () => true) {
    let status = 0;
    let passed = false;
    try {
      const response = await send(new URL(path, origin), {
        ...init,
        redirect: "manual",
        signal: AbortSignal.timeout(10000),
      });
      status = response.status;
      passed =
        status === expected &&
        response.headers.get("x-frame-options") === "DENY" &&
        Boolean(response.headers.get("strict-transport-security")) &&
        (await predicate(response));
      if (response.body && !response.bodyUsed)
        await response.body.cancel().catch(() => {});
    } catch {
      // Never surface response bodies, cookies or exception contents.
    }
    checks.push({ name, status, expected, passed });
  }

  await check("account lifecycle page", "/lifecycle", 200, {}, async (response) => {
    const text = await response.text();
    return (
      response.headers.get("content-type")?.includes("text/html") &&
      text.includes("Delete this PostSteward workspace") &&
      text.includes('id="confirmation"')
    );
  });
  await check("account lifecycle client", "/lifecycle.js", 200);
  await check(
    "unauthenticated lifecycle status rejected",
    "/api/lifecycle/status",
    401,
    {},
    (response) => response.headers.get("cache-control")?.includes("no-store"),
  );
  await check(
    "unauthenticated lifecycle deletion rejected",
    "/api/lifecycle/delete",
    401,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: origin },
      body: '{"delete":true,"confirmation":"DELETE smoke"}',
    },
  );
  await check(
    "cross-origin lifecycle deletion rejected before authentication",
    "/api/lifecycle/delete",
    403,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: "https://untrusted.example",
      },
      body: '{"delete":true,"confirmation":"DELETE smoke"}',
    },
  );

  return {
    origin,
    observedAt: new Date().toISOString(),
    checks,
    passed: checks.every((item) => item.passed),
    deletionAttempted: false,
    boundary:
      "Hosted lifecycle smoke checks only static assets and rejected unauthenticated/cross-origin requests. It never creates a deletion tombstone or erases workspace data.",
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const config = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
  validateConfiguration(config);
  const report = await inspectLifecycleBoundary(config.vars.PUBLIC_ORIGIN);
  console.log("POSTSTEWARD_LIFECYCLE_BOUNDARY_REPORT " + JSON.stringify(report));
  if (process.env.GITHUB_STEP_SUMMARY)
    appendFileSync(
      process.env.GITHUB_STEP_SUMMARY,
      "\nAccount lifecycle hosted boundary:\n\n```json\n" +
        JSON.stringify(report, null, 2) +
        "\n```\n",
    );
  demand(
    report.passed,
    "Account lifecycle hosted boundary checks failed. No workspace deletion was attempted.",
  );
}
