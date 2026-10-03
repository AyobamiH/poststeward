import assert from "node:assert/strict";
import test from "node:test";
import { Engine } from "../src/engine.ts";
import { harness, owner } from "./helpers.ts";
import { invalidateRestoredAuthority } from "../src/recovery-local.ts";

const selection = {
  repository: "example/product",
  releaseTag: "v1.0",
  documentationPaths: ["README.md"],
  allowPrivate: false,
  allowUnreleased: false,
};
const context = {
  audience: "Builders delegating routine publishing",
  objective: "Explain documented capabilities through useful audience problems",
  brandVoice: "Specific British English",
  productContext: "A tool recording delivery failures and recovery steps",
  exclusions: "No outcome guarantees",
  callToAction: "Read the documentation",
};
const quote =
  "Records delivery failures and recovery steps in a diagnostic timeline.";
const reference = { evidence: "docs", quote };
const texts = [
  "A failed delivery leaves a trail. The diagnostic timeline records failures and recovery steps, giving operators a place to begin their investigation.",
  "Writing an incident handover? Documented recovery steps in the timeline can supply concrete detail for a colleague taking over your investigation.",
  "When preparing an operational checklist, consider where your team will look for recorded delivery failures. The diagnostic timeline documents those events.",
];
async function fixture(
  options: { issues?: boolean; duplicate?: boolean; advisory?: boolean } = {},
) {
  const h = harness();
  await h.setup();
  h.env.ADVANCED_ENABLED = "true";
  h.store.put("entitlement", {
    kind: "subscription",
    until: h.options.now() + 86400000,
    reference: "fixture",
  });
  let sourceSha = "a".repeat(40),
    count = 0,
    evidenceText = quote;
  const materials: any[] = [];
  const engine = new Engine(h.store, h.env, h.provider, {
    ...h.options,
    preparationEvidence: async () => ({
      sha: sourceSha,
      evidence: [
        {
          id: "docs",
          kind: "documentation",
          url: "https://github.com/example/product/blob/v1.0/README.md",
          text: evidenceText,
        },
      ],
      gaps: options.advisory
        ? [
            "No previous release selected; only explicit documented facts are supported.",
          ]
        : [],
      coverage: "Synthetic fixture",
    }),
    preparationModel: async (_key, stage, material) => {
      materials.push(material);
      const value =
        stage === "interpret"
          ? {
              changes: [
                {
                  fact: quote,
                  sources: [reference],
                  audienceProblem: "An operator needs investigation evidence",
                  implication: "Recorded detail can inform a handover",
                },
              ],
              positioning: "Explain concrete investigation support",
              objective: context.objective,
              audience: context.audience,
              channelApproach: "Useful specific problems with bounded claims",
              missingContext: [],
              risks: ["No speed guarantee"],
            }
          : stage === "draft"
            ? {
                drafts: [
                  {
                    alias: "account",
                    text: texts[options.duplicate ? 0 : count++ % texts.length],
                    claims: [{ claim: quote, sources: [reference] }],
                    rationale: "Explain a distinct supported use case",
                  },
                ],
              }
            : {
                acceptableForOwnerReview: !options.issues,
                issues: options.issues
                  ? [
                      {
                        alias: "account",
                        category: "unsupported_claim",
                        detail: "The claim is not supported by this evidence",
                      },
                    ]
                  : [],
                summary: "Checks completed on a synthetic fixture",
              };
      return { value, inputTokens: 100, outputTokens: 100, latencyMs: 1 };
    },
  });
  const run = (name: string, input: any = {}, actor = owner) =>
    engine.run(name, input, actor) as Promise<any>;
  await run("model_connect", {
    apiKey: "fixture-workspace-model-key",
    maxJobsPerDay: 8,
    allowAgents: false,
    inputUsdPerMillion: 0.4,
    outputUsdPerMillion: 1.6,
    maxDailyUsd: 1,
    idempotencyKey: "model-fixture",
  });
  const configure = (extra = {}) =>
    run("autonomy_configure", {
      project: "project",
      selection,
      context,
      enabled: true,
      intervalMinutes: 60,
      stockFloor: 1,
      maxDailyDeliveries: 2,
      ...extra,
      idempotencyKey: crypto.randomUUID(),
    });
  const produce = async () => {
    await engine.autonomy.tick();
    for (let i = 0; i < 4; i++) await engine.preparation.tick();
    h.advance(60001);
    await engine.tick();
  };
  return {
    ...h,
    engine,
    run,
    configure,
    produce,
    materials,
    changeEvidence: () => {
      evidenceText = quote + " Updated qualifications for the same commit.";
    },
    changeSource: () => {
      sourceSha = "b".repeat(40);
    },
  };
}

