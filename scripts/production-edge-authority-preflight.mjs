import { pathToFileURL } from "node:url";

const api = "https://api.cloudflare.com/client/v4";

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function exactTarget(origin, zoneName) {
  const url = new URL(origin);
  const zone = String(zoneName || "").trim().toLowerCase();
  demand(
    url.protocol === "https:" &&
      url.origin === origin &&
      !url.username &&
      !url.password &&
      !url.hostname.endsWith(".workers.dev"),
    "Production origin must be an exact custom HTTPS origin.",
  );
  demand(
    /^[a-z0-9.-]+\.[a-z]{2,}$/i.test(zone) &&
      (url.hostname === zone || url.hostname.endsWith(`.${zone}`)),
    "Production hostname must belong to the confirmed Cloudflare zone.",
  );
  return { origin: url.origin, hostname: url.hostname, zoneName: zone };
}

function boundedErrors(body) {
  return (Array.isArray(body?.errors) ? body.errors : []).slice(0, 3).map((error) => ({
    code: Number.isFinite(Number(error?.code)) ? Number(error.code) : null,
    message:
      typeof error?.message === "string"
        ? error.message.replace(/[\r\n\t]/g, " ").slice(0, 180)
        : "Cloudflare rejected the request.",
  }));
}

async function request(path, token, send = fetch) {
  const response = await send(`${api}${path}`, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
    redirect: "error",
    signal: AbortSignal.timeout(15_000),
  });
  const body = await response.json().catch(() => ({}));
  return { status: response.status, ok: response.ok, body };
}

export async function preflightProductionEdgeAuthority({
  origin,
  zoneName,
  token,
  send = fetch,
}) {
  const target = exactTarget(origin, zoneName);
  demand(typeof token === "string" && token.length >= 20, "Protected Cloudflare API token is required.");

  const verified = await request("/user/tokens/verify", token, send);
  if (!verified.ok || verified.body?.success === false || verified.body?.result?.status !== "active")
    return {
      schemaVersion: 1,
      ready: false,
      state: "token_inactive_or_invalid",
      tokenActive: false,
      zoneVisible: false,
      wafReadable: false,
      errors: boundedErrors(verified.body),
    };

  const zones = await request(
    `/zones?name=${encodeURIComponent(target.zoneName)}&status=active&per_page=50`,
    token,
    send,
  );
  if (!zones.ok || zones.body?.success === false)
    return {
      schemaVersion: 1,
      ready: false,
      state: "zone_resource_denied",
      tokenActive: true,
      zoneVisible: false,
      wafReadable: false,
      errors: boundedErrors(zones.body),
      remediation:
        "Replace the protected production token with one scoped to the PostSteward zone and required Zone read/WAF authority; review token IP restrictions if present.",
    };

  const matches = Array.isArray(zones.body?.result) ? zones.body.result : [];
  if (matches.length !== 1 || !/^[a-f0-9]{32}$/.test(matches[0]?.id || ""))
    return {
      schemaVersion: 1,
      ready: false,
      state: "zone_not_uniquely_visible",
      tokenActive: true,
      zoneVisible: false,
      wafReadable: false,
      errors: [],
    };

  const zoneId = matches[0].id;
  const waf = await request(
    `/zones/${zoneId}/rulesets/phases/http_request_firewall_custom/entrypoint`,
    token,
    send,
  );
  // A missing entrypoint is acceptable: the reconciler may create it. A 403 is
  // not. Cloudflare documents Zone WAF Read or Write as sufficient for this GET.
  if (waf.status !== 404 && (!waf.ok || waf.body?.success === false))
    return {
      schemaVersion: 1,
      ready: false,
      state: waf.status === 403 ? "zone_waf_authority_denied" : "zone_waf_read_failed",
      tokenActive: true,
      zoneVisible: true,
      wafReadable: false,
      errors: boundedErrors(waf.body),
      remediation:
        "The protected production token is active and can see the zone but cannot read the WAF phase. Grant Zone WAF Read and Zone WAF Write for this zone, and remove any token IP condition that excludes GitHub-hosted runners.",
    };

  return {
    schemaVersion: 1,
    ready: true,
    state: "ready",
    tokenActive: true,
    zoneVisible: true,
    wafReadable: true,
    origin: target.origin,
    zoneName: target.zoneName,
  };
}

async function main() {
  const result = await preflightProductionEdgeAuthority({
    origin: process.env.POSTSTEWARD_PRODUCTION_ORIGIN || "",
    zoneName: process.env.CLOUDFLARE_ZONE_NAME || "",
    token: process.env.CLOUDFLARE_API_TOKEN || "",
  });
  console.log("POSTSTEWARD_PRODUCTION_EDGE_AUTHORITY " + JSON.stringify(result));
  if (!result.ready) process.exitCode = 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch((error) => {
    console.error(
      "POSTSTEWARD_PRODUCTION_EDGE_AUTHORITY_FAILED " +
        JSON.stringify({ message: error instanceof Error ? error.message : "Unknown failure." }),
    );
    process.exitCode = 1;
  });
