import assert from "node:assert/strict";
import test from "node:test";
import {
  EDGE_CONFIRMATION,
  canonicalEdgeRules,
  reconcileProductionEdge,
} from "../scripts/production-edge-apply.mjs";

const zoneId = "a".repeat(32);
const token = "cloudflare-test-token-" + "x".repeat(24);

function fakeCloudflare(initial = {}) {
  const phases = structuredClone(initial);
  const calls = [];
  let next = 1;
  const send = async (path, _token, init = {}) => {
    calls.push({ path, method: init.method || "GET", body: init.body });
    if (path.startsWith("/zones?"))
      return [{ id: zoneId, name: "example.com" }];
    const phaseMatch = path.match(/\/phases\/(.+)\/entrypoint$/);
    if (phaseMatch) return phases[phaseMatch[1]] || null;
    if (path === `/zones/${zoneId}/rulesets` && init.method === "POST") {
      const body = JSON.parse(init.body);
      const id = (next++).toString(16).padStart(32, "0");
      phases[body.phase] = { id, phase: body.phase, rules: [] };
      return phases[body.phase];
    }
    const add = path.match(new RegExp(`^/zones/${zoneId}/rulesets/([a-f0-9]{32})/rules$`));
    if (add && init.method === "POST") {
      const ruleset = Object.values(phases).find((item) => item.id === add[1]);
      const body = JSON.parse(init.body);
      const rule = { id: (next++).toString(16).padStart(32, "0"), ...body };
      ruleset.rules.push(rule);
      return { ...ruleset };
    }
    const patch = path.match(new RegExp(`^/zones/${zoneId}/rulesets/([a-f0-9]{32})/rules/([a-f0-9]{32})$`));
    if (patch && init.method === "PATCH") {
      const ruleset = Object.values(phases).find((item) => item.id === patch[1]);
      const body = JSON.parse(init.body);
      const index = ruleset.rules.findIndex((rule) => rule.id === patch[2]);
      ruleset.rules[index] = { id: patch[2], ...body };
      return { ...ruleset };
    }
    throw new Error(`unexpected fake Cloudflare call ${init.method || "GET"} ${path}`);
  };
  return { phases, calls, send };
}

test("canonical rules are narrowly host-scoped and conservative", () => {
  const value = canonicalEdgeRules("service.example.com");
  assert.match(value.waf.expression, /http\.host eq "service\.example\.com"/);
  assert.match(value.waf.expression, /TRACE/);
  assert.deepEqual(value.rate.ratelimit, {
    characteristics: ["cf.colo.id", "ip.src"],
    period: 60,
    requests_per_period: 30,
    mitigation_timeout: 60,
  });
  assert.match(value.rate.expression, /starts_with\(http\.request\.uri\.path, "\/auth\/"\)/);
});

test("dry run reports missing controls without mutation", async () => {
  const cf = fakeCloudflare();
  const report = await reconcileProductionEdge({
    origin: "https://service.example.com",
    zoneName: "example.com",
    token,
    confirmation: "",
    send: cf.send,
  });
  assert.equal(report.ready, false);
  assert.equal(report.changed, false);
  assert.deepEqual(report.before, {
    waf: "missing_ruleset",
    rate: "missing_ruleset",
  });
  assert.equal(cf.calls.some((call) => call.method !== "GET"), false);
});

test("confirmed apply creates only the two reviewed entrypoint rules and verifies readback", async () => {
  const cf = fakeCloudflare();
  const report = await reconcileProductionEdge({
    origin: "https://service.example.com",
    zoneName: "example.com",
    token,
    confirmation: EDGE_CONFIRMATION,
    send: cf.send,
  });
  assert.equal(report.ready, true);
  assert.equal(report.changed, true);
  assert.deepEqual(report.after, { waf: "ready", rate: "ready" });
  assert.equal(cf.calls.filter((call) => call.method === "POST").length, 4);
  assert.equal(cf.calls.some((call) => call.method === "PATCH"), false);
});

test("drifted canonical rule is patched in place rather than duplicated", async () => {
  const expected = canonicalEdgeRules("service.example.com");
  const wafId = "b".repeat(32);
  const rateId = "c".repeat(32);
  const wafRuleId = "d".repeat(32);
  const rateRuleId = "e".repeat(32);
  const cf = fakeCloudflare({
    http_request_firewall_custom: {
      id: wafId,
      rules: [{ id: wafRuleId, ...expected.waf, enabled: false }],
    },
    http_ratelimit: {
      id: rateId,
      rules: [{ id: rateRuleId, ...expected.rate }],
    },
  });
  const report = await reconcileProductionEdge({
    origin: "https://service.example.com",
    zoneName: "example.com",
    token,
    confirmation: EDGE_CONFIRMATION,
    send: cf.send,
  });
  assert.equal(report.ready, true);
  assert.equal(cf.calls.filter((call) => call.method === "PATCH").length, 1);
  assert.equal(cf.calls.filter((call) => call.method === "POST").length, 0);
});

test("ambiguous same-description rule fails closed", async () => {
  const expected = canonicalEdgeRules("service.example.com");
  const cf = fakeCloudflare({
    http_request_firewall_custom: {
      id: "b".repeat(32),
      rules: [
        {
          id: "c".repeat(32),
          ...expected.waf,
          ref: "some_other_ref",
        },
      ],
    },
  });
  await assert.rejects(
    reconcileProductionEdge({
      origin: "https://service.example.com",
      zoneName: "example.com",
      token,
      confirmation: EDGE_CONFIRMATION,
      send: cf.send,
    }),
    /ambiguous PostSteward rule candidates/,
  );
});

test("origin must be custom HTTPS and inside confirmed zone", async () => {
  const cf = fakeCloudflare();
  for (const [origin, zone] of [
    ["https://poststeward.workers.dev", "example.com"],
    ["http://service.example.com", "example.com"],
    ["https://service.other.com", "example.com"],
  ])
    await assert.rejects(
      reconcileProductionEdge({
        origin,
        zoneName: zone,
        token,
        confirmation: "",
        send: cf.send,
      }),
    );
});