test("standing setup produces, checks, publishes and replenishes without per-copy approval", async () => {
  const h = await fixture();
  await h.configure();
  await h.produce();
  assert.equal(h.calls.publish, 1);
  assert.equal(h.store.list<any>("delivery:")[0].automatic, true);
  assert.equal(h.store.list<any>("delivery:")[0].status, "published_verified");
  assert.equal(h.store.list<any>("delivery:")[0].approval, undefined);
  h.advance(1001);
  await h.produce();
  const deliveries = h.store.list<any>("delivery:");
  assert.equal(deliveries.length, 2);
  assert.equal(deliveries[1].status, "scheduled");
  assert.ok(deliveries[1].dueAt >= deliveries[0].dueAt + 3600000);
  assert.ok(h.materials.some((m) => m.priorPublications?.length));
  h.advance(3600000);
  await h.engine.tick();
  assert.equal(h.calls.publish, 2);
});
test("an agent cannot grant itself standing owner authority", async () => {
  const h = await fixture();
  await assert.rejects(
    h.run(
      "autonomy_configure",
      {
        project: "project",
        selection,
        context,
        enabled: true,
        intervalMinutes: 60,
        stockFloor: 1,
        maxDailyDeliveries: 2,
        idempotencyKey: "agent-grant",
      },
      { ...owner, grant: "grant" },
    ),
    /owner must choose/,
  );
});
test("standing policy does not release arbitrary agent copy or existing pending deliveries", async () => {
  const h = await fixture();
  const campaign = await h.campaign("Unreviewed agent text");
  const agent = { ...owner, scopes: ["publish" as const], grant: "agent" };
  const before = await h.run(
    "publish_now",
    { campaign: campaign.id, idempotencyKey: "agent-before" },
    agent,
  );
  await h.configure();
  const after = await h.run(
    "publish_now",
    { campaign: campaign.id, idempotencyKey: "agent-after" },
    agent,
  );
  assert.equal(before.deliveries[0].status, "pending_approval");
  assert.equal(after.deliveries[0].status, "pending_approval");
});
test("editorial issues hold supply and do not publish or automatically retry inference", async () => {
  const h = await fixture({ issues: true });
  await h.configure();
  await h.produce();
  assert.equal(h.calls.publish, 0);
  assert.ok((await h.run("autonomy_list"))[0].error);
  const calls = h.materials.length;
  h.advance(86400000);
  await h.engine.autonomy.tick();
  assert.equal(h.materials.length, calls);
});
test("duplicate supply holds while preserving the first publication", async () => {
  const h = await fixture({ duplicate: true });
  await h.configure();
  await h.produce();
  h.advance(1001);
  await h.produce();
  assert.equal(h.calls.publish, 1);
  assert.equal(
    (await h.run("autonomy_list"))[0].error,
    "AUTONOMY_DUPLICATE_COPY",
  );
});
test("standing pause revokes scheduled effects", async () => {
  const h = await fixture();
  await h.configure();
  await h.produce();
  h.advance(1001);
  await h.produce();
  await h.run("autonomy_pause", {
    project: "project",
    idempotencyKey: "pause-standing",
  });
  h.advance(3600000);
  await h.engine.tick();
  assert.equal(h.calls.publish, 1);
  assert.equal(h.store.list<any>("delivery:")[1].status, "drift_blocked");
});
test("changed source blocks before provider creation", async () => {
  const h = await fixture();
  await h.configure();
  await h.produce();
  h.advance(1001);
  await h.produce();
  h.changeSource();
  h.advance(3600000);
  await h.engine.tick();
  assert.equal(h.calls.publish, 1);
  assert.equal(h.store.list<any>("delivery:")[1].status, "drift_blocked");
});
test("changed account routing blocks autonomous scheduling and generation", async () => {
  const h = await fixture();
  await h.configure();
  const account = h.store.get<any>("account:account");
  account.version++;
  h.store.put("account:account", account);
  await h.engine.autonomy.tick();
  assert.equal(
    (await h.run("autonomy_list"))[0].error,
    "STANDING_ACCOUNT_DRIFT",
  );
  assert.equal(h.materials.length, 0);
});
test("restored snapshots invalidate standing authority", async () => {
  const h = await fixture();
  await h.configure();
  await invalidateRestoredAuthority(
    h.store,
    h.env,
    owner.workspace,
    h.options.now(),
  );
  assert.equal((await h.run("autonomy_list"))[0].enabled, false);
  await h.engine.autonomy.tick();
  assert.equal(h.materials.length, 0);
});

