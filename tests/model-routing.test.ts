import assert from "node:assert/strict";
import test from "node:test";
import { Engine } from "../src/engine.ts";
import { Fault } from "../src/common.ts";
import {
  routingSchema,
  verifyCloudflare,
  cloudflareModel,
  pipelineCeiling,
} from "../src/model-routing.ts";
import { strategySchema } from "../src/preparation-contracts.ts";
import { harness, owner } from "./helpers.ts";

const account = "a".repeat(32),
  token = "cloudflare-test-token-never-live";
const reference = {
  evidence: "release",
  quote:
    "Adds a diagnostic timeline recording delivery failures and recovery steps.",
};
const context = {
  audience: "Operators investigating delivery failures",
  objective: "Explain the diagnostic timeline and its practical use",
  brandVoice: "Specific, restrained British English",
  productContext: "A reviewed release communication tool for operators",
  exclusions: "No unsupported performance promises",
  callToAction: "Read the release notes",
};
const strategy = {
  changes: [
    {
      fact: "Adds a diagnostic timeline",
      sources: [reference],
      audienceProblem: "Operators need to inspect failure sequences",
      implication:
        "Use the timeline as a starting point, without a recovery-speed promise",
    },
  ],
  positioning: "Understand the sequence around a delivery failure",
  objective: context.objective,
  audience: context.audience,
  channelApproach:
    "Explain one supported operator benefit and link release notes",
  missingContext: [],
  risks: [],
};
const drafts = {
  drafts: [
    {
      alias: "account",
      text: "Investigating a failed delivery? This release adds a diagnostic timeline recording failures and recovery steps. Read the release notes.",
      claims: [
        {
          claim: "A diagnostic timeline records failures and recovery steps",
          sources: [reference],
        },
      ],
      rationale: "A source-backed update for operators",
    },
  ],
};
const critique = {
  acceptableForOwnerReview: true,
  issues: [],
  summary: "Source support and channel suitability require final owner review.",
};
const workers = {
  provider: "cloudflare_workers" as const,
  model: "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
  funding: "workers_ai" as const,
};
const gateway = {
  provider: "cloudflare_gateway" as const,
  model: "gpt-4.1-mini",
  funding: "gateway_credits" as const,
  gatewayId: "acceptance",
};
const policy = (primary = workers as any, fallbacks: any[] = []) =>
  routingSchema.parse({
    version: 1,
    accountId: account,
    primary,
    fallbacks,
    maxInputBytes: 24000,
    maxOutputTokens: 4000,
    temperature: 0.2,
    maxJobUsd: 0.15,
    maxDailyUsd: 1,
    logging: "metadata",
  });
