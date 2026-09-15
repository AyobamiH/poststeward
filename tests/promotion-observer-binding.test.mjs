import assert from "node:assert/strict";
import test from "node:test";
import { checkCrossTenantIsolation } from "../scripts/hosted-acceptance.mjs";
import { bindCapacityObservation, readCapacityRuntime } from "../scripts/capacity-observe.mjs";
import { assessObservation } from "../scripts/promotion-evidence.mjs";
import { collectObservations } from "../scripts/release-promotion-controller.mjs";

const release = "a".repeat(40);
const base = "https://staging.example.com";
const tokenA = "fixture-private-token-a";
const tokenB = "fixture-private-token-b";
function runtime(overrides = {}) {
  return { schemaVersion: 2, release, environment: "staging", policy: { healthy: true, violations: [] },
    gates: {}, runtimeCapabilities: { policies: { advancedEnabled: false } }, ...overrides };
}
function isolationTransport({ initial = runtime(), final = initial, accountRelease = release } = {}) {
  let reads = 0;
  const calls = [];
  const send = async (url, init = {}) => {
    const parsed = new URL(url);
    assert.equal(parsed.origin, base);
    assert.equal(init.redirect, "manual");
    calls.push({ path: parsed.pathname, input: init.body ? JSON.parse(init.body) : null });
    if (parsed.pathname === "/readiness.json") return Response.json(++reads === 1 ? initial : final);
    const bearer = new Headers(init.headers).get("authorization");
    if (new Headers(init.headers).has("origin")) return Response.json({}, { status: 403 });
    if (parsed.pathname === "/api/operations/workspace_status")
      return Response.json({ workspace: bearer === `Bearer ${tokenA}` ? "workspace-private-a" : "workspace-private-b", release: accountRelease });
    assert.equal(parsed.pathname, "/api/operations/receipt_get");
    return Response.json({}, { status: bearer === `Bearer ${tokenA}` ? 200 : 404 });
  };
  return { send, calls };
}
test("bound read-only isolation preserves independently observed context and emits no raw identity", async () => {
  const { send, calls } = isolationTransport();
  const result = await checkCrossTenantIsolation(base, tokenA, tokenB, "existing-receipt", send, release);
  assert.equal(result.evidenceClass, "hosted_observation");
  assert.equal(result.release, release);
  assert.equal(result.environment, "staging");
  assert.equal(result.origin, base);
  assert.equal(result.objectIdSwap, "denied");
  assert.equal(result.hostileOriginReplay, "denied");
  assert.equal(calls.filter((call) => call.path === "/readiness.json").length, 2);
  assert.ok(calls.every((call) => ["/readiness.json", "/api/operations/workspace_status", "/api/operations/receipt_get"].includes(call.path)));
  for (const value of [tokenA, tokenB, "workspace-private-a", "workspace-private-b", "existing-receipt"])
    assert.ok(!JSON.stringify(result).includes(value));
  assert.equal(assessObservation({ ...result, ready: true }, { release, environment: "staging", origin: base }).ready, true);
});
test("unbound legacy isolation results are not silently stamped as promotion evidence", async () => {
  const { send } = isolationTransport();
  const result = await checkCrossTenantIsolation(base, tokenA, tokenB, "existing-receipt", send);
  assert.equal(result.evidenceClass, undefined);
  assert.equal(assessObservation({ ...result, ready: true }, { release, environment: "staging", origin: base }).ready, false);
});
test("bound isolation rejects release changes before and after probes", async () => {
  for (const options of [
    { initial: runtime({ release: "b".repeat(40) }) },
    { final: runtime({ release: "b".repeat(40) }) },
    { accountRelease: "b".repeat(40) },
    { final: runtime({ runtimeCapabilities: { policies: { advancedEnabled: true } } }) },
    { final: runtime({ environment: "production" }) },
  ]) {
    const { send } = isolationTransport(options);
    await assert.rejects(() => checkCrossTenantIsolation(base, tokenA, tokenB, "existing-receipt", send, release));
  }
});
test("empty expected SHA or a path-bearing origin never sends isolation credentials", async () => {
  const { send, calls } = isolationTransport();
  await assert.rejects(() => checkCrossTenantIsolation(base, tokenA, tokenB, "existing-receipt", send, ""));
  await assert.rejects(() => checkCrossTenantIsolation(base + "/app", tokenA, tokenB, "existing-receipt", send, release));
  assert.equal(calls.length, 0);
});
test("capacity context is observed from an exact healthy runtime", async () => {
  const result = await readCapacityRuntime(base, release, async () => Response.json(runtime()));
  assert.equal(result.origin, base);
  assert.equal(result.release, release);
  assert.equal(result.environment, "staging");
  for (const overrides of [
    { release: "b".repeat(40) }, { environment: "unknown" }, { schemaVersion: undefined },
    { policy: { healthy: false, violations: [] } }, { policy: { healthy: true, violations: ["drift"] } },
  ]) await assert.rejects(() => readCapacityRuntime(base, release, async () => Response.json(runtime(overrides))));
});
test("capacity freshness follows the durable sample time, not collection time", () => {
  const collected = Date.parse("2026-09-15T18:00:00Z");
  const sample = collected - 2 * 86400000;
  const context = { release, environment: "staging", origin: base };
  const report = bindCapacityObservation({ schemaVersion: 1, evidenceClass: "hosted_observation", ready: true },
    context, { firstObservedAt: sample - 1000, lastObservedAt: sample }, collected);
  assert.equal(report.observedAt, sample);
  assert.equal(report.collectedAt, collected);
  assert.ok(assessObservation(report, context, collected).blockers.includes("evidence_stale"));
  assert.throws(() => bindCapacityObservation({}, context, { firstObservedAt: null, lastObservedAt: sample }, collected));
  assert.throws(() => bindCapacityObservation({}, context, { firstObservedAt: sample, lastObservedAt: collected + 1 }, collected));
});
test("partially supplied collector authority is rejected rather than silently ignored", async () => {
  for (const env of [
    { POSTSTEWARD_PRODUCTION_ORIGIN: base },
    { CLOUDFLARE_ZONE_NAME: "example.com" },
    { POSTSTEWARD_AGENT_TOKEN_A: tokenA },
    { POSTSTEWARD_FOREIGN_DELIVERY_ID: "existing-receipt" },
  ]) await assert.rejects(() => collectObservations(env), /incomplete/);
});
