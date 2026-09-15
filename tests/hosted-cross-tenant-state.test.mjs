import assert from "node:assert/strict";
import test from "node:test";
import { checkCrossTenantStateIsolation } from "../scripts/hosted-acceptance.mjs";

const release = "a".repeat(40);
const tokenA = "cross-tenant-token-a-123456789";
const tokenB = "cross-tenant-token-b-123456789";

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function harness({ sameWorkspace = false } = {}) {
  const state = {
    [tokenA]: { workspace: "workspace-a", paused: false },
    [tokenB]: {
      workspace: sameWorkspace ? "workspace-a" : "workspace-b",
      paused: false,
    },
  };
  const mutations = [];
  const send = async (url, init = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/readiness.json") return json({ release });
    const authorization = init.headers?.Authorization || "";
    const bearer = authorization.replace(/^Bearer /, "");
    const current = state[bearer];
    if (init.headers?.Origin === "https://attacker.invalid")
      return json({ error: { code: "ORIGIN_REJECTED" } }, 403);
    if (!current) return json({ error: { code: "UNAUTHENTICATED" } }, 401);
    if (parsed.pathname.endsWith("/workspace_status"))
      return json({
        workspace: current.workspace,
        release,
        publishingPaused: current.paused,
      });
    if (parsed.pathname.endsWith("/publishing_pause")) {
      const body = JSON.parse(init.body || "{}");
      current.paused = body.paused;
      mutations.push({ bearer, paused: body.paused });
      return json({ paused: body.paused });
    }
    return json({ error: { code: "NOT_FOUND" } }, 404);
  };
  return { state, mutations, send };
}

test("cross-tenant state acceptance proves isolation and always restores the pause canary", async () => {
  const h = harness();
  const report = await checkCrossTenantStateIsolation(
    "https://poststeward-staging.example",
    tokenA,
    tokenB,
    release,
    h.send,
  );
  assert.equal(report.ready, true);
  assert.equal(report.release, release);
  assert.equal(report.distinctWorkspaces, true);
  assert.equal(report.workspaceLocalStateIsolation, true);
  assert.equal(report.hostileOriginReplay, "denied");
  assert.equal(report.providerEffectAttempted, false);
  assert.equal(report.paymentAttempted, false);
  assert.equal(report.recoveryAttempted, false);
  assert.equal(h.state[tokenA].paused, false);
  assert.equal(h.state[tokenB].paused, false);
  assert.deepEqual(
    h.mutations.map(({ bearer, paused }) => [bearer, paused]),
    [
      [tokenA, true],
      [tokenA, false],
    ],
  );
  const serialized = JSON.stringify(report);
  assert.equal(serialized.includes(tokenA), false);
  assert.equal(serialized.includes(tokenB), false);
  assert.equal(serialized.includes("workspace-a"), false);
  assert.equal(serialized.includes("workspace-b"), false);
});

test("cross-tenant state acceptance refuses same-workspace grants before mutation", async () => {
  const h = harness({ sameWorkspace: true });
  await assert.rejects(
    () =>
      checkCrossTenantStateIsolation(
        "https://poststeward-staging.example",
        tokenA,
        tokenB,
        release,
        h.send,
      ),
    /two distinct workspaces/,
  );
  assert.deepEqual(h.mutations, []);
});

test("cross-tenant state acceptance refuses a hosted release mismatch before mutation", async () => {
  const h = harness();
  await assert.rejects(
    () =>
      checkCrossTenantStateIsolation(
        "https://poststeward-staging.example",
        tokenA,
        tokenB,
        "b".repeat(40),
        h.send,
      ),
    /does not equal the reviewed workflow revision/,
  );
  assert.deepEqual(h.mutations, []);
});