function transport(overrides: any = {}) {
  const calls: { url: string; options: RequestInit }[] = [];
  const send = async (url: any, options: any = {}) => {
    calls.push({ url: String(url), options });
    assert.ok(
      String(url).startsWith("https://api.cloudflare.com/") ||
        String(url).startsWith("https://gateway.ai.cloudflare.com/") ||
        String(url).startsWith("https://developers.cloudflare.com/"),
    );
    if (options.method !== "POST") {
      if (overrides.read) {
        const custom = await overrides.read(String(url));
        if (custom) return custom;
      }
      if (String(url).startsWith("https://developers.cloudflare.com/")) {
        assert.equal(options.headers, undefined);
        const large = String(url).includes("gpt-4.1/index");
        return new Response(
          "`openai/" +
            (large ? "gpt-4.1" : "gpt-4.1-mini") +
            "` Responses Input (per 1M tokens)$" +
            (large ? "2.00" : "0.40") +
            " Output (per 1M tokens)$" +
            (large ? "8.00" : "1.60"),
        );
      }
      let result: any = null;
      if (String(url).includes("models/search"))
        result = [workers.model, "@cf/meta/llama-3.1-8b-instruct"].map(
          (name) => ({ name }),
        );
      else if (String(url).includes("provider_configs"))
        result = overrides.keys || [];
      else if (String(url).includes("credit-balance"))
        result = { balance: overrides.balance ?? 100 };
      else
        result = {
          id: "acceptance",
          byok_only: false,
          retry_max_attempts: 0,
          spend_limits: {
            enabled: true,
            rules: [
              { limitType: "cost", limit: 1, window: 86400, enabled: true },
            ],
          },
          workers_ai_billing_mode: "unified",
          modified_at: "2026-10-01T00:00:00Z",
          ...overrides.gateway,
        };
      return Response.json({ success: true, result });
    }
    if (overrides.post) {
      const custom = await overrides.post(String(url), options);
      if (custom) return custom;
    }
    const body = JSON.parse(options.body),
      messages = body.messages || body.input;
    const payload = JSON.parse(messages[1].content);
    assert.ok(!JSON.stringify(payload).includes(token));
    const value =
      payload.stage === "interpret"
        ? strategy
        : payload.stage === "draft"
          ? drafts
          : critique;
    if (String(url).includes("gateway.ai"))
      return Response.json({
        status: "completed",
        gatewayMetadata: { keySource: "Unified" },
        output: [
          {
            type: "message",
            content: [{ type: "output_text", text: JSON.stringify(value) }],
          },
        ],
        usage: { input_tokens: 1000, output_tokens: 500 },
      });
    return Response.json({
      success: true,
      result: {
        response: value,
        usage: { prompt_tokens: 1000, completion_tokens: 500 },
      },
    });
  };
  return { send: send as typeof fetch, calls };
}
async function fixture(
  primary: any = workers,
  overrides: any = {},
  fallbacks: any[] = [],
) {
  const h = harness();
  h.advance(22 * 86400000);
  await h.setup();
  const http = transport(overrides);
  const engine = new Engine(h.store, h.env, h.provider, {
    ...h.options,
    preparationSend: http.send,
    preparationEvidence: async () => ({
      sha: "b".repeat(40),
      evidence: [
        {
          id: "release",
          kind: "release",
          url: "https://github.com/example/product/releases/tag/v1",
          text: reference.quote,
        },
      ],
      gaps: [],
      coverage: "Synthetic fixture, no live provider or model account.",
    }),
  });
  const run = (name: string, input: any = {}, actor = owner) =>
    engine.run(name, input, actor) as Promise<any>;
  const input = {
    apiKey: token,
    routing: policy(primary, fallbacks),
    maxJobsPerDay: 8,
    allowAgents: false,
    inputUsdPerMillion: null,
    outputUsdPerMillion: null,
    maxDailyUsd: null,
    idempotencyKey: "connect-cloudflare",
  };
  await run("model_connect", input);
  const create = () =>
    run("preparation_create", {
      project: "project",
      context,
      selection: {
        repository: "example/product",
        releaseTag: "v1",
        documentationPaths: [],
        allowPrivate: false,
        allowUnreleased: false,
      },
      idempotencyKey: crypto.randomUUID(),
    });
  const complete = async () => {
    const job = await create();
    for (let i = 0; i < 4; i++) await engine.preparation.tick();
    return engine.preparation.get(job.id);
  };
  return { ...h, ...http, engine, run, input, create, complete };
}

for (const route of [workers, gateway])
  test(`${route.provider}: source→strategy→draft→check retains exact owner handoff and records route/cost`, async () => {
    const h = await fixture(route),
      job = await h.complete();
    assert.ok(job.attempts && job.budget);
    assert.equal(job.status, "review");
    assert.equal(job.attempts.length, 3);
    assert.ok(
      job.attempts.every(
        (a: any) =>
          a.model === route.model &&
          a.funding === route.funding &&
          a.outcome === "reported_usage",
      ),
    );
    assert.equal(job.budget.reservedMicros, 0);
    assert.ok(job.budget.chargedMicros > 0);
    const status = await h.run("model_status");
    assert.equal(status.usage.reservedMicros, 0);
    assert.equal(status.usage.uncertainMicros, 0);
    assert.ok(status.usage.settledMicros > 0);
    assert.ok(!JSON.stringify(status).includes(token));
    assert.ok(!JSON.stringify(job).includes(token));
    assert.equal(h.calls.filter((c) => c.options.method === "POST").length, 3);
    assert.equal(h.store.list("delivery:").length, 0);
    await assert.rejects(
      h.run(
        "preparation_approve",
        {
          id: job.id,
          revision: job.revision,
          digest: job.digest,
          idempotencyKey: "agent-approve",
        },
        { ...owner, grant: "agent" },
      ),
      /owner/,
    );
    const approved = await h.run("preparation_approve", {
      id: job.id,
      revision: job.revision,
      digest: job.digest,
      idempotencyKey: "exact-owner-approval",
    });
    assert.ok(approved.campaign);
    assert.equal(
      h.calls.filter((c) => c.url.includes("api.openai.com")).length,
      0,
    );
  });
