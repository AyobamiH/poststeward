import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

function exactHttpsOrigin(value) {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.origin !== value || parsed.username ||
      parsed.password || parsed.hostname.endsWith(".workers.dev"))
    throw new Error("Production origin must be an exact custom HTTPS origin, not workers.dev.");
  return parsed;
}
async function cf(path, token) {
  const response = await fetch(`https://api.cloudflare.com/client/v4${path}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
    redirect: "error", signal: AbortSignal.timeout(15_000),
  });
  if (response.status === 404) return undefined;
  if (!response.ok) throw new Error(`Cloudflare readiness query failed with HTTP ${response.status}.`);
  const body = await response.json();
  if (!body.success) throw new Error("Cloudflare readiness query returned success=false.");
  return body.result;
}
async function phase(zoneId, name, token) {
  const result = await cf(`/zones/${encodeURIComponent(zoneId)}/rulesets/phases/${name}/entrypoint`, token);
  return Array.isArray(result?.rules) ? result.rules.filter((rule) => rule.enabled !== false) : [];
}
export function evaluateEdgeEvidence({ readiness, headers, workerDomains, customFirewallRules,
  rateLimitRules, hostname, workerName }) {
  const hsts = headers.get("strict-transport-security") || "";
  const csp = headers.get("content-security-policy") || "";
  const customDomain = workerDomains.some(
    (domain) =>
      domain.hostname === hostname &&
      domain.service === workerName &&
      typeof domain.cert_id === "string" &&
      domain.cert_id.length > 0,
  );
  // Cloudflare Custom Domains create/manage the DNS record and certificate for
  // the Worker origin. This is stronger evidence for this originless Worker
  // than a separate DNS-record read and needs only Workers Scripts read access.
  const dnsProxied = customDomain;
  const firewallRules = customFirewallRules.length;
  const result = {
    releasePinned: /^[a-f0-9]{40}$/.test(String(readiness?.release || "")),
    restrictedSignup: readiness?.access?.signupMode === "restricted",
    advancedDisabled: readiness?.payments?.advancedEnabled === false,
    mppDisabled: readiness?.payments?.mppEnabled === false,
    hsts: /max-age=\d+/.test(hsts), csp: csp.length > 0,
    customDomain, dnsProxied,
    firewallRules, rateLimitRules: rateLimitRules.length,
  };
  return { ...result, ready: result.releasePinned && result.restrictedSignup &&
    result.advancedDisabled && result.mppDisabled && result.hsts && result.csp &&
    result.dnsProxied && result.firewallRules > 0 && result.rateLimitRules > 0 };
}
async function readHostedReadiness(origin) {
  const response = await fetch(origin + "/readiness.json", {
    redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error(`Production readiness returned HTTP ${response.status}.`);
  return response.json();
}
export async function inspectProductionEdge({
  origin,
  zoneName,
  cloudflareToken,
  accountId,
  workerName = "poststeward",
  expectedRelease,
}) {
  const parsed = exactHttpsOrigin(origin);
  if (!/^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(zoneName || ""))
    throw new Error("CLOUDFLARE_ZONE_NAME must be the exact active zone name.");
  if (parsed.hostname !== zoneName && !parsed.hostname.endsWith("." + zoneName))
    throw new Error("Production origin is outside the reviewed zone.");
  if (!cloudflareToken || cloudflareToken.length < 20)
    throw new Error("CLOUDFLARE_API_TOKEN with Workers Scripts and Zone Rulesets read access is required.");
  if (!/^[a-f0-9]{32}$/.test(accountId || ""))
    throw new Error("CLOUDFLARE_ACCOUNT_ID must be the exact Workers account.");
  const home = await fetch(parsed.origin + "/", {
    redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(15_000),
  });
  if (home.status !== 200) throw new Error(`Production origin returned HTTP ${home.status}.`);
  const readiness = await readHostedReadiness(parsed.origin);
  if (readiness?.environment !== "production" || readiness?.policy?.healthy !== true ||
      !/^[a-f0-9]{40}$/.test(readiness?.release || "") ||
      (expectedRelease !== undefined && readiness.release !== expectedRelease))
    throw new Error("Production edge requires the exact healthy production runtime.");
  const zones = await cf(`/zones?name=${encodeURIComponent(zoneName)}&status=active`, cloudflareToken);
  if (!Array.isArray(zones) || zones.length !== 1)
    throw new Error("Expected exactly one active Cloudflare zone for production.");
  const zoneId = zones[0].id;
  const [workerDomains, customFirewallRules, rateLimitRules] = await Promise.all([
    cf(
      `/accounts/${encodeURIComponent(accountId)}/workers/domains?hostname=${encodeURIComponent(parsed.hostname)}`,
      cloudflareToken,
    ),
    phase(zoneId, "http_request_firewall_custom", cloudflareToken),
    phase(zoneId, "http_ratelimit", cloudflareToken),
  ]);
  const after = await readHostedReadiness(parsed.origin);
  if (after.release !== readiness.release || after.environment !== readiness.environment ||
      after.policy?.healthy !== true)
    throw new Error("Production runtime changed during edge observation.");
  return {
    ...evaluateEdgeEvidence({
      readiness,
      headers: home.headers,
      workerDomains: Array.isArray(workerDomains) ? workerDomains : [],
      customFirewallRules,
      rateLimitRules,
      hostname: parsed.hostname,
      workerName,
    }),
    schemaVersion: 1, evidenceClass: "hosted_observation", origin: parsed.origin,
    release: readiness.release, environment: readiness.environment,
    observedAt: new Date().toISOString(),
  };
}
async function main() {
  const result = await inspectProductionEdge({
    origin: process.env.POSTSTEWARD_PRODUCTION_ORIGIN || "",
    zoneName: process.env.CLOUDFLARE_ZONE_NAME || "",
    cloudflareToken: process.env.CLOUDFLARE_API_TOKEN || "",
    accountId: process.env.CLOUDFLARE_ACCOUNT_ID || "",
    workerName: process.env.POSTSTEWARD_WORKER_NAME || "poststeward",
    expectedRelease: process.env.POSTSTEWARD_EXPECTED_RELEASE ?? process.env.GITHUB_SHA,
  });
  console.log(JSON.stringify(result, null, 2));
  if (!result.ready) process.exitCode = 2;
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href)
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