test("unapproved agent spending cannot create demand or hold the owner's producer", async () => {
  const h = await fixture();
  await h.configure();
  await assert.rejects(
    h.run(
      "autonomy_request",
      { project: "project", idempotencyKey: "agent-demand" },
      { ...owner, scopes: ["campaign:write"], grant: "agent" },
    ),
    /not allowed campaign-scoped agents/,
  );
  const policy = (await h.run("autonomy_list"))[0];
  assert.equal(policy.job, undefined);
  assert.equal(policy.error, undefined);
  await h.produce();
  assert.equal(h.calls.publish, 1);
});

test("concurrent permitted agent requests reuse one durable supply request", async () => {
  const h = await fixture();
  await h.run("model_connect", {
    apiKey: "fixture-workspace-model-key",
    maxJobsPerDay: 8,
    allowAgents: true,
    inputUsdPerMillion: 0.4,
    outputUsdPerMillion: 1.6,
    maxDailyUsd: 1,
    idempotencyKey: "model-agent-consent",
  });
  await h.configure();
  const agent = {
    ...owner,
    scopes: ["campaign:write" as const],
    grant: "agent",
  };
  const results = await Promise.all(
    ["agent-request-1", "agent-request-2"].map((idempotencyKey) =>
      h.run("autonomy_request", { project: "project", idempotencyKey }, agent),
    ),
  );
  assert.equal(results[0].preparation, results[1].preparation);
  assert.equal(h.store.list("preparation:").length, 1);
  for (let i = 0; i < 4; i++) await h.engine.preparation.tick();
  h.advance(1001);
  await h.engine.tick();
  assert.equal(h.calls.publish, 1);
});

test("unstarted daily budget exhaustion defers and resumes without owner intervention", async () => {
  const h = await fixture();
  await h.configure();
  const day = new Date(h.options.now()).toISOString().slice(0, 10);
  h.store.put("preparation-usage:" + day, {
    jobs: 8,
    calls: 24,
    reservedInputTokens: 0,
    reservedOutputTokens: 0,
    inputTokens: 0,
    outputTokens: 0,
    estimatedUsd: 0,
  });
  await h.engine.autonomy.tick();
  const policy = (await h.run("autonomy_list"))[0];
  assert.equal(policy.error, undefined);
  assert.equal(policy.deferred, "PREPARATION_DAILY_LIMIT");
  assert.equal(h.store.list("preparation:").length, 0);
  h.advance(13 * 3600000);
  await h.engine.autonomy.tick();
  assert.equal(h.store.list("preparation:").length, 1);
});

test("advisory source coverage does not impose routine approval on checked facts", async () => {
  const h = await fixture({ advisory: true });
  await h.configure();
  await h.produce();
  assert.equal(h.calls.publish, 1);
});

test("edited source content at the same SHA blocks a captured delivery", async () => {
  const h = await fixture();
  await h.configure();
  await h.produce();
  h.advance(1001);
  await h.produce();
  h.changeEvidence();
  h.advance(3600000);
  await h.engine.tick();
  assert.equal(h.calls.publish, 1);
  assert.equal(h.store.list<any>("delivery:")[1].status, "drift_blocked");
});