test("connection validation has zero inference; conflicts, balance and management permission fail closed", async () => {
  for (const overrides of [
    { keys: [{ provider_slug: "openai", default_config: true }] },
    { balance: 0 },
    { gateway: { byok_only: true } },
    { gateway: { retry_max_attempts: 2 } },
    { read: () => new Response("private-error-with-token", { status: 403 }) },
  ]) {
    const http = transport(overrides),
      r = policy(gateway);
    await assert.rejects(
      verifyCloudflare(
        r,
        r.primary,
        token,
        Date.parse("2026-10-01"),
        http.send,
      ),
      (error) =>
        error instanceof Fault && !error.message.includes("private-error"),
    );
    assert.ok(http.calls.every((c) => c.options.method !== "POST"));
  }
});
test("owner limits are reserved atomically for parallel jobs near exhaustion", async () => {
  const h = await fixture();
  const limit = pipelineCeiling(h.input.routing, 3) / 1e6;
  await h.run("model_connect", {
    ...h.input,
    routing: { ...h.input.routing, maxDailyUsd: limit * 1.5 },
    idempotencyKey: "tight-budget",
  });
  const outcomes = await Promise.allSettled([h.create(), h.create()]);
  assert.equal(outcomes.filter((x) => x.status === "fulfilled").length, 1);
  assert.equal(outcomes.filter((x) => x.status === "rejected").length, 1);
  assert.equal(h.calls.filter((c) => c.options.method === "POST").length, 0);
});
test("explicit fallback works without switching account; off/uncertain/authentication never falls back", async () => {
  const rejectPrimary = {
    post: (url: string) =>
      url.includes("api.cloudflare.com")
        ? new Response(null, { status: 404 })
        : undefined,
  };
  const h = await fixture(workers, rejectPrimary, [gateway]);
  const job = await h.complete();
  assert.ok(job.attempts);
  assert.equal(job.status, "review");
  assert.equal(job.attempts.length, 6);
  assert.equal(job.attempts.filter((a: any) => a.fallback).length, 3);
  assert.ok((await h.run("model_status")).usage.uncertainMicros > 0);
  const disabled = await fixture(workers, rejectPrimary);
  assert.equal((await disabled.complete()).status, "failed");
  assert.equal(
    disabled.calls.filter((c) => c.options.method === "POST").length,
    1,
  );
  for (const post of [
    () => {
      throw new Error("timeout");
    },
    () => new Response(null, { status: 401 }),
  ]) {
    const stopped = await fixture(workers, { post }, [gateway]);
    const outcome = await stopped.complete();
    assert.ok(["uncertain", "failed"].includes(outcome.status));
    assert.equal(
      stopped.calls.filter((c) => c.options.method === "POST").length,
      1,
    );
  }
});
test("changed gateway keys and queued policy changes prevent later paid attempts", async () => {
  let conflict = false;
  const h = await fixture(gateway, {
    read: (url: string) =>
      conflict && url.includes("provider_configs")
        ? Response.json({
            success: true,
            result: [{ provider_slug: "openai" }],
          })
        : undefined,
  });
  const job = await h.create();
  await h.engine.preparation.tick();
  conflict = true;
  await h.engine.preparation.tick();
  assert.equal(
    h.engine.preparation.get(job.id).error?.code,
    "MODEL_STORED_KEY_CONFLICT",
  );
  assert.equal(h.calls.filter((c) => c.options.method === "POST").length, 0);
  const q = await fixture();
  const queued = await q.create();
  await q.run("model_connect", {
    ...q.input,
    apiKey: undefined,
    routing: { ...q.input.routing, maxDailyUsd: 0.1 },
    idempotencyKey: "stricter-policy",
  });
  await q.engine.preparation.tick();
  assert.equal(
    q.engine.preparation.get(queued.id).error?.code,
    "MODEL_AUTHORITY_CHANGED",
  );
});
test("cancellation retains in-flight ceiling then settles late usage without resurrecting work", async () => {
  let release!: () => void, entered!: () => void;
  const started = new Promise<void>((r) => {
    entered = r;
  });
  const pending = new Promise<void>((r) => {
    release = r;
  });
  const h = await fixture(workers, {
    post: async () => {
      entered();
      await pending;
    },
  });
  const job = await h.create();
  await h.engine.preparation.tick();
  const tick = h.engine.preparation.tick();
  await started;
  await h.run("preparation_reject", {
    id: job.id,
    revision: 1,
    idempotencyKey: "reject-in-flight",
  });
  const during = await h.run("model_status");
  assert.equal(during.usage.reservedMicros, 0);
  assert.ok(during.usage.uncertainMicros > 0);
  release();
  await tick;
  assert.equal(h.engine.preparation.get(job.id).status, "rejected");
  const after = await h.run("model_status");
  assert.equal(after.usage.uncertainMicros, 0);
  assert.ok(after.usage.settledMicros > 0);
});
test("workspace encryption rejects cross-tenant key copy and disconnection prevents generation", async () => {
  const h = await fixture();
  const job = await h.create();
  await h.engine.preparation.tick();
  await h.run("model_disconnect", { idempotencyKey: "disconnect" });
  await h.engine.preparation.tick();
  assert.equal(h.calls.filter((c) => c.options.method === "POST").length, 0);
  assert.equal((await h.run("model_status")).configured, false);
  const other = { ...owner, workspace: "different-workspace" };
  const bad = await fixture();
  await assert.rejects(
    bad.run(
      "model_connect",
      { ...bad.input, apiKey: undefined, idempotencyKey: "copied-key" },
      other,
    ),
  );
});
test("route validation rejects arbitrary endpoints, duplicate fallbacks, unsupported models and cross funding", () => {
  for (const change of [
    { accountId: "https://evil.test" },
    { primary: { ...gateway, funding: "workers_ai" } },
    { primary: { ...workers, model: "unknown" } },
    { fallbacks: [workers] },
    { endpoint: "https://evil.test" },
  ])
    assert.equal(
      routingSchema.safeParse({ ...policy(), ...change }).success,
      false,
    );
});
test("adapter suppresses cache/payload logs, limits output and rejects bad schema/usage without leaking upstream errors", async () => {
  const r = policy(),
    http = transport();
  const result = await cloudflareModel(r, r.primary, http.send)(
    token,
    "interpret",
    { context },
    strategySchema,
  );
  assert.equal(result.inputTokens, 1000);
  const call = http.calls[0];
  const headers = call.options.headers as any;
  assert.equal(headers["cf-aig-collect-log-payload"], "false");
  assert.equal(headers["cf-aig-skip-cache"], "true");
  assert.equal(JSON.parse(String(call.options.body)).max_tokens, 4000);
  for (const body of [
    {
      success: true,
      result: {
        response: { wrong: "shape" },
        usage: { prompt_tokens: 1000, completion_tokens: 500 },
      },
    },
    {
      success: true,
      result: {
        response: strategy,
        usage: { prompt_tokens: 999999, completion_tokens: 500 },
      },
    },
  ]) {
    await assert.rejects(
      cloudflareModel(r, r.primary, async () => Response.json(body))(
        token,
        "interpret",
        {},
        strategySchema,
      ),
    );
  }
});

