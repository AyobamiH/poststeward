import assert from "node:assert/strict";
import test from "node:test";
import { assertRetainedRoots, runRootCutover } from "../scripts/root-cutover.mjs";

const release = "a".repeat(40), token = "b".repeat(64), targetRoot = "c".repeat(64);
test("ordinary redeployment cannot lose either retained key or roll writes back", () => {
  const current = { ENCRYPTION_LEGACY_ROOT_ID: "old", ENCRYPTION_NEXT_ROOT_ID: "next", ENCRYPTION_ROOT_WRITE: "next" };
  assert.doesNotThrow(() => assertRetainedRoots(current, { ...current }));
  for (const field of ["ENCRYPTION_LEGACY_ROOT_ID", "ENCRYPTION_NEXT_ROOT_ID"]) {
    const missing = { ...current }; delete missing[field];
    assert.throws(() => assertRetainedRoots(current, missing), /retained root/);
    assert.throws(() => assertRetainedRoots(current, { ...current, [field]: "wrong" }), /retained root/);
  }
  assert.throws(() => assertRetainedRoots(current, { ...current, ENCRYPTION_ROOT_WRITE: "legacy" }), /roll/);
});
test("protected cutover driver traverses pages, applies exact inspected digest and separately verifies completion", async () => {
  const calls = [], state = { one: 1, two: 0 };
  const report = await runRootCutover({ origin: "https://publish.example", release, token, send: async (url, init) => {
    assert.equal(url, "https://publish.example/internal/root-cutover");
    assert.equal(init.redirect, "error");
    assert.equal(init.headers.Authorization, "Bearer " + token);
    const input = JSON.parse(init.body); calls.push(input);
    assert.equal(input.release, release);
    let value;
    if (input.action === "list") value = input.cursor ? { workspaces: ["two"], next: null } : { workspaces: ["one"], next: "one" };
    else if (input.action === "inspect") value = { pending: state[input.workspace], verifiedComplete: !state[input.workspace], inventoryDigest: "d".repeat(64), credentialCount: 1, observedAt: 1 };
    else {
      assert.equal(input.expectedDigest, "d".repeat(64));
      assert.equal(input.workspace, "one");
      state.one = 0; value = { changed: 1 };
    }
    return Response.json({ ...value, release, targetRoot });
  }});
  assert.equal(report.workspaceCount, 2);
  assert.equal(report.changed, 1);
  assert.equal(report.credentialCount, 2);
  assert.equal(calls.filter(x => x.action === "migrate").length, 1);
  assert.equal(calls.filter(x => x.action === "inspect").length, 3);
  assert.ok(report.oldRootRetained);
  assert.ok(!JSON.stringify(report).includes(token));
});
test("cutover driver stops on changed release or failed writes without blindly retrying", async () => {
  await assert.rejects(runRootCutover({ origin: "https://publish.example", release, token,
    send: async () => Response.json({ release: "e".repeat(40) }) }), /release changed/);
  let calls = 0;
  await assert.rejects(runRootCutover({ origin: "https://publish.example", release, token,
    send: async () => { calls++; return new Response("private diagnostic", { status: 409 }); } }), /HTTP 409/);
  assert.equal(calls, 1);
});
