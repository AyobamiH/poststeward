import assert from "node:assert/strict";
import test from "node:test";
import { preflightProductionEdgeAuthority } from "../scripts/production-edge-authority-preflight.mjs";

const token = "cloudflare-token-" + "x".repeat(32);
const zoneId = "a".repeat(32);

function response(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fake(routes) {
  return async (url) => {
    const path = new URL(url).pathname + new URL(url).search;
    const found = routes.find(([pattern]) =>
      typeof pattern === "string" ? path === pattern : pattern.test(path),
    );
    if (!found) throw new Error(`unexpected Cloudflare request ${path}`);
    return found[1];
  };
}

test("active zone token with WAF read authority passes preflight", async () => {
  const send = fake([
    ["/client/v4/user/tokens/verify", response(200, { success: true, result: { status: "active" } })],
    [/\/client\/v4\/zones\?name=example.com/, response(200, { success: true, result: [{ id: zoneId, name: "example.com" }] })],
    [`/client/v4/zones/${zoneId}/rulesets/phases/http_request_firewall_custom/entrypoint`, response(404, { success: false, errors: [{ code: 1000, message: "not found" }] })],
  ]);
  const result = await preflightProductionEdgeAuthority({
    origin: "https://service.example.com",
    zoneName: "example.com",
    token,
    send,
  });
  assert.equal(result.ready, true);
  assert.equal(result.state, "ready");
  assert.equal(result.tokenActive, true);
  assert.equal(result.zoneVisible, true);
  assert.equal(result.wafReadable, true);
});

test("inactive token is classified before zone access", async () => {
  const send = fake([
    ["/client/v4/user/tokens/verify", response(200, { success: true, result: { status: "expired" } })],
  ]);
  const result = await preflightProductionEdgeAuthority({
    origin: "https://service.example.com",
    zoneName: "example.com",
    token,
    send,
  });
  assert.equal(result.ready, false);
  assert.equal(result.state, "token_inactive_or_invalid");
  assert.equal(result.tokenActive, false);
});

test("active token that can see zone but receives WAF 403 is classified without mutation", async () => {
  const send = fake([
    ["/client/v4/user/tokens/verify", response(200, { success: true, result: { status: "active" } })],
    [/\/client\/v4\/zones\?name=example.com/, response(200, { success: true, result: [{ id: zoneId, name: "example.com" }] })],
    [`/client/v4/zones/${zoneId}/rulesets/phases/http_request_firewall_custom/entrypoint`, response(403, { success: false, errors: [{ code: 9109, message: "Request is not authorized to access this resource" }] })],
  ]);
  const result = await preflightProductionEdgeAuthority({
    origin: "https://service.example.com",
    zoneName: "example.com",
    token,
    send,
  });
  assert.equal(result.ready, false);
  assert.equal(result.state, "zone_waf_authority_denied");
  assert.equal(result.tokenActive, true);
  assert.equal(result.zoneVisible, true);
  assert.equal(result.wafReadable, false);
  assert.deepEqual(result.errors, [
    { code: 9109, message: "Request is not authorized to access this resource" },
  ]);
});

test("zone visibility failure is distinguished from WAF permission failure", async () => {
  const send = fake([
    ["/client/v4/user/tokens/verify", response(200, { success: true, result: { status: "active" } })],
    [/\/client\/v4\/zones\?name=example.com/, response(403, { success: false, errors: [{ code: 9109, message: "forbidden" }] })],
  ]);
  const result = await preflightProductionEdgeAuthority({
    origin: "https://service.example.com",
    zoneName: "example.com",
    token,
    send,
  });
  assert.equal(result.state, "zone_resource_denied");
  assert.equal(result.zoneVisible, false);
});
