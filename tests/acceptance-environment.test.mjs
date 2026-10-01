import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, stat, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  initialize,
  acceptanceMatrix,
  spendEligibility,
  generate,
  redact,
  preflight,
  evidencePack,
} from "../scripts/acceptance/environment.mjs";
const release = "b".repeat(40),
  workspace = "00000000-0000-4000-8000-000000000001";
const model = {
  configured: true,
  configurationVersion: 2,
  limits: { allowAgents: true },
  routing: {
    accountId: "a".repeat(32),
    primary: { provider: "cloudflare_workers" },
    maxJobUsd: 0.05,
    maxDailyUsd: 0.1,
  },
};
const plan = () => ({
  release,
  disposableWorkspace: true,
  customerAccountAttestedDistinct: true,
  workspaceId: workspace,
  project: "fixture",
  sourcePrivacy: "public",
  sourceRepository: "example/controlled",
  spending: {
    approvedUsd: 0.2,
    maxRequests: 2,
    reservedUsd: 0,
    requests: 0,
    approvedByOwnerAt: "2026-10-01",
  },
  runs: [],
});
const send = async (path) =>
  path === "/health"
    ? { status: "ok", release }
    : path.includes("stable.json")
      ? { revision: release, runtime_tree_sha256: "c".repeat(64) }
      : path.endsWith("workspace_status")
        ? { workspace }
        : path.endsWith("model_status")
          ? model
          : path.endsWith("accounts_list")
            ? []
            : {};
test("private bundle reuses four source fixtures; no human/generated/passed evidence is fabricated", async () => {
  const root = await mkdtemp(join(tmpdir(), "poststeward-acceptance-"));
  try {
    const p = await initialize(root, release);
    assert.equal((await stat(root)).mode & 0o777, 0o700);
    assert.equal((await stat(join(root, "plan.json"))).mode & 0o777, 0o600);
    assert.equal(p.spending.approvedUsd, 0);
    assert.ok(p.matrix.every((r) => r.status === "not provisioned"));
    assert.equal(
      JSON.parse(await readFile(join(root, "fixtures.json"))).length,
      4,
    );
    assert.match(
      await readFile(join(root, "review-pack.md"), "utf8"),
      /NOT GENERATED/,
    );
    await assert.rejects(initialize(root, release), /already exists/);
  } finally {
    await rm(root, { recursive: true });
  }
});
test("public-only preflight is free, exact-pinned and never implies customer readiness", async () => {
  const calls = [];
  const free = async (...args) => {
    calls.push(args);
    return send(args[0]);
  };
  const result = await preflight({ release }, free);
  assert.equal(result.liveRelease, "passed");
  assert.equal(result.customerModel, "not provisioned");
  assert.equal(calls.length, 2);
  assert.ok(calls.every((c) => c.length === 1));
  await assert.rejects(preflight({ release: "d".repeat(40) }, free), /differs/);
  await assert.rejects(
    preflight(
      { ...plan(), workspaceId: "another" },
      send,
      "protected-synthetic-agent-token",
    ),
    /designated/,
  );
});
test("paid admission requires explicit allowance, dedicated account, grant policy and owner private disclosure", () => {
  for (const change of [
    { spending: { approvedUsd: 0 } },
    { customerAccountAttestedDistinct: false },
    { sourcePrivacy: "private" },
    { spending: { ...plan().spending, requests: 2 } },
    { spending: { ...plan().spending, reservedUsd: 0.18 } },
  ])
    assert.throws(() =>
      spendEligibility({ ...plan(), ...change }, model, "cloudflare_workers"),
    );
  assert.throws(() =>
    spendEligibility(
      plan(),
      { ...model, limits: { allowAgents: false } },
      "cloudflare_workers",
    ),
  );
  assert.throws(() => spendEligibility(plan(), model, "cloudflare_gateway"));
  assert.equal(spendEligibility(plan(), model, "cloudflare_workers"), 0.05);
});
test("generation writes allowance/key before the one product mutation and retains unknown outcomes", async () => {
  const p = plan();
  const saves = [];
  let mutations = 0;
  const http = async (path, input, ...args) => {
    if (path.endsWith("preparation_create")) {
      mutations++;
      assert.equal(saves[0].spending.reservedUsd, 0.05);
      assert.ok(saves[0].runs[0].idempotencyKey);
      assert.equal(input.selection.allowPrivate, false);
      throw new Error("network lost");
    }
    return send(path, input, ...args);
  };
  await assert.rejects(
    generate(
      p,
      {
        id: "feature",
        audience: "Test operators",
        objective: "Assess source-backed clarity",
      },
      "cloudflare_workers",
      async (v) => saves.push(structuredClone(v)),
      http,
      "protected-synthetic-agent-token",
    ),
    /uncertain/,
  );
  assert.equal(mutations, 1);
  assert.equal(p.runs[0].status, "uncertain");
  assert.equal(p.spending.reservedUsd, 0.05);
  await assert.rejects(
    generate(
      p,
      { id: "feature" },
      "cloudflare_workers",
      async () => {},
      http,
      "protected-synthetic-agent-token",
    ),
    /existing run/,
  );
  assert.equal(mutations, 1);
});
test("credential evidence is redacted; source and attribution retained privately with no fabricated human/payment proof", () => {
  const key = "private-cloudflare-credential";
  assert.deepEqual(redact({ apiKey: key, text: "value " + key }, [key]), {
    apiKey: "[redacted]",
    text: "value [redacted]",
  });
  const pack = evidencePack(
    {
      id: "job",
      routing: { accountId: "a".repeat(32) },
      attempts: [
        {
          fundingProof: { account: "a".repeat(32), liveInvoiceVerified: false },
        },
      ],
      drafts: [{ text: "Source-backed authored acceptance result" }],
    },
    key,
  );
  assert.ok(!JSON.stringify(pack).includes("a".repeat(32)));
  assert.equal(pack.humanAssessment, "not performed");
  assert.equal(pack.liveInvoiceVerified, false);
  assert.ok(
    acceptanceMatrix(release).some((r) => r.id.includes("LinkedIn Page")),
  );
});
