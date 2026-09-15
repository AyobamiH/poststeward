import { pathToFileURL } from "node:url";

export const EDGE_CONFIRMATION = "APPLY_POSTSTEWARD_PRODUCTION_EDGE";

const phases = Object.freeze({
  waf: "http_request_firewall_custom",
  rate: "http_ratelimit",
});

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function exactProductionOrigin(value, zoneName) {
  const url = new URL(value);
  demand(
    url.protocol === "https:" &&
      url.origin === value &&
      !url.username &&
      !url.password &&
      !url.hostname.endsWith(".workers.dev"),
    "Production origin must be an exact custom HTTPS origin.",
  );
  const host = url.hostname.toLowerCase();
  const zone = String(zoneName || "").trim().toLowerCase();
  demand(
    /^[a-z0-9.-]+\.[a-z]{2,}$/i.test(zone) &&
      (host === zone || host.endsWith(`.${zone}`)),
    "Production hostname must belong to the confirmed Cloudflare zone.",
  );
  return { origin: url.origin, hostname: host, zoneName: zone };
}

export function canonicalEdgeRules(hostname) {
  return {
    waf: {
      ref: "poststeward_block_unsupported_methods_v1",
      description: "PostSteward block unsupported HTTP methods",
      expression: `(http.host eq "${hostname}" and http.request.method in {"TRACE" "TRACK"})`,
      action: "block",
      enabled: true,
    },
    rate: {
      ref: "poststeward_auth_rate_limit_v1",
      description: "PostSteward owner authentication rate limit",
      expression: `(http.host eq "${hostname}" and starts_with(http.request.uri.path, "/auth/"))`,
      action: "block",
      enabled: true,
      ratelimit: {
        characteristics: ["cf.colo.id", "ip.src"],
        period: 60,
        requests_per_period: 30,
        mitigation_timeout: 60,
      },
    },
  };
}

function comparableRule(rule) {
  return {
    ref: rule?.ref || null,
    description: rule?.description || "",
    expression: rule?.expression || "",
    action: rule?.action || "",
    enabled: rule?.enabled !== false,
    ...(rule?.ratelimit
      ? {
          ratelimit: {
            characteristics: [...(rule.ratelimit.characteristics || [])],
            period: Number(rule.ratelimit.period),
            requests_per_period: Number(rule.ratelimit.requests_per_period),
            mitigation_timeout: Number(rule.ratelimit.mitigation_timeout),
          },
        }
      : {}),
  };
}

function sameRule(actual, expected) {
  return JSON.stringify(comparableRule(actual)) === JSON.stringify(expected);
}

function canonicalRulesetName(kind) {
  return kind === "waf"
    ? "PostSteward custom security"
    : "PostSteward authentication rate limits";
}

