import { readFileSync, appendFileSync } from "node:fs";
import { demand, validateConfiguration } from "./deployment-config.mjs";
const c = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
validateConfiguration(c);
const origin = c.vars.PUBLIC_ORIGIN;
for (const path of ["/health", "/help.json"]) {
  const response = await fetch(new URL(path, origin), {
    signal: AbortSignal.timeout(15000),
    redirect: "error",
  });
  demand(response.ok, `${path} returned ${response.status}.`);
  demand(
    response.headers.get("strict-transport-security")?.includes("max-age="),
    "HSTS header missing.",
  );
  const body = await response.json();
  demand(
    body.release === c.vars.RELEASE_SHA,
    `Deployed revision mismatch at ${path}.`,
  );
  if (path === "/help.json")
    demand(
      body.operations?.length === 26 && body.payment?.enabled === false,
      "Capability or billing configuration mismatch.",
    );
}
const unauthenticated = await fetch(new URL("/api/session", origin), {
  signal: AbortSignal.timeout(15000),
  redirect: "error",
});
demand(
  unauthenticated.status === 401 &&
    unauthenticated.headers.get("cache-control")?.includes("no-store"),
  "Unauthenticated workspace access must return uncached 401.",
);
const crossOrigin = await fetch(new URL("/api/session", origin), {
  headers: { Origin: "https://untrusted.example" },
  signal: AbortSignal.timeout(15000),
  redirect: "error",
});
demand(
  crossOrigin.status === 403,
  "Cross-origin workspace access was not rejected.",
);
const evidence = `PostSteward ${c.vars.DEPLOY_ENV}: ${origin}\nRevision: ${c.vars.RELEASE_SHA}\nRevision, security headers, discovery, disabled payments and unauthenticated/cross-origin rejection passed.\nOwner sign-in, provider publishing, restoration and payment settlement require separate acceptance.\n`;
console.log(evidence);
if (process.env.GITHUB_STEP_SUMMARY)
  appendFileSync(process.env.GITHUB_STEP_SUMMARY, evidence);
