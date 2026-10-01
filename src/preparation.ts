import { z } from "zod";
import { digest, Fault, requireValue, uid } from "./common.ts";
import { credentialRoots, seal, unseal } from "./crypto.ts";
import { publicationParts } from "./publications.ts";
import {
  critiqueSchema,
  draftsSchema,
  strategySchema,
  type Context,
  type Selection,
  type Evidence,
  type Strategy,
  type Drafts,
  type Critique,
} from "./preparation-contracts.ts";
import {
  MAX_INPUT_BYTES,
  MAX_OUTPUT_TOKENS,
  openAIModel,
  PREPARATION_MODEL,
  PREPARATION_EDITORIAL_VERSION,
  type ModelPort,
} from "./preparation-model.ts";
import type { Account, Actor, Campaign, Env, Project, Store } from "./types.ts";
import {
  modelCatalog,
  modelPrice,
  attemptCeiling,
  pipelineCeiling,
  verifyCloudflare,
  cloudflareModel,
  type Routing,
  type FundingProof,
} from "./model-routing.ts";

type Connection = {
  secret: string;
  revision: number;
  model: string;
  maxJobsPerDay: number;
  allowAgents: boolean;
  inputUsdPerMillion: number | null;
  outputUsdPerMillion: number | null;
  maxDailyUsd: number | null;
  verifiedAt?: number;
  routing?: Routing;
  fundingProofs?: FundingProof[];
};
type Stage = "source" | "interpret" | "draft" | "check";
type Channel = {
  alias: string;
  provider: Account["provider"];
  binding: number;
  identityId: string;
};
type Usage = {
  calls: number;
  jobs: number;
  reservedInputTokens: number;
  reservedOutputTokens: number;
  inputTokens: number;
  outputTokens: number;
  estimatedUsd: number | null;
  reservedMicros?: number;
  settledMicros?: number;
  uncertainMicros?: number;
};
export type PreparationJob = {
  id: string;
  project: string;
  revision: number;
  status:
    | "queued"
    | "running"
    | "review"
    | "edited"
    | "approved"
    | "handed_off"
    | "rejected"
    | "failed"
    | "uncertain";
  stage: Stage;
  createdAt: number;
  updatedAt: number;
  actor: Actor;
  modelRevision: number;
  channels: Channel[];
  selection: Selection;
  context: Context;
  sha?: string;
  evidence?: Evidence[];
  gaps?: string[];
  coverage?: string;
  strategy?: Strategy;
  drafts?: Drafts["drafts"];
  critique?: Critique;
  digest?: string;
  claim?: string;
  claimUntil?: number;
  regenerateAlias?: string;
  usage: {
    stage: string;
    editorialPolicyVersion?: string;
    executionRelease?: string;
    inputTokens: number;
    outputTokens: number;
    latencyMs: number;
    estimatedUsd: number | null;
  }[];
  reservationDay: string;
  error?: { code: string; message: string };
  approvedBy?: string;
  approvedAt?: number;
  campaign?: string;
  routing?: Routing;
  budget?: {
    day: string;
    generation: string;
    reservedMicros: number;
    chargedMicros: number;
    maxMicros: number;
  };
  attempts?: {
    stage: string;
    editorialPolicyVersion?: string;
    executionRelease?: string;
    provider: string;
    model: string;
    funding: string;
    configurationVersion: number;
    fallback: boolean;
    outcome: string;
    reservedMicros: number;
    estimatedMicros?: number;
    inputTokens?: number;
    outputTokens?: number;
    latencyMs?: number;
    fundingProof?: FundingProof;
  }[];
};
export interface PreparationOptions {
  now: () => number;
  wake: (at: number) => Promise<void>;
  authorized: (actor: Actor) => Promise<boolean>;
  evidence?: (selection: Selection) => Promise<{
    sha: string;
    evidence: Evidence[];
    gaps: string[];
    coverage: string;
  }>;
  model?: ModelPort;
  send?: typeof fetch;
  handoff: (input: {
    id: string;
    project: string;
    text: Record<string, string>;
    source: { profile: string; sha: string; family: string };
  }) => Promise<Campaign>;
}