async function cloudflare(path, token, init = {}) {
  const response = await fetch(`https://api.cloudflare.com/client/v4${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
    redirect: "error",
    signal: AbortSignal.timeout(20_000),
  });
  if (response.status === 404 && init.allow404) {
    await response.body?.cancel().catch(() => {});
    return null;
  }
  let value = {};
  try {
    value = await response.json();
  } catch {
    value = {};
  }
  demand(
    response.ok && value.success !== false,
    `Cloudflare edge request failed with HTTP ${response.status}.`,
  );
  return value.result ?? value;
}

async function zoneIdentity(zoneName, token, send) {
  const zones = await send(
    `/zones?name=${encodeURIComponent(zoneName)}&status=active&per_page=50`,
    token,
  );
  demand(
    Array.isArray(zones) &&
      zones.length === 1 &&
      zones[0].name === zoneName &&
      /^[a-f0-9]{32}$/.test(zones[0].id || ""),
    "Expected exactly one active confirmed Cloudflare zone.",
  );
  return zones[0];
}

async function entrypoint(zoneId, phase, token, send) {
  return send(
    `/zones/${zoneId}/rulesets/phases/${phase}/entrypoint`,
    token,
    { allow404: true },
  );
}

function inspectCanonical(ruleset, expected) {
  if (!ruleset) return { state: "missing_ruleset", rule: null };
  const rules = Array.isArray(ruleset.rules) ? ruleset.rules : [];
  const matching = rules.filter((rule) => rule.ref === expected.ref);
  const collisions = rules.filter(
    (rule) =>
      rule.ref !== expected.ref &&
      (rule.description === expected.description ||
        rule.expression === expected.expression),
  );
  demand(
    matching.length <= 1 && collisions.length === 0,
    `Cloudflare phase contains ambiguous PostSteward rule candidates for ${expected.ref}.`,
  );
  if (!matching.length) return { state: "missing_rule", rule: null };
  return {
    state: sameRule(matching[0], expected) ? "ready" : "drifted_rule",
    rule: matching[0],
  };
}

async function createRuleset(zoneId, kind, token, send) {
  return send(`/zones/${zoneId}/rulesets`, token, {
    method: "POST",
    body: JSON.stringify({
      name: canonicalRulesetName(kind),
      description: `${canonicalRulesetName(kind)} managed by PostSteward release governance.`,
      kind: "zone",
      phase: phases[kind],
    }),
  });
}

async function addRule(zoneId, rulesetId, expected, token, send) {
  return send(`/zones/${zoneId}/rulesets/${rulesetId}/rules`, token, {
    method: "POST",
    body: JSON.stringify(expected),
  });
}

async function updateRule(zoneId, rulesetId, ruleId, expected, token, send) {
  return send(
    `/zones/${zoneId}/rulesets/${rulesetId}/rules/${ruleId}`,
    token,
    { method: "PATCH", body: JSON.stringify(expected) },
  );
}

export async function reconcileProductionEdge({
  origin,
  zoneName,
  token,
  confirmation,
  send = cloudflare,
}) {
  const target = exactProductionOrigin(origin, zoneName);
  demand(
    typeof token === "string" && token.length >= 20,
    "CLOUDFLARE_API_TOKEN with Zone Rulesets write authority is required.",
  );
  const apply = confirmation === EDGE_CONFIRMATION;
  const zone = await zoneIdentity(target.zoneName, token, send);
  const expected = canonicalEdgeRules(target.hostname);
  const before = {};
  const actions = [];

  for (const kind of ["waf", "rate"]) {
    let ruleset = await entrypoint(zone.id, phases[kind], token, send);
    let inspected = inspectCanonical(ruleset, expected[kind]);
    before[kind] = inspected.state;
    if (inspected.state === "ready") continue;
    actions.push({ kind, action: inspected.state });
    if (!apply) continue;

    if (!ruleset) ruleset = await createRuleset(zone.id, kind, token, send);
    demand(
      /^[a-f0-9]{32}$/.test(ruleset?.id || ""),
      `Cloudflare did not return the ${kind} ruleset identity.`,
    );
    if (inspected.state === "drifted_rule")
      await updateRule(
        zone.id,
        ruleset.id,
        inspected.rule.id,
        expected[kind],
        token,
        send,
      );
    else await addRule(zone.id, ruleset.id, expected[kind], token, send);
  }

  const after = {};
  for (const kind of ["waf", "rate"]) {
    const ruleset = await entrypoint(zone.id, phases[kind], token, send);
    after[kind] = inspectCanonical(ruleset, expected[kind]).state;
  }
  const ready = Object.values(after).every((state) => state === "ready");
  return {
    schemaVersion: 1,
    origin: target.origin,
    hostname: target.hostname,
    zoneName: target.zoneName,
    changed: apply && actions.length > 0,
    applyRequested: apply,
    before,
    after,
    actions,
    ready,
    workerCustomDomain:
      "Managed separately by the reviewed Worker deployment using APP_ORIGIN/custom_domain.",
  };
}

async function main() {
  const result = await reconcileProductionEdge({
    origin: process.env.POSTSTEWARD_PRODUCTION_ORIGIN,
    zoneName: process.env.CLOUDFLARE_ZONE_NAME,
    token: process.env.CLOUDFLARE_API_TOKEN,
    confirmation: process.env.POSTSTEWARD_APPLY_PRODUCTION_EDGE || "",
  });
  console.log("POSTSTEWARD_PRODUCTION_EDGE_RECONCILE " + JSON.stringify(result));
  if (!result.ready) process.exitCode = result.applyRequested ? 2 : 3;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch((error) => {
    console.error(
      "POSTSTEWARD_PRODUCTION_EDGE_RECONCILE_FAILED " +
        JSON.stringify({ message: error instanceof Error ? error.message : "Unknown failure." }),
    );
    process.exitCode = 1;
  });
