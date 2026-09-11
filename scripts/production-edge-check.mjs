import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

function exactHttpsOrigin(value) {
  const parsed = new URL(value);
  if (
    parsed.protocol !== "https:" ||
    parsed.origin !== value ||
    parsed.username ||
    parsed.password ||
    parsed.hostname.endsWith(".workers.dev")
  )
    throw new Error("Production origin must be an exact custom HTTPS origin, not workers.dev.");
  return parsed;
}

async function cf(path, token) {
  const response = await fetch(`https://api.cloudflare.com/client/v4${path}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
    redirect: "error",
    signal: AbortSignal.timeout(15_000),
  });
  if (response.status === 404) return undefined;
  if (!response.ok)
    throw new Error(`Cloudflare readiness query failed with HTTP ${response.status}.`);
  const body = await response.json();
  if (!body.success) throw new Error("Cloudflare readiness query returned success=false.");
  return body.result;
}

async function phase(zoneId, name, token) {
  const result = await cf(
    `/zones/${encodeURIComponent(zoneId)}/rulesets/phases/${name}/entrypoint`,
    token,
  );
  return Array.isArray(result?.rules) ? result.rules.filter((rule) => rule.enabled !== false) : [];
}

export function evaluateEdgeEvidence({
  readiness,
  headers,
  dnsRecords,
  customFirewallRules,
  managedFirewallRules,
  rateLimitRules,
}) {
  const hsts = headers.get("strict-transport-security") || "";
  const csp = headers.get("content-security-policy") || "";
  const dnsProxied = dnsRecords.some((record) => record.proxied === true);
  const firewallRules = customFirewallRules.length + managedFirewallRules.length;
  const result = {
    releasePinned: /^[a-f0-9]{40}$/.test(String(readiness?.release || "")),
    restrictedSignup: readiness?.access?.signupMode === "restricted",
    advancedDisabled: readiness?.payments?.advancedEnabled === false,
    mppDisabled: readiness?.payments?.mppEnabled === false,
    hsts: /max-age=\d+/.test(hsts),
    csp: csp.length > 0,
    dnsProxied,
    firewallRules,
    rateLimitRules: rateLimitRules.length,
  };
  return {
    ...result,
    ready:
      result.releasePinned &&
      result.restrictedSignup &&
      result.advancedDisabled &&
      result.mppDisabled &&
      result.hsts &&
      result.csp &&
      result.dnsProxied &&
      result.firewallRules > 0 &&
      result.rateLimitRules > 0,
  };
}

export async function inspectProductionEdge({ origin, zoneName, cloudflareToken }) {
  const parsed = exactHttpsOrigin(origin);
  if (!/^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(zoneName || ""))
    throw new Error("CLOUDFLARE_ZONE_NAME must be the exact active zone name.");
  if (!cloudflareToken || cloudflareToken.length < 20)
    throw new Error("CLOUDFLARE_API_TOKEN with Zone/DNS/Rulesets read access is required.");

  const home = await fetch(parsed.origin + "/", {
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(15_000),
  });
  if (home.status < 200 || home.status >= 400)
    throw new Error(`Production origin returned HTTP ${home.status}.`);
  const readinessResponse = await fetch(parsed.origin + "/readiness.json", {
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(15_000),
  });
  if (!readinessResponse.ok)
    throw new Error(`Production readiness returned HTTP ${readinessResponse.status}.`);
  const readiness = await readinessResponse.json();

  const zones = await cf(`/zones?name=${encodeURIComponent(zoneName)}&status=active`, cloudflareToken);
  if (!Array.isArray(zones) || zones.length !== 1)
    throw new Error("Expected exactly one active Cloudflare zone for production.");
  const zoneId = zones[0].id;
  const dnsRecords = await cf(
    `/zones/${encodeURIComponent(zoneId)}/dns_records?name=${encodeURIComponent(parsed.hostname)}`,
    cloudflareToken,
  );
  const [customFirewallRules, managedFirewallRules, rateLimitRules] = await Promise.all([
    phase(zoneId, "http_request_firewall_custom", cloudflareToken),
    phase(zoneId, "http_request_firewall_managed", cloudflareToken),
    phase(zoneId, "http_ratelimit", cloudflareToken),
  ]);

  return evaluateEdgeEvidence({
    readiness,
    headers: home.headers,
    dnsRecords: Array.isArray(dnsRecords) ? dnsRecords : [],
    customFirewallRules,
    managedFirewallRules,
    rateLimitRules,
  });
}

async function main() {
  const result = await inspectProductionEdge({
    origin: process.env.POSTSTEWARD_PRODUCTION_ORIGIN || "",
    zoneName: process.env.CLOUDFLARE_ZONE_NAME || "",
    cloudflareToken: process.env.CLOUDFLARE_API_TOKEN || "",
  });
  console.log(JSON.stringify(result, null, 2));
  if (!result.ready) process.exitCode = 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href)
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
