import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fetchMonitorReadiness, resolveMonitorRelease } from "../scripts/monitor-readiness.mjs";

const origin = "https://app.poststeward.com";
const release = "a".repeat(40);
const reset = () => new TypeError("fetch failed", { cause: Object.assign(new Error("transport"), { code: "ECONNRESET" }) });
const response = () => new Response(JSON.stringify({ environment: "production", release }));

function harness(send) {
  const records = [], waits = [];
  return {
    records, waits,
    options: { send, record: (entry) => records.push(entry), wait: async (ms) => waits.push(ms), now: () => "2026-11-03T13:05:37.000Z" },
  };
}

test("readiness retries a lost GET connection, records attempts and still validates the release", async () => {
  const requests = [];
  const h = harness(async (url, init) => {
    requests.push({ url, init });
    if (requests.length === 1) throw reset();
    return response();
  });
  assert.equal(await resolveMonitorRelease(origin, "production", h.options), release);
  assert.equal(requests.length, 2);
  for (const { url, init } of requests) {
    assert.equal(url.href, origin + "/readiness.json");
    assert.equal(init.method, "GET");
    assert.equal(init.redirect, "manual");
    assert.equal(init.cache, "no-store");
    assert.deepEqual(init.headers, { "Cache-Control": "no-cache" });
    assert.equal(init.body, undefined);
  }
  assert.notEqual(requests[0].init.signal, requests[1].init.signal);
  assert.deepEqual(h.waits, [1000]);
  assert.deepEqual(h.records.map((x) => [x.attempt, x.maxAttempts, x.outcome]), [[1, 3, "request_failed"], [2, 3, "http_response"]]);
  assert.ok(h.records.every((x) => x.startedAt === "2026-11-03T13:05:37.000Z" && x.finishedAt === x.startedAt));
  assert.equal(h.records[0].code, "ECONNRESET");
  assert.equal(h.records[1].status, 200);
});

test("exhausted readiness transport failures remain failures after three bounded attempts", async () => {
  let count = 0;
  const error = reset();
  const h = harness(async () => { count++; throw error; });
  await assert.rejects(() => resolveMonitorRelease(origin, "production", h.options), (actual) => actual === error);
  assert.equal(count, 3);
  assert.deepEqual(h.waits, [1000, 2000]);
  assert.deepEqual(h.records.map((x) => x.retrying), [true, true, false]);
});

test("readiness HTTP errors, redirects, malformed content and identity mismatches never retry", async (t) => {
  const cases = [
    ...[302, 401, 403, 404, 429, 500, 503].map((status) => ({ name: `HTTP ${status}`, make: () => new Response("{}", { status }) })),
    { name: "invalid JSON", make: () => new Response("not JSON") },
    { name: "wrong environment", make: () => new Response(JSON.stringify({ environment: "staging", release })) },
    { name: "missing release", make: () => new Response(JSON.stringify({ environment: "production" })) },
    { name: "non-exact release", make: () => new Response(JSON.stringify({ environment: "production", release: "main" })) },
  ];
  for (const scenario of cases) await t.test(scenario.name, async () => {
    let count = 0;
    const h = harness(async () => { count++; return scenario.make(); });
    await assert.rejects(() => resolveMonitorRelease(origin, "production", h.options));
    assert.equal(count, 1);
    assert.deepEqual(h.waits, []);
  });
});

test("certificate failures and errors after receiving a response are outside the retry boundary", async () => {
  const certificate = new TypeError("fetch failed", { cause: Object.assign(new Error("certificate"), { code: "CERT_HAS_EXPIRED" }) });
  let count = 0;
  const h = harness(async () => { count++; throw certificate; });
  await assert.rejects(() => fetchMonitorReadiness(origin, h.options), (actual) => actual === certificate);
  assert.equal(count, 1);
  assert.deepEqual(h.waits, []);
  const bodyError = reset();
  const body = harness(async () => ({ ok: true, status: 200, json: async () => { throw bodyError; } }));
  await assert.rejects(() => resolveMonitorRelease(origin, "production", body.options), (actual) => actual === bodyError);
  assert.equal(body.records.length, 1);
  assert.deepEqual(body.waits, []);
});

test("all monitor readiness callers use the bounded GET helper while effectful calls stay outside it", () => {
  for (const path of [".github/workflows/operational-alert-control-plane.yml", ".github/workflows/staging-capacity-observation.yml"])
    assert.match(readFileSync(path, "utf8"), /release="\$\(node scripts\/monitor-readiness\.mjs\)"/);
  for (const path of ["scripts/capacity-observe.mjs", "scripts/advanced-slo-observe.mjs"])
    assert.match(readFileSync(path, "utf8"), /const response = await fetchMonitorReadiness\(origin/);
  assert.doesNotMatch(readFileSync("scripts/github-alert-control-plane.mjs", "utf8"), /fetchMonitorReadiness|resolveMonitorRelease/);
});