test("credit attribution, secondary spend guard and changed catalogue prices fail closed", async () => {
  for (const gateway of [
    { spend_limits: null },
    {
      spend_limits: {
        enabled: true,
        rules: [
          {
            limitType: "cost",
            limit: 1,
            window: 86400,
            model: { mode: "filter", values: ["another-model"] },
          },
        ],
      },
    },
  ]) {
    const http = transport({ gateway });
    const r = policy({
      ...workers,
      funding: "gateway_credits",
      gatewayId: "acceptance",
    });
    await assert.rejects(
      verifyCloudflare(
        r,
        r.primary,
        token,
        Date.parse("2026-10-01"),
        http.send,
      ),
      /gateway-wide spend limit/,
    );
    assert.ok(http.calls.every((c) => c.options.method !== "POST"));
  }
  const changed = transport({
    read: (url: string) =>
      url.startsWith("https://developers.")
        ? new Response(
            "`openai/gpt-4.1-mini` Responses Input (per 1M tokens)$0.80 Output (per 1M tokens)$1.60",
          )
        : undefined,
  });
  await assert.rejects(
    verifyCloudflare(
      policy(gateway),
      gateway,
      token,
      Date.parse("2026-10-01"),
      changed.send,
    ),
    /pricing differs/,
  );
  for (const source of [undefined, "BYOK"]) {
    const h = await fixture(
      gateway,
      {
        post: () =>
          Response.json({
            status: "completed",
            gatewayMetadata: { keySource: source },
            output: [],
            usage: { input_tokens: 1000, output_tokens: 500 },
          }),
      },
      [workers],
    );
    const job = await h.complete();
    assert.equal(job.error?.code, "MODEL_FUNDING_UNVERIFIED");
    assert.equal(h.calls.filter((c) => c.options.method === "POST").length, 1);
    assert.ok((await h.run("model_status")).usage.uncertainMicros > 0);
  }
});
test("freshness, credential rotation, grant revocation and UTC rollover preserve spending evidence", async () => {
  const h = await fixture();
  const job = await h.create();
  await h.engine.preparation.tick();
  const rotated = "rotated-synthetic-cloudflare-token";
  await h.run("model_connect", {
    ...h.input,
    apiKey: rotated,
    idempotencyKey: "rotation",
  });
  await h.engine.preparation.tick();
  assert.equal(
    h.engine.preparation.get(job.id).error?.code,
    "MODEL_AUTHORITY_CHANGED",
  );
  const fresh = await h.complete();
  assert.equal(fresh.status, "review");
  assert.ok(
    h.calls
      .filter((c) => c.options.method === "POST")
      .every(
        (c) => (c.options.headers as any).Authorization === "Bearer " + rotated,
      ),
  );
  const revoked = await fixture();
  await revoked.create();
  revoked.authorize(false);
  await revoked.engine.preparation.tick();
  assert.equal(
    revoked.calls.filter((c) => c.options.method === "POST").length,
    0,
  );
  const midnight = await fixture();
  const pending = await midnight.create();
  await midnight.engine.preparation.tick();
  await midnight.engine.preparation.tick();
  midnight.advance(86400000);
  await midnight.engine.preparation.tick();
  await midnight.engine.preparation.tick();
  assert.equal(midnight.engine.preparation.get(pending.id).status, "review");
  assert.equal((await midnight.run("model_status")).usage.reservedMicros, 0);
  await assert.rejects(
    verifyCloudflare(
      policy(),
      workers,
      token,
      Date.parse("2026-11-01"),
      transport().send,
    ),
    /pricing catalogue expired/,
  );
});

test("metadata latency cannot start inference under an expired execution claim", async () => {
  let expire = false;
  let advance!: (ms: number) => void;
  const h = await fixture(workers, {
    read: () => {
      if (expire) {
        advance(400000);
        expire = false;
      }
    },
  });
  advance = h.advance;
  const job = await h.create();
  await h.engine.preparation.tick();
  expire = true;
  await h.engine.preparation.tick();
  assert.equal(h.calls.filter((c) => c.options.method === "POST").length, 0);
  await h.engine.preparation.tick();
  assert.equal(h.engine.preparation.get(job.id).status, "uncertain");
  assert.equal((await h.run("model_status")).usage.reservedMicros, 0);
});