export class Preparation {
  constructor(
    private store: Store,
    private env: Env,
    private options: PreparationOptions,
  ) {}
  private owner(actor: Actor) {
    requireValue(
      !actor.grant && actor.scopes.includes("admin"),
      "OWNER_MODEL_REVIEW_REQUIRED",
      "A signed-in workspace owner must manage model access and approve prepared content.",
      403,
    );
  }
  private connection(): Connection {
    const value = this.store.get<Connection>("model:openai");
    requireValue(
      value?.secret,
      "MODEL_NOT_CONNECTED",
      "Connect this workspace’s own OpenAI or Cloudflare model account first. PostSteward does not use a shared company key or a ChatGPT subscription.",
      409,
    );
    return value;
  }
  private day() {
    return new Date(this.options.now()).toISOString().slice(0, 10);
  }
  private usage(): Usage {
    return (
      this.store.get<Usage>("preparation-usage:" + this.day()) || {
        calls: 0,
        jobs: 0,
        reservedInputTokens: 0,
        reservedOutputTokens: 0,
        inputTokens: 0,
        outputTokens: 0,
        estimatedUsd: null,
      }
    );
  }
  private assertActor(actor: Actor, connection: Connection) {
    requireValue(
      !actor.grant || connection.allowAgents,
      "MODEL_AGENT_SPEND_DISABLED",
      "The owner has not allowed campaign-scoped agents to spend this workspace’s model allowance.",
      403,
    );
  }
  status() {
    const value = this.store.get<Connection>("model:openai");
    return {
      provider: value?.routing?.primary.provider || "openai",
      model: value?.routing?.primary.model || PREPARATION_MODEL,
      configured: Boolean(value?.secret),
      authentication: value?.verifiedAt
        ? "verified_by_successful_call"
        : "unverified",
      funding: value?.routing?.primary.funding || "workspace_model_account",
      configurationVersion: value?.revision || 0,
      routing: value?.routing || null,
      fundingProofs: value?.fundingProofs || [],
      catalogue: modelCatalog,
      inferenceStatus: value?.verifiedAt
        ? "ready_by_successful_call"
        : value?.routing
          ? "metadata_validated_inference_unverified"
          : "unverified",
      chatGPTSubscriptionIsAPIAccess: false,
      limits: value
        ? {
            maxJobsPerDay: value.maxJobsPerDay,
            allowAgents: value.allowAgents,
            maxDailyUsd: value.maxDailyUsd,
            inputUsdPerMillion: value.inputUsdPerMillion,
            outputUsdPerMillion: value.outputUsdPerMillion,
          }
        : null,
      usage: this.usage(),
      costNotice:
        "Your selected workspace-owned account funds inference. Cloudflare balances are shared account funds, not this workspace's exclusive credits. Credit purchases carry Cloudflare's fee and may overshoot under concurrency. Usage costs are estimates, not invoices. PostSteward GBP pricing is separate; no top-ups or service-funded fallback occur.",
    };
  }
  async connect(input: any, actor: Actor) {
    this.owner(actor);
    requireValue(
      input.routing ||
        input.maxDailyUsd === null ||
        (input.inputUsdPerMillion !== null &&
          input.outputUsdPerMillion !== null),
      "MODEL_RATES_REQUIRED",
      "A USD budget requires both current input and output rates from your provider account. Token/call caps always apply.",
    );
    const current = this.store.get<Connection>("model:openai");
    const expectedRevision = current?.revision || 0;
    requireValue(
      input.apiKey ||
        (current?.secret &&
          Boolean(input.routing) === Boolean(current.routing)),
      "MODEL_CREDENTIAL_REQUIRED",
      "Enter the credential when connecting or changing between direct OpenAI and Cloudflare. Existing encrypted credentials can be retained only within the same credential type.",
      409,
    );
    const retained =
      !input.apiKey && current
        ? await unseal<{ apiKey: string; inspectionToken?: string }>(
            current.secret,
            credentialRoots(this.env),
            actor.workspace + ":model:openai",
          )
        : undefined;
    const apiKey = input.apiKey || retained!.apiKey;
    const inspectionToken = input.inspectionToken || retained?.inspectionToken;
    const fundingProofs: FundingProof[] = [];
    if (input.routing)
      for (const route of [input.routing.primary, ...input.routing.fallbacks]) {
        fundingProofs.push(
          await verifyCloudflare(
            input.routing,
            route,
            inspectionToken || apiKey,
            this.options.now(),
            this.options.send,
          ),
        );
      }
    const secret = await seal(
      { apiKey, ...(inspectionToken ? { inspectionToken } : {}) },
      credentialRoots(this.env),
      actor.workspace + ":model:openai",
      this.env.ENCRYPTION_KEY_VERSION,
    );
    this.store.tx(() => {
      requireValue(
        (this.store.get<Connection>("model:openai")?.revision || 0) ===
          expectedRevision,
        "MODEL_CONNECTION_CHANGED",
        "Model connection changed; inspect settings before retrying.",
        409,
      );
      this.store.put("model:openai", {
        secret,
        revision: expectedRevision + 1,
        model: input.routing?.primary.model || PREPARATION_MODEL,
        ...(input.routing ? { routing: input.routing, fundingProofs } : {}),
        maxJobsPerDay: input.maxJobsPerDay,
        allowAgents: input.allowAgents,
        inputUsdPerMillion: input.inputUsdPerMillion,
        outputUsdPerMillion: input.outputUsdPerMillion,
        maxDailyUsd: input.routing?.maxDailyUsd ?? input.maxDailyUsd,
      });
    });
    return this.status();
  }
  disconnect(actor: Actor) {
    this.owner(actor);
    const current = this.store.get<Connection>("model:openai");
    if (current)
      this.store.put("model:openai", {
        ...current,
        secret: "",
        revision: current.revision + 1,
        verifiedAt: undefined,
      });
    for (const job of this.store.list<PreparationJob>("preparation:")) {
      if (["queued", "running"].includes(job.status))
        this.fail(
          job,
          "MODEL_DISCONNECTED",
          "Model access was disconnected. Completed drafts remain private; no retry or publication occurred.",
        );
    }
    return this.status();
  }
  public(job: PreparationJob) {
    const { actor, claim, claimUntil, ...value } = job;
    return value;
  }
  list() {
    return this.store
      .list<PreparationJob>("preparation:")
      .sort((a, b) => b.createdAt - a.createdAt)
      .slice(0, 100)
      .map((job) => this.public(job));
  }
  get(id: string) {
    const job = this.store.get<PreparationJob>("preparation:" + id);
    requireValue(
      job,
      "PREPARATION_NOT_FOUND",
      "No preparation exists in this workspace.",
      404,
    );
    return job;
  }
  async export(id: string) {
    const preparation = this.public(this.get(id));
    return {
      workspace: this.get(id).actor.workspace,
      preparation,
      reviewDigest: await digest(preparation),
      boundary:
        "Private workspace export. Removing a preparation never removes immutable campaigns, delivery receipts or spending history.",
    };
  }
  async archive(input: any, actor: Actor) {
    this.owner(actor);
    const exported = await this.export(input.id);
    requireValue(
      exported.preparation.revision === input.revision &&
        exported.reviewDigest === input.reviewDigest,
      "PREPARATION_REVIEW_CHANGED",
      "Refresh and export the exact preparation before removing it.",
      409,
    );
    this.store.tx(() => {
      const job = this.get(input.id);
      requireValue(
        JSON.stringify(this.public(job)) ===
          JSON.stringify(exported.preparation) &&
          job.revision === input.revision &&
          !["queued", "running", "approved"].includes(job.status),
        "PREPARATION_STILL_ACTIVE",
        "Reject running work or complete the immutable handoff before removing the preparation.",
        409,
      );
      this.store.delete("preparation:" + job.id);
    });
    return {
      removed: true,
      id: input.id,
      campaign: exported.preparation.campaign || null,
      retained:
        "Immutable campaigns, delivery receipts, spending history and existing operation history remain.",
    };
  }
  private assertMaterial(value: unknown) {
    requireValue(
      !/(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,})\b|Bearer\s+[A-Za-z0-9._-]{16,}|(?:access_token|api_key|client_secret)=)/i.test(
        JSON.stringify(value),
      ),
      "PREPARATION_SENSITIVE_CONTEXT",
      "Remove credentials and secret-bearing URLs from editorial material before model disclosure.",
      422,
    );
  }
  private channels(project: string): Channel[] {
    const selected = this.store.get<Project>("project:" + project);
    requireValue(
      selected && selected.accounts.length > 0 && selected.accounts.length <= 3,
      "PREPARATION_PROJECT_INVALID",
      "Choose a project with one to three connected destination accounts.",
    );
    return selected.accounts.map((alias) => {
      const account = this.store.get<Account>("account:" + alias);
      requireValue(
        account?.active,
        "PREPARATION_ACCOUNT_INACTIVE",
        "Reconnect the selected project accounts first.",
        409,
      );
      return {
        alias,
        provider: account.provider,
        binding: account.version,
        identityId: account.identity.id,
      };
    });
  }
  private reserve(connection: Connection, calls: number) {
    let reservedMicros = 0;
    this.store.tx(() => {
      const usage = this.usage();
      requireValue(
        usage.jobs < connection.maxJobsPerDay &&
          usage.calls + calls <= connection.maxJobsPerDay * 3,
        "PREPARATION_DAILY_LIMIT",
        "This workspace’s daily preparation allowance is exhausted. Inspect usage; do not bypass with another key.",
        429,
      );
      const input = MAX_INPUT_BYTES * calls,
        output = MAX_OUTPUT_TOKENS * calls;
      const worstUsd =
        connection.inputUsdPerMillion !== null &&
        connection.outputUsdPerMillion !== null
          ? ((usage.reservedInputTokens + input) *
              connection.inputUsdPerMillion +
              (usage.reservedOutputTokens + output) *
                connection.outputUsdPerMillion) /
            1e6
          : null;
      if (connection.routing) {
        reservedMicros = pipelineCeiling(connection.routing, calls);
        requireValue(
          reservedMicros <= Math.floor(connection.routing.maxJobUsd * 1e6),
          "PREPARATION_JOB_COST_LIMIT",
          "The full preparation including every allowed fallback exceeds the per-job budget. Reduce output/context/fallbacks or explicitly change owner limits.",
          429,
        );
        requireValue(
          (usage.reservedMicros || 0) +
            (usage.settledMicros || 0) +
            (usage.uncertainMicros || 0) +
            reservedMicros <=
            Math.floor(connection.routing.maxDailyUsd * 1e6),
          "PREPARATION_COST_LIMIT",
          "Reserved and charged usage would exceed today's workspace budget.",
          429,
        );
        usage.reservedMicros = (usage.reservedMicros || 0) + reservedMicros;
      }
      requireValue(
        connection.routing ||
          connection.maxDailyUsd === null ||
          (worstUsd !== null && worstUsd <= connection.maxDailyUsd),
        "PREPARATION_COST_LIMIT",
        "Worst-case reserved usage would exceed this workspace’s owner-set model budget.",
        429,
      );
      usage.jobs++;
      usage.calls += calls;
      usage.reservedInputTokens += input;
      usage.reservedOutputTokens += output;
      this.store.put("preparation-usage:" + this.day(), usage);
    });
    return reservedMicros;
  }
  async create(
    input: { project: string; selection: Selection; context: Context },
    actor: Actor,
  ) {
    const connection = this.connection();
    this.assertActor(actor, connection);
    this.assertMaterial(input.context);
    requireValue(
      (!input.selection.allowPrivate && !input.selection.allowUnreleased) ||
        !actor.grant,
      "OWNER_DISCLOSURE_REQUIRED",
      "Only the signed-in owner can approve private or unreleased disclosure.",
      403,
    );
    requireValue(
      this.store.list("preparation:").length < 100,
      "PREPARATION_STORAGE_LIMIT",
      "This workspace has 100 retained preparations; export and remove completed or rejected work before creating more.",
      429,
    );
    const channels = this.channels(input.project);
    const reservedMicros = this.reserve(connection, 3);
    const job: PreparationJob = {
      id: uid(),
      project: input.project,
      revision: 1,
      status: "queued",
      stage: "source",
      createdAt: this.options.now(),
      updatedAt: this.options.now(),
      actor,
      modelRevision: connection.revision,
      channels,
      selection: input.selection,
      context: input.context,
      usage: [],
      reservationDay: this.day(),
      ...(connection.routing
        ? {
            routing: structuredClone(connection.routing),
            budget: {
              day: this.day(),
              generation: uid(),
              reservedMicros,
              chargedMicros: 0,
              maxMicros: Math.floor(connection.routing.maxJobUsd * 1e6),
            },
            attempts: [],
          }
        : {}),
    };
    this.store.put("preparation:" + job.id, job);
    await this.options.wake(this.options.now() + 1);
    return this.public(job);
  }
  private save(job: PreparationJob) {
    job.updatedAt = this.options.now();
    this.store.put("preparation:" + job.id, job);
  }
  private releaseBudget(job: PreparationJob) {
    if (!job.budget) return;
    this.store.tx(() => {
      const current = this.store.get<PreparationJob>("preparation:" + job.id);
      const budget = current?.budget || job.budget!;
      const usage = this.store.get<Usage>("preparation-usage:" + budget.day);
      if (usage) {
        usage.reservedMicros = Math.max(
          0,
          (usage.reservedMicros || 0) - budget.reservedMicros,
        );
        this.store.put("preparation-usage:" + budget.day, usage);
      }
      job.budget = { ...budget, reservedMicros: 0 };
      if (current)
        this.store.put("preparation:" + job.id, {
          ...current,
          budget: job.budget,
        });
    });
  }
  private beginAttempt(
    job: PreparationJob,
    index: number,
    proof: FundingProof,
  ) {
    const routing = job.routing!,
      route = [routing.primary, ...routing.fallbacks][index];
    const amount = attemptCeiling(routing, route),
      id = uid();
    this.store.tx(() => {
      const current = this.get(job.id);
      requireValue(
        current.status === "running" &&
          (current.claimUntil || 0) > this.options.now() &&
          current.claim === job.claim &&
          current.revision === job.revision &&
          this.connection().revision === job.modelRevision,
        "MODEL_AUTHORITY_CHANGED",
        "Inference authority changed before the attempt.",
        409,
      );
      const budget = current.budget!;
      requireValue(
        budget.reservedMicros >= amount &&
          budget.chargedMicros + amount <= budget.maxMicros,
        "PREPARATION_JOB_COST_LIMIT",
        "The job's remaining reserved allowance cannot cover this attempt.",
        429,
      );
      const usage = this.store.get<Usage>("preparation-usage:" + budget.day)!;
      requireValue(
        (usage.reservedMicros || 0) +
          (usage.settledMicros || 0) +
          (usage.uncertainMicros || 0) <=
          Math.floor(this.connection().routing!.maxDailyUsd * 1e6),
        "PREPARATION_COST_LIMIT",
        "The current periodic spending policy blocks this attempt.",
        429,
      );
      budget.reservedMicros -= amount;
      budget.chargedMicros += amount;
      usage.reservedMicros = (usage.reservedMicros || 0) - amount;
      usage.uncertainMicros = (usage.uncertainMicros || 0) + amount;
      const price = modelPrice(route);
      const attempt = {
        id,
        day: budget.day,
        generation: budget.generation,
        job: job.id,
        stage: job.stage,
        editorialPolicyVersion: PREPARATION_EDITORIAL_VERSION,
        executionRelease: this.env.RELEASE_SHA,
        provider: route.provider,
        model: route.model,
        funding: route.funding,
        configurationVersion: job.modelRevision,
        fallback: index > 0,
        outcome: "in_flight",
        reservedMicros: amount,
        fundingProof: proof,
        price: {
          input: price.input,
          output: price.output,
          factor: route.funding === "gateway_credits" ? 1.05 : 1,
          maxInput: Math.min(routing.maxInputBytes, price.contextBytes),
          maxOutput: routing.maxOutputTokens,
        },
      };
      current.attempts = [...(current.attempts || []), attempt];
      requireValue(
        current.attempts.length <= 512,
        "PREPARATION_HISTORY_LIMIT",
        "Export/archive this preparation and start a new one before more attempts.",
        429,
      );
      this.store.put("model-attempt:" + id, attempt);
      this.store.put("preparation-usage:" + budget.day, usage);
      this.store.put("preparation:" + job.id, current);
      job.budget = budget;
      job.attempts = current.attempts;
    });
    return id;
  }
  private settleAttempt(
    id: string,
    result?: { inputTokens: number; outputTokens: number; latencyMs: number },
    outcome = "reported_usage",
  ) {
    this.store.tx(() => {
      const attempt = this.store.get<any>("model-attempt:" + id);
      if (!attempt || attempt.outcome !== "in_flight") return;
      const current = this.store.get<PreparationJob>(
        "preparation:" + attempt.job,
      );
      let charged = attempt.reservedMicros;
      if (result) {
        const p = attempt.price;
        requireValue(
          Number.isSafeInteger(result.inputTokens) &&
            result.inputTokens >= 0 &&
            result.inputTokens <= p.maxInput &&
            Number.isSafeInteger(result.outputTokens) &&
            result.outputTokens >= 0 &&
            result.outputTokens <= p.maxOutput,
          "MODEL_USAGE_INVALID",
          "Reported usage exceeded its reserved boundary.",
          502,
        );
        charged = Math.ceil(
          (result.inputTokens * p.input + result.outputTokens * p.output) *
            p.factor,
        );
        requireValue(
          charged <= attempt.reservedMicros,
          "MODEL_USAGE_INVALID",
          "Reported cost exceeded the reservation.",
          502,
        );
      }
      const usage = this.store.get<Usage>("preparation-usage:" + attempt.day)!;
      if (result) {
        usage.uncertainMicros = Math.max(
          0,
          (usage.uncertainMicros || 0) - attempt.reservedMicros,
        );
        usage.settledMicros = (usage.settledMicros || 0) + charged;
      }
      const updated = {
        ...attempt,
        outcome,
        ...(result ? { ...result, estimatedMicros: charged } : {}),
      };
      this.store.put("model-attempt:" + id, updated);
      this.store.put("preparation-usage:" + attempt.day, usage);
      if (current) {
        if (current.budget && current.budget.generation === attempt.generation)
          current.budget.chargedMicros = Math.max(
            0,
            current.budget.chargedMicros - (attempt.reservedMicros - charged),
          );
        current.attempts = (current.attempts || []).map((a: any) =>
          a.id === id ? updated : a,
        );
        this.store.put("preparation:" + current.id, current);
      }
    });
  }
  private fail(job: PreparationJob, code: string, message: string) {
    this.releaseBudget(job);
    job.status = code.includes("UNCERTAIN") ? "uncertain" : "failed";
    job.error = { code, message };
    job.claim = undefined;
    job.claimUntil = undefined;
    this.save(job);
  }
  private assertReferences(value: Strategy | Drafts, evidence: Evidence[]) {
    const claims =
      "changes" in value
        ? value.changes
        : value.drafts.flatMap((draft) => draft.claims);
    for (const claim of claims)
      for (const source of claim.sources) {
        const item = evidence.find((item) => item.id === source.evidence);
        requireValue(
          item && item.text.includes(source.quote),
          "PREPARATION_UNSUPPORTED_REFERENCE",
          "A generated evidence quote is missing from the pinned source. Inspect the release; no approval is available.",
          422,
        );
      }
  }
  private assertDrafts(drafts: Drafts["drafts"], channels: Channel[]) {
    requireValue(
      new Set(drafts.map((d) => d.alias)).size === drafts.length &&
        drafts.every((d) => channels.some((c) => c.alias === d.alias)),
      "PREPARATION_DESTINATION_INVALID",
      "The model returned an unselected or duplicate destination.",
      422,
    );
    for (const draft of drafts) {
      requireValue(
        draft.text === draft.text.replaceAll("\r\n", "\n").trim(),
        "PREPARATION_TEXT_NOT_CANONICAL",
        "Remove leading/trailing whitespace and use LF line breaks before checking or approval. Copy cannot change after review.",
        422,
      );
      requireValue(
        !/(?:-----BEGIN|\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{16,})/.test(
          draft.text,
        ),
        "PREPARATION_SENSITIVE_OUTPUT",
        "Sensitive-looking model output was excluded from the campaign.",
        422,
      );
      publicationParts(
        channels.find((c) => c.alias === draft.alias)!.provider,
        draft.text,
      );
    }
  }
  private async finalDigest(job: PreparationJob) {
    return digest({
      revision: job.revision,
      project: job.project,
      selection: job.selection,
      context: job.context,
      sha: job.sha,
      evidence: job.evidence,
      strategy: job.strategy,
      drafts: job.drafts,
      channels: job.channels,
    });
  }
  async tick() {
    for (const current of this.store
      .list<PreparationJob>("preparation:")
      .filter((j) => j.status === "running")) {
      if ((current.claimUntil || 0) <= this.options.now())
        this.fail(
          current,
          "MODEL_CALL_UNCERTAIN",
          "Preparation was interrupted in flight. The provider may have billed it; no automatic retry occurred. Inspect your provider usage before regenerating.",
        );
    }
    const job = this.store
      .list<PreparationJob>("preparation:")
      .find((j) => j.status === "queued");
    if (!job) return;
    try {
      const connection = this.connection();
      requireValue(
        connection.revision === job.modelRevision,
        "MODEL_AUTHORITY_CHANGED",
        "Model access or limits changed. Explicit regeneration is required.",
        409,
      );
      this.assertActor(job.actor, connection);
      requireValue(
        await this.options.authorized(job.actor),
        "PREPARATION_AUTHORITY_EXPIRED",
        "The original preparation authority expired or was revoked.",
        403,
      );
      const latest = this.store.get<PreparationJob>("preparation:" + job.id);
      if (
        !latest ||
        latest.status !== "queued" ||
        latest.revision !== job.revision
      )
        return;
      if (job.reservationDay !== this.day()) {
        const phases =
          job.stage === "source" || job.stage === "interpret"
            ? 3
            : job.stage === "draft"
              ? 2
              : 1;
        if (job.budget && connection.routing)
          requireValue(
            job.budget.chargedMicros +
              pipelineCeiling(connection.routing, phases) <=
              job.budget.maxMicros,
            "PREPARATION_JOB_COST_LIMIT",
            "The full job's expenditure ceiling remains in force across midnight.",
            429,
          );
        this.releaseBudget(job);
        const reserved = this.reserve(
          connection,
          job.stage === "source" || job.stage === "interpret"
            ? 3
            : job.stage === "draft"
              ? 2
              : 1,
        );
        job.reservationDay = this.day();
        if (job.budget) {
          requireValue(
            job.budget.chargedMicros + reserved <= job.budget.maxMicros,
            "PREPARATION_JOB_COST_LIMIT",
            "The full job's expenditure ceiling remains in force across midnight.",
            429,
          );
          job.budget.day = this.day();
          job.budget.reservedMicros = reserved;
        }
      }
      // Claim persisted before any external I/O. The watchdog never resends an uncertain call.
      const claim = uid();
      job.status = "running";
      job.claim = claim;
      job.claimUntil =
        this.options.now() +
        (job.stage === "source" ? 120000 : connection.routing ? 360000 : 60000);
      this.save(job);
      const remainsCurrent = async () => {
        const current = this.store.get<PreparationJob>("preparation:" + job.id);
        return (
          !!current &&
          current.claim === claim &&
          current.status === "running" &&
          (current.claimUntil || 0) > this.options.now() &&
          current.revision === job.revision &&
          this.store.get<Connection>("model:openai")?.revision ===
            connection.revision &&
          (await this.options.authorized(job.actor))
        );
      };
      if (job.stage === "source") {
        requireValue(
          this.options.evidence,
          "PREPARATION_SOURCE_UNCONFIGURED",
          "The release evidence reader is unavailable.",
          503,
        );
        const result = await this.options.evidence(job.selection);
        if (!(await remainsCurrent())) return;
        job.sha = result.sha;
        job.evidence = result.evidence;
        job.gaps = result.gaps;
        job.coverage = result.coverage;
        job.evidence.push({
          id: "context",
          kind: "approved_context",
          text: JSON.stringify(job.context),
          url: this.env.PUBLIC_ORIGIN + "/app",
        });
        job.stage = "interpret";
        job.status = "queued";
      } else {
        const stage = job.stage;
        const schema: z.ZodType =
          stage === "interpret"
            ? strategySchema
            : stage === "draft"
              ? draftsSchema
              : critiqueSchema;
        const key = await unseal<{ apiKey: string; inspectionToken?: string }>(
          connection.secret,
          credentialRoots(this.env),
          job.actor.workspace + ":model:openai",
        );
        if (!(await remainsCurrent())) return;
        const channels =
          job.regenerateAlias && stage === "draft"
            ? job.channels.filter((c) => c.alias === job.regenerateAlias)
            : job.channels;
        const material = {
          sourceScope: {
            mode: job.selection.previousTag
              ? "bounded_release_comparison"
              : "selected_release_snapshot",
            repository: job.selection.repository,
            releaseTag: job.selection.releaseTag,
            pinnedCommit: job.sha,
            previousTag: job.selection.previousTag || null,
          },
          context: job.context,
          evidence: job.evidence,
          coverage: job.coverage,
          gaps: job.gaps,
          channels: channels.map(({ alias, provider }) => ({
            alias,
            provider,
          })),
          ...(stage === "draft" ? { strategy: job.strategy } : {}),
          ...(stage === "check"
            ? {
                reviewTarget: "current_saved_channel_text",
                reviewIntent: {
                  audience: job.strategy?.audience,
                  objective: job.strategy?.objective,
                },
                // Generated rationale/claims can refer to copy the owner removed.
                // Check every assertion in current text against pinned evidence.
                drafts: job.drafts?.map(({ alias, text }) => ({ alias, text })),
              }
            : {}),
        };
        let selectedRoute = connection.routing?.primary;
        let result;
        if (connection.routing) {
          const routes = [
            connection.routing.primary,
            ...connection.routing.fallbacks,
          ];
          for (let index = 0; index < routes.length; index++) {
            selectedRoute = routes[index];
            // Re-read model/account/gateway/key precedence immediately before EVERY
            // attempted route. Metadata failure never triggers a paid fallback.
            const proof = await verifyCloudflare(
              connection.routing,
              selectedRoute,
              key.inspectionToken || key.apiKey,
              this.options.now(),
              this.options.send,
            );
            if (!(await remainsCurrent())) return;
            this.assertActor(job.actor, this.connection());
            const attempt = this.beginAttempt(job, index, proof);
            try {
              result = await (
                this.options.model ||
                cloudflareModel(
                  connection.routing,
                  selectedRoute,
                  this.options.send,
                )
              )(key.apiKey, stage, material, schema);
              this.settleAttempt(attempt, result);
              break;
            } catch (error) {
              this.settleAttempt(
                attempt,
                undefined,
                error instanceof Fault ? error.code : "MODEL_CALL_UNCERTAIN",
              );
              // A rejection can still cost money: retain its full reservation.
              // No uncertain/credential/policy errors cause another attempt.
              if (
                !(error instanceof Fault) ||
                ![
                  "MODEL_UNAVAILABLE",
                  "MODEL_RATE_LIMITED",
                  "MODEL_OUTPUT_INVALID",
                ].includes(error.code) ||
                index === routes.length - 1
              )
                throw error;
              if (!(await remainsCurrent())) return;
            }
          }
          if (!(await remainsCurrent())) return;
          const persisted = this.get(job.id);
          job.budget = persisted.budget;
          job.attempts = persisted.attempts;
        } else
          result = await (this.options.model || openAIModel())(
            key.apiKey,
            stage,
            material,
            schema,
          );
        requireValue(
          result,
          "MODEL_UNAVAILABLE",
          "No permitted model route remains.",
          409,
        );
        const parsed = schema.safeParse(result.value);
        requireValue(
          parsed.success,
          "MODEL_OUTPUT_INVALID",
          "The model returned an invalid structured editorial result.",
          422,
        );
        const estimate =
          connection.routing && selectedRoute
            ? ((result.inputTokens * modelPrice(selectedRoute).input +
                result.outputTokens * modelPrice(selectedRoute).output) *
                (selectedRoute.funding === "gateway_credits" ? 1.05 : 1)) /
              1e6
            : connection.inputUsdPerMillion !== null &&
                connection.outputUsdPerMillion !== null
              ? (result.inputTokens * connection.inputUsdPerMillion +
                  result.outputTokens * connection.outputUsdPerMillion) /
                1e6
              : null;
        const usage = this.usage();
        usage.inputTokens += result.inputTokens;
        usage.outputTokens += result.outputTokens;
        usage.estimatedUsd =
          estimate === null ? null : (usage.estimatedUsd || 0) + estimate;
        this.store.put("preparation-usage:" + this.day(), usage);
        if (!(await remainsCurrent())) return;
        job.usage.push({
          stage,
          editorialPolicyVersion: PREPARATION_EDITORIAL_VERSION,
          executionRelease: this.env.RELEASE_SHA,
          inputTokens: result.inputTokens,
          outputTokens: result.outputTokens,
          latencyMs: result.latencyMs,
          estimatedUsd: estimate,
        });
        this.save(job);
        this.store.put("model:openai", {
          ...connection,
          verifiedAt: this.options.now(),
        });
        if (stage === "interpret") {
          const strategy = parsed.data as Strategy;
          this.assertReferences(strategy, job.evidence!);
          job.strategy = strategy;
          if (strategy.missingContext.length || !strategy.changes.length) {
            job.status = "review";
            job.error = {
              code: "PREPARATION_CONTEXT_REQUIRED",
              message:
                "The model identified insufficient context. Review the missing information; drafts and approval are unavailable.",
            };
          } else {
            job.stage = "draft";
            job.status = "queued";
          }
        } else if (stage === "draft") {
          const drafts = parsed.data as Drafts;
          this.assertReferences(drafts, job.evidence!);
          this.assertDrafts(drafts.drafts, channels);
          requireValue(
            drafts.drafts.length === channels.length,
            "PREPARATION_DESTINATION_MISSING",
            "The model omitted a selected channel.",
            422,
          );
          job.drafts = job.regenerateAlias
            ? [
                ...(job.drafts || []).filter(
                  (d) => d.alias !== job.regenerateAlias,
                ),
                ...drafts.drafts,
              ]
            : drafts.drafts;
          job.regenerateAlias = undefined;
          job.stage = "check";
          job.status = "queued";
        } else {
          job.critique = parsed.data as Critique;
          job.status = "review";
          job.digest = await this.finalDigest(job);
        }
      }
      job.claim = undefined;
      job.claimUntil = undefined;
      if (job.status !== "queued") this.releaseBudget(job);
      this.save(job);
      if (job.status === "queued")
        await this.options.wake(this.options.now() + 1);
    } catch (error) {
      // Do not overwrite a concurrent disconnect/reject/edit or resurrect an old job.
      const current = this.store.get<PreparationJob>("preparation:" + job.id);
      if (
        !current ||
        current.revision !== job.revision ||
        !["queued", "running"].includes(current.status)
      )
        return;
      const known =
        error instanceof Fault
          ? error
          : new Fault(
              "PREPARATION_FAILED",
              "Preparation could not complete. No template fallback or publication occurred.",
              502,
            );
      this.fail(current, known.code, known.message);
    }
  }
  async scheduleNext() {
    const jobs = this.store.list<PreparationJob>("preparation:");
    if (jobs.some((j) => j.status === "queued"))
      await this.options.wake(this.options.now() + 1000);
    else {
      const running = jobs
        .filter((j) => j.status === "running")
        .map((j) => j.claimUntil || this.options.now() + 60000);
      if (running.length) await this.options.wake(Math.min(...running));
    }
  }
  async edit(input: any, actor: Actor) {
    const job = this.get(input.id);
    requireValue(
      ["review", "edited", "rejected", "failed", "uncertain"].includes(
        job.status,
      ) && job.revision === input.revision,
      "PREPARATION_REVIEW_CHANGED",
      "Preparation changed or is still running; refresh before editing.",
      409,
    );
    requireValue(
      job.drafts,
      "PREPARATION_DRAFTS_MISSING",
      "This preparation has no drafts to edit.",
      409,
    );
    const drafts = job.drafts
      .filter((d) => Object.hasOwn(input.text, d.alias))
      .map((d) => ({ ...d, text: input.text[d.alias] }));
    requireValue(
      drafts.length > 0 && drafts.length === Object.keys(input.text).length,
      "PREPARATION_DESTINATION_INVALID",
      "Edit only existing selected variants.",
    );
    this.assertMaterial({ text: input.text, strategy: input.strategy });
    this.assertDrafts(drafts, job.channels);
    job.drafts = drafts;
    if (input.strategy) {
      this.assertReferences(input.strategy, job.evidence!);
      job.strategy = input.strategy;
    }
    job.revision++;
    job.status = "edited";
    job.critique = undefined;
    job.digest = await this.finalDigest(job);
    job.error = undefined;
    this.store.tx(() => {
      requireValue(
        this.store.get<PreparationJob>("preparation:" + job.id)?.revision ===
          input.revision,
        "PREPARATION_REVIEW_CHANGED",
        "Preparation changed while saving; refresh before editing.",
        409,
      );
      this.save(job);
    });
    return this.public(job);
  }
  async regenerate(input: any, actor: Actor) {
    const job = this.get(input.id);
    requireValue(
      !["running", "queued", "approved", "handed_off"].includes(job.status) &&
        job.revision === input.revision,
      "PREPARATION_REVIEW_CHANGED",
      "Refresh the preparation before regeneration.",
      409,
    );
    const connection = this.connection();
    this.assertActor(actor, connection);
    requireValue(
      (!job.selection.allowPrivate && !job.selection.allowUnreleased) ||
        !actor.grant,
      "OWNER_DISCLOSURE_REQUIRED",
      "Only the owner can regenerate disclosed private/unreleased evidence.",
      403,
    );
    if (input.stage === "check")
      requireValue(
        job.drafts && job.strategy,
        "PREPARATION_DRAFTS_MISSING",
        "There is no complete draft to check.",
      );
    if (input.stage === "draft")
      requireValue(
        job.strategy && !job.strategy.missingContext.length,
        "PREPARATION_CONTEXT_REQUIRED",
        "Resolve missing context before drafting.",
      );
    if (input.alias)
      requireValue(
        input.stage === "draft" &&
          job.channels.some((c) => c.alias === input.alias),
        "PREPARATION_DESTINATION_INVALID",
        "Choose a selected draft channel.",
      );
    this.assertMaterial({
      context: input.context,
      strategy: job.strategy,
      drafts: job.drafts,
    });
    this.releaseBudget(job);
    const reservedMicros = this.reserve(
      connection,
      input.stage === "interpret" ? 3 : input.stage === "draft" ? 2 : 1,
    );
    job.actor = actor;
    job.modelRevision = connection.revision;
    job.routing = connection.routing
      ? structuredClone(connection.routing)
      : undefined;
    job.budget = connection.routing
      ? {
          day: this.day(),
          generation: uid(),
          reservedMicros,
          chargedMicros: 0,
          maxMicros: Math.floor(connection.routing.maxJobUsd * 1e6),
        }
      : undefined;
    job.revision++;
    job.stage =
      input.stage === "interpret" && !job.evidence?.length
        ? "source"
        : input.stage;
    job.status = "queued";
    job.reservationDay = this.day();
    job.regenerateAlias = input.alias || undefined;
    job.critique = undefined;
    job.digest = undefined;
    job.error = undefined;
    if (input.stage === "interpret") {
      job.strategy = undefined;
      job.drafts = undefined;
    }
    if (input.context) {
      job.context = input.context;
      job.evidence = job.evidence?.filter((e) => e.id !== "context");
      job.evidence?.push({
        id: "context",
        kind: "approved_context",
        text: JSON.stringify(input.context),
        url: this.env.PUBLIC_ORIGIN + "/app",
      });
    }
    this.save(job);
    await this.options.wake(this.options.now() + 1);
    return this.public(job);
  }
  reject(input: any) {
    const job = this.get(input.id);
    requireValue(
      job.revision === input.revision &&
        !["approved", "handed_off"].includes(job.status),
      "PREPARATION_REVIEW_CHANGED",
      "Refresh before rejecting this preparation.",
      409,
    );
    this.releaseBudget(job);
    job.status = "rejected";
    job.revision++;
    job.claim = undefined;
    job.claimUntil = undefined;
    this.save(job);
    return this.public(job);
  }
  async approve(input: any, actor: Actor) {
    this.owner(actor);
    const job = this.get(input.id);
    requireValue(
      job.revision === input.revision &&
        job.digest === input.digest &&
        ["review", "approved", "handed_off"].includes(job.status),
      "PREPARATION_REVIEW_CHANGED",
      "Exact owner approval requires the current reviewed revision and digest.",
      409,
    );
    requireValue(
      job.critique?.acceptableForOwnerReview &&
        !job.critique.issues.length &&
        job.strategy &&
        !job.strategy.missingContext.length &&
        job.drafts?.length,
      "PREPARATION_CHECK_REQUIRED",
      "Resolve editorial issues and run checking before owner approval.",
      409,
    );
    const channels = this.channels(job.project);
    requireValue(
      JSON.stringify(channels) === JSON.stringify(job.channels),
      "PREPARATION_ACCOUNT_DRIFT",
      "Account/project bindings changed; prepare and review current destinations.",
      409,
    );
    this.assertDrafts(job.drafts!, job.channels);
    if (job.campaign) return this.public(job);
    job.status = "approved";
    job.approvedBy = actor.id;
    job.approvedAt = this.options.now();
    this.save(job);
    const campaign = await this.options.handoff({
      id: await digest({
        preparation: job.id,
        revision: job.revision,
        digest: job.digest,
      }),
      project: job.project,
      text: Object.fromEntries(job.drafts!.map((d) => [d.alias, d.text])),
      source: {
        profile: "preparation-" + job.id,
        sha: job.sha!,
        family: "owner-reviewed-release",
      },
    });
    job.campaign = campaign.id;
    job.status = "handed_off";
    this.save(job);
    return this.public(job);
  }
}
