import assert from "node:assert/strict";
import test from "node:test";
import { inspectProductionEdge } from "../scripts/production-edge-check.mjs";
const origin = "https://service.example.com";
const release = "a".repeat(40);
const secret = "fixture-cloudflare-read-authority";
function runtime(overrides = {}) {
  return { schemaVersion: 2, release, environment: "production", policy: { healthy: true, violations: [] },
    access: { signupMode: "restricted" }, payments: { advancedEnabled: false, mppEnabled: false }, ...overrides };
}
async function scenario(options = {}) {
  const calls = [];
  const original = globalThis.fetch;
  let runtimeReads = 0;
  globalThis.fetch = async (url, init = {}) => {
    const parsed = new URL(url);
    calls.push(parsed.pathname);
    assert.ok(init.method === undefined || init.method === "GET");
    if (parsed.origin === origin) {
      assert.equal(new Headers(init.headers).has("authorization"), false);
      if (parsed.pathname === "/readiness.json")
        return Response.json(++runtimeReads === 1 ? options.initial || runtime() : options.final || runtime());
      return new Response("ok", { status: options.homeStatus || 200,
        headers: { "strict-transport-security": "max-age=31536000", "content-security-policy": "default-src 'self'" } });
    }
    assert.equal(parsed.origin, "https://api.cloudflare.com");
    assert.equal(new Headers(init.headers).get("authorization"), `Bearer ${secret}`);
    if (parsed.pathname.endsWith("/zones")) return Response.json({ success: true, result: [{ id: "zone-fixture" }] });
    if (parsed.pathname.endsWith("/dns_records")) return Response.json({ success: true, result: [{ proxied: true }] });
    return Response.json({ success: true, result: { rules: [{ enabled: true }] } });
  };
  try {
    const result = await inspectProductionEdge({ origin, zoneName: options.zoneName || "example.com",
      cloudflareToken: secret, expectedRelease: release });
    return { result, calls };
  } finally { globalThis.fetch = original; }
}
test("edge acceptance reports independently read production context without leaking authority", async () => {
  const { result, calls } = await scenario();
  assert.equal(result.ready, true);
  assert.equal(result.release, release);
  assert.equal(result.environment, "production");
  assert.equal(result.origin, origin);
  assert.equal(result.evidenceClass, "hosted_observation");
  assert.ok(Number.isFinite(Date.parse(result.observedAt)));
  assert.equal(calls.filter((path) => path === "/readiness.json").length, 2);
  assert.ok(!JSON.stringify(result).includes(secret));
});
test("edge inspection rejects wrong release, staging, redirected homes and runtime changes", async () => {
  for (const options of [
    { initial: runtime({ release: "b".repeat(40) }) },
    { initial: runtime({ environment: "staging" }) },
    { final: runtime({ release: "b".repeat(40) }) },
    { final: runtime({ policy: { healthy: false, violations: ["drift"] } }) },
    { homeStatus: 302 }, { zoneName: "unrelated.example" },
  ]) await assert.rejects(() => scenario(options));
});
