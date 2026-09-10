import { byName, plans } from "./operations/catalog.ts";
import { guardControlledPublication } from "./controlled.ts";
import {
  active,
  digest,
  explicitTime,
  Fault,
  requireValue,
  uid,
  validZone,
} from "./common.ts";
import { seal, unseal } from "./crypto.ts";
import {
  validateText,
  type Credential,
  type ProviderAPI,
} from "./providers.ts";
import type {
  Account,
  Actor,
  Campaign,
  Delivery,
  Entitlement,
  Env,
  Profile,
  Project,
  Store,
} from "./types.ts";
export interface BillingPort {
  status(): Promise<unknown>;
  quote(input: any, actor: Actor): Promise<unknown>;
  checkout(input: any, actor: Actor): Promise<unknown>;
  portal(input: any, actor: Actor): Promise<unknown>;
}
export interface EngineOptions {
  now?: () => number;
  wake: (at: number) => Promise<void>;
  authorized: (actor: Actor) => Promise<boolean>;
  source: (profile: Profile) => Promise<{ sha: string }>;
  billing: BillingPort;
}
type Handler = (input: any, actor: Actor) => unknown | Promise<unknown>;
export class Engine {
  readonly handlers: Record<string, Handler>;
  private now: () => number;
  constructor(
    readonly store: Store,
    private env: Env,
    private providers: ProviderAPI,
    private options: EngineOptions,
  ) {
    this.now = options.now || Date.now;
    this.handlers = {
      workspace_status: (_input, actor) => ({
        workspace: actor.workspace,
        release: env.RELEASE_SHA,
        plan: this.paid() ? "advanced" : "free",
        entitlement: this.store.get("entitlement") || null,
        publishingPaused: this.paused(),
        limits: {
          requestsPerMinute: Number(env.WORKSPACE_REQUEST_LIMIT),
          dailyDeliveryAttempts: Number(env.DAILY_DELIVERY_LIMIT),
          activeSchedules: Number(env.ACTIVE_SCHEDULE_LIMIT),
        },
        prices: plans,
      }),
      accounts_list: () =>
        this.store.list<Account>("account:").map(({ secret, ...a }) => a),
      account_disconnect: (i) =>
        this.store.tx(() => {
          const a = this.get<Account>("account:", i.alias);
          a.active = false;
          a.version++;
          this.store.put("account:" + a.alias, a);
          for (const d of this.deliveries().filter(
            (d) =>
              d.account === a.alias &&
              ["scheduled", "waiting_container"].includes(d.status),
          ))
            this.update(d, {
              status: "drift_blocked",
              reason: "Account disconnected.",
            });
          return { alias: a.alias, active: false };
        }),
      project_put: (i) => {
        for (const alias of i.accounts) this.connected(alias);
        const p: Project = {
          id: i.id,
          name: i.name,
          accounts: [...new Set<string>(i.accounts)],
        };
        this.store.put("project:" + p.id, p);
        return p;
      },
      projects_list: () => this.store.list<Project>("project:"),
      campaign_create: async (i) => {
        const p = this.get<Project>("project:", i.project);
        for (const alias of Object.keys(i.text)) {
          requireValue(
            p.accounts.includes(alias),
            "ACCOUNT_NOT_BOUND",
            "Campaign account is not bound to this project.",
          );
          validateText(this.connected(alias).provider, i.text[alias]);
        }
        requireValue(
          Object.keys(i.text).length > 0,
          "EMPTY_CAMPAIGN",
          "Provide at least one destination.",
        );
        const c: Campaign = {
          id: uid(),
          project: p.id,
          text: i.text,
          digest: await digest(i.text),
          createdAt: this.now(),
        };
        this.store.put("campaign:" + c.id, c);
        return c;
      },
      campaign_get: (i) => this.get<Campaign>("campaign:", i.campaign),
      campaign_validate: (i) =>
        this.validate(this.get<Campaign>("campaign:", i.campaign)),
      publish_now: async (i, a) =>
        this.reserve(i.campaign, this.now(), "UTC", a),
      schedule_create: async (i, a) => {
        const at = explicitTime(i.at);
        requireValue(
          at > this.now(),
          "SCHEDULE_IN_PAST",
          "Choose a future time.",
        );
        validZone(i.timezone);
        return this.reserve(i.campaign, at, i.timezone, a);
      },
      schedule_cancel: (i) =>
        this.store.tx(() => {
          const d = this.get<Delivery>("delivery:", i.delivery);
          if (
            ["executing", "waiting_container"].includes(d.status) &&
            d.phase === "publish"
          )
            return {
              cancelled: false,
              delivery: this.publicDelivery(d),
              reason: "already_executing",
            };
          if (["scheduled", "waiting_container"].includes(d.status)) {
            this.update(d, {
              status: "cancelled",
              reason: "Cancelled by authorised actor.",
            });
            return { cancelled: true, delivery: this.publicDelivery(d) };
          }
          return {
            cancelled: d.status === "cancelled",
            delivery: this.publicDelivery(d),
            reason:
              d.status === "executing"
                ? "already_executing"
                : "already_terminal",
          };
        }),
      schedule_replace: async (i, a) => {
        const at = explicitTime(i.at);
        requireValue(
          at > this.now(),
          "SCHEDULE_IN_PAST",
          "Choose a future time.",
        );
        validZone(i.timezone);
        const c = this.get<Campaign>("campaign:", i.campaign);
        const original = this.get<Delivery>("delivery:", i.delivery);
        requireValue(
          c.project === original.project && c.text[original.account],
          "REPLACEMENT_ROUTING_MISMATCH",
          "Replacement must include the same project and account.",
        );
        const prepared = await this.prepare(
          c,
          at,
          i.timezone,
          a,
          false,
          undefined,
          [original.account],
        );
        const result = this.store.tx(() => {
          const d = this.get<Delivery>("delivery:", i.delivery);
          requireValue(
            ["scheduled", "waiting_container"].includes(d.status),
            "ALREADY_EXECUTING",
            "This delivery can no longer be replaced.",
            409,
          );
          this.update(d, {
            status: "cancelled",
            reason: "Replaced by reviewed campaign " + c.id,
          });
          return this.reservePrepared(prepared);
        });
        await this.options.wake(at);
        return result;
      },
      receipt_get: (i) =>
        this.publicDelivery(this.get<Delivery>("delivery:", i.delivery)),
      receipts_list: (i) =>
        this.deliveries()
          .filter((d) => !i.before || d.createdAt < i.before)
          .sort((a, b) => b.createdAt - a.createdAt)
          .slice(0, i.limit)
          .map((d) => this.publicDelivery(d)),
      workspace_export: () => ({
        projects: this.store.list("project:"),
        campaigns: this.store.list("campaign:"),
        receipts: this.deliveries().map((d) => this.publicDelivery(d)),
      }),
      metrics_capture: async (i) => {
        const d = this.get<Delivery>("delivery:", i.delivery);
        const a = this.connected(d.account);
        requireValue(
          a.identity.id === d.identity.id,
          "ACCOUNT_DRIFT",
          "Account changed since publication.",
          409,
        );
        const metrics = await this.providers.metrics(
          d,
          await this.credential(a),
        );
        this.update(d, { metrics });
        return metrics;
      },
      publishing_pause: (i) => {
        this.store.put("paused", i.paused);
        return { paused: i.paused };
      },
      automation_configure: (i, a) => {
        this.get<Project>("project:", i.project);
        requireValue(
          this.store.get("profile:" + i.id) ||
            this.store.list("profile:").length < 10,
          "PROFILE_LIMIT",
          "The initial pilot supports 10 reviewed source profiles per workspace.",
          429,
        );
        requireValue(
          !this.store.get<Profile>("profile:" + i.id)?.enabled,
          "PAUSE_REQUIRED",
          "Pause this profile before changing its authority.",
          409,
        );
        const p: Profile = {
          id: i.id,
          revision:
            (this.store.get<Profile>("profile:" + i.id)?.revision || 0) + 1,
          project: i.project,
          repository: i.repository,
          branch: i.branch,
          path: i.path,
          templates: i.templates,
          family: i.family,
          intervalMinutes: i.intervalMinutes,
          minSpacingMinutes: i.minSpacingMinutes,
          enabled: false,
          nextRun: this.now(),
          nextMetrics: this.now() + 86400000,
          authority: a,
        };
        this.validateTemplates(p);
        this.store.put("profile:" + p.id, p);
        return p;
      },
      automation_inspect: () => ({
        profiles: this.store
          .list<Profile>("profile:")
          .map(({ authority, ...p }) => p),
        deliveries: this.deliveries()
          .filter((d) => d.automatic)
          .map((d) => this.publicDelivery(d)),
      }),
      automation_preview: async (i) => {
        const p = this.get<Profile>("profile:", i.id);
        const source = await this.options.source(p);
        return this.automationDecision(p, source.sha);
      },
      automation_enable: async (i, a) => {
        const p = this.get<Profile>("profile:", i.id);
        this.validateTemplates(p);
        p.authority = a;
        p.enabled = true;
        p.nextRun = this.now();
        this.store.put("profile:" + p.id, p);
        await this.options.wake(this.now() + 1);
        return { id: p.id, enabled: true };
      },
      automation_pause: (i) =>
        this.pauseProfile(i.id, "Paused by authorised actor."),
      billing_status: () => this.options.billing.status(),
      billing_quote: (i, a) => this.options.billing.quote(i, a),
      billing_checkout: (i, a) => this.options.billing.checkout(i, a),
      billing_portal: (i, a) => this.options.billing.portal(i, a),
    };
  }
  get<T>(prefix: string, id: string): T {
    const result = this.store.get<T>(prefix + id);
    requireValue(
      result,
      "NOT_FOUND",
      "No matching record in this workspace.",
      404,
    );
    return result;
  }
  paid() {
    const e = this.store.get<Entitlement>("entitlement");
    return (
      this.env.ADVANCED_ENABLED === "true" &&
      !!e &&
      !e.revoked &&
      e.until > this.now()
    );
  }
  paused() {
    return (
      this.env.PUBLISHING_PAUSED === "true" ||
      this.store.get<boolean>("paused") === true
    );
  }
  private connected(alias: string): Account {
    const a = this.get<Account>("account:", alias);
    requireValue(
      a.active,
      "CONNECTION_INACTIVE",
      "Reconnect this account.",
      409,
    );
    return a;
  }
  private async credential(a: Account) {
    return unseal<Credential>(
      a.secret,
      this.env.ENCRYPTION_KEY,
      this.store.get<string>("workspace") + ":" + a.alias,
    );
  }
  private deliveries() {
    return this.store.list<Delivery>("delivery:");
  }
  publicDelivery(d: Delivery) {
    const { actor, ...rest } = d;
    return rest;
  }
  private update(d: Delivery, patch: Partial<Delivery>) {
    Object.assign(d, patch, { updatedAt: this.now() });
    this.store.put("delivery:" + d.id, d);
  }
  async connect(
    actor: Actor,
    input: {
      alias: string;
      provider: Account["provider"];
      accessToken: string;
      expiresAt?: number;
      funding?: "customer_app";
    },
  ) {
    requireValue(
      actor.scopes.includes("connections") || actor.scopes.includes("admin"),
      "INSUFFICIENT_SCOPE",
      "Connection authority is required.",
      403,
    );
    requireValue(
      input.provider !== "x" || input.funding === "customer_app",
      "X_FUNDING_REQUIRED",
      "Confirm these user credentials belong to your own funded X developer application.",
    );
    const identity = await this.providers.identity(input.provider, input);
    const encrypted = await seal(
      {
        accessToken: input.accessToken,
        expiresAt: input.expiresAt,
        funding: input.funding,
      },
      this.env.ENCRYPTION_KEY,
      actor.workspace + ":" + input.alias,
      this.env.ENCRYPTION_KEY_VERSION,
    );
    return this.store.tx(() => {
      const old = this.store.get<Account>("account:" + input.alias);
      const a: Account = {
        alias: input.alias,
        provider: input.provider,
        identity,
        version: (old?.version || 0) + 1,
        secret: encrypted,
        active: true,
        verifiedAt: this.now(),
      };
      this.store.put("account:" + a.alias, a);
      return {
        alias: a.alias,
        provider: a.provider,
        identity,
        version: a.version,
      };
    });
  }
  async run(name: string, input: unknown, actor: Actor): Promise<unknown> {
    const operation = byName.get(name);
    requireValue(
      operation,
      "UNKNOWN_OPERATION",
      "Read /help.json for available operations.",
      404,
    );
    requireValue(
      actor.scopes.includes("admin") || actor.scopes.includes(operation.scope),
      "INSUFFICIENT_SCOPE",
      `Required scope: ${operation.scope}`,
      403,
    );
    const parsed = operation.schema.safeParse(input);
    if (!parsed.success)
      throw new Fault(
        "INVALID_INPUT",
        "Input did not match the operation schema.",
        400,
        parsed.error.issues.map((i) => ({ path: i.path, message: i.message })),
      );
    requireValue(
      operation.tier === "free" || this.paid(),
      "ADVANCED_REQUIRED",
      "This operation requires active Advanced access. Free publishing remains available.",
      402,
    );
    const data = parsed.data as any;
    guardControlledPublication(this.store, name, data);
    if (!data.idempotencyKey) return this.handlers[name](data, actor);
    const hash = await digest({ name, data }),
      key =
        "operation:" +
        (await digest({ actor: actor.id, key: data.idempotencyKey }));
    const prior = this.store.tx(() => {
      const previous = this.store.get<any>(key);
      if (previous) {
        requireValue(
          previous.hash === hash,
          "IDEMPOTENCY_CONFLICT",
          "This key was used with different inputs.",
          409,
        );
        return previous;
      }
      this.store.put(key, { hash, status: "pending", createdAt: this.now() });
      return undefined;
    });
    if (prior) {
      if (prior.status === "failed")
        throw new Fault(prior.code, prior.message, prior.httpStatus);
      return prior.status === "complete"
        ? prior.result
        : {
            status: "pending",
            operation: name,
            inspection: operation.inspection,
          };
    }
    try {
      const result = await this.handlers[name](data, actor);
      this.store.put(key, { hash, status: "complete", result });
      return result;
    } catch (e) {
      if (e instanceof Fault)
        this.store.put(key, {
          hash,
          status: "failed",
          code: e.code,
          message: e.message,
          httpStatus: e.status,
        });
      throw e;
    }
  }
  private validate(c: Campaign) {
    const p = this.get<Project>("project:", c.project);
    return {
      campaign: c.id,
      digest: c.digest,
      targets: Object.entries(c.text).map(([alias, text]) => {
        requireValue(
          p.accounts.includes(alias),
          "ACCOUNT_NOT_BOUND",
          "Project routing changed.",
          409,
        );
        const a = this.connected(alias);
        return {
          alias,
          identity: a.identity,
          binding: a.version,
          ...validateText(a.provider, text),
        };
      }),
    };
  }
  private async prepare(
    c: Campaign,
    at: number,
    timezone: string,
    actor: Actor,
    automatic = false,
    policy?: string,
    aliases = Object.keys(c.text),
  ): Promise<Delivery[]> {
    this.validate(c);
    requireValue(
      c.digest === (await digest(c.text)),
      "PAYLOAD_DRIFT",
      "Campaign integrity check failed.",
      409,
    );
    return Promise.all(
      aliases.map(async (alias) => {
        const a = this.connected(alias);
        return {
          id: uid(),
          fingerprint: await digest({
            provider: a.provider,
            identity: a.identity.id,
            text: c.text[alias],
          }),
          campaign: c.id,
          project: c.project,
          account: alias,
          provider: a.provider,
          identity: a.identity,
          binding: a.version,
          text: c.text[alias],
          digest: await digest(c.text[alias]),
          dueAt: at,
          timezone,
          status: "scheduled" as const,
          createdAt: this.now(),
          updatedAt: this.now(),
          actor,
          automatic,
          policy,
          policyVersion: policy
            ? this.get<Profile>("profile:", policy).revision
            : undefined,
        };
      }),
    );
  }
  private reservePrepared(prepared: Delivery[]) {
    const existing = this.deliveries();
    const resolved = prepared.map((d) => {
      const id = this.store.get<string>("fingerprint:" + d.fingerprint);
      const previous = id
        ? this.store.get<Delivery>("delivery:" + id)
        : undefined;
      return previous &&
        (previous.status !== "cancelled" || previous.reviewedRelease)
        ? previous
        : d;
    });
    const fresh = resolved.filter(
      (d, index) =>
        !this.store.get("delivery:" + d.id) &&
        resolved.findIndex((x) => x.fingerprint === d.fingerprint) === index,
    );
    requireValue(
      existing.filter((d) => active.has(d.status)).length + fresh.length <=
        Number(this.env.ACTIVE_SCHEDULE_LIMIT),
      "SCHEDULE_LIMIT",
      "Active schedule capacity is full. Cancel existing schedules or wait; no charge applies.",
      429,
    );
    for (const d of fresh) {
      const day = new Date(d.dueAt).toISOString().slice(0, 10);
      const inDay =
        existing.filter(
          (x) =>
            new Date(x.dueAt).toISOString().slice(0, 10) === day &&
            x.status !== "cancelled",
        ).length +
        fresh.filter(
          (x) => new Date(x.dueAt).toISOString().slice(0, 10) === day,
        ).length;
      requireValue(
        inDay <= Number(this.env.DAILY_DELIVERY_LIMIT),
        "DELIVERY_LIMIT",
        "Daily reserved delivery capacity is full.",
        429,
      );
    }
    for (const d of fresh) {
      this.store.put("delivery:" + d.id, d);
      this.store.put("fingerprint:" + d.fingerprint, d.id);
    }
    return {
      deliveries: resolved.map((d) =>
        this.publicDelivery(
          this.store.get<Delivery>(
            "delivery:" +
              this.store.get<string>("fingerprint:" + d.fingerprint),
          )!,
        ),
      ),
      reused: prepared.length - fresh.length,
    };
  }
  /** Internal owner-pilot reservation. No I/O inside the atomic commit. */
  reserveReviewed(delivery: Delivery, commitApproval: () => void) {
    return this.store.tx(() => {
      requireValue(
        !this.paused(),
        "PUBLISHING_PAUSED",
        "Publishing is paused.",
        409,
      );
      requireValue(
        delivery.actor.workspace === this.store.get("workspace") &&
          delivery.actor.scopes.includes("admin") &&
          !delivery.actor.grant &&
          delivery.actor.ownerSession &&
          !delivery.automatic &&
          delivery.reviewedRelease === this.env.RELEASE_SHA &&
          delivery.dueAt >= this.now(),
        "REVIEW_INVALID",
        "A current session-bound owner review is required.",
        409,
      );
      const account = this.connected(delivery.account);
      requireValue(
        account.version === delivery.binding &&
          account.provider === delivery.provider &&
          account.identity.id === delivery.identity.id,
        "ACCOUNT_DRIFT",
        "The reviewed account binding changed.",
        409,
      );
      validateText(delivery.provider, delivery.text);
      requireValue(
        !this.store.get("fingerprint:" + delivery.fingerprint),
        "EXISTING_PUBLICATION",
        "This exact account/content already has a delivery. Inspect it rather than creating a pilot duplicate.",
        409,
      );
      const result = this.reservePrepared([delivery]);
      requireValue(
        result.reused === 0 &&
          result.deliveries.length === 1 &&
          result.deliveries[0].id === delivery.id,
        "RESERVATION_CONFLICT",
        "Controlled reservation did not allocate exactly its one delivery.",
        409,
      );
      commitApproval();
      return result;
    });
  }
  async reserve(
    campaign: string,
    at: number,
    timezone: string,
    actor: Actor,
    automatic = false,
    policy?: string,
  ) {
    const prepared = await this.prepare(
      this.get<Campaign>("campaign:", campaign),
      at,
      timezone,
      actor,
      automatic,
      policy,
    );
    const result = this.store.tx(() => this.reservePrepared(prepared));
    await this.options.wake(Math.max(this.now() + 1, at));
    return result;
  }
  private pauseProfile(id: string, reason: string) {
    const p = this.get<Profile>("profile:", id);
    p.enabled = false;
    this.store.put("profile:" + id, p);
    for (const d of this.deliveries().filter(
      (d) =>
        d.policy === id &&
        ["scheduled", "waiting_container"].includes(d.status),
    ))
      this.update(d, { status: "cancelled", reason });
    return { id, enabled: false };
  }
  private validateTemplates(p: Profile) {
    const project = this.get<Project>("project:", p.project);
    requireValue(
      Object.keys(p.templates).length > 0,
      "EMPTY_TEMPLATES",
      "Supply reviewed templates.",
    );
    for (const [alias, text] of Object.entries(p.templates)) {
      requireValue(
        project.accounts.includes(alias),
        "ACCOUNT_NOT_BOUND",
        "Template destination is not bound to the project.",
      );
      requireValue(
        !/\{(?!repository\}|commit\}|source_url\})[^}]*\}/.test(text),
        "UNKNOWN_TEMPLATE_FIELD",
        "Allowed substitutions: {repository}, {commit}, {source_url}.",
      );
      validateText(
        this.connected(alias).provider,
        text
          .replaceAll("{repository}", p.repository)
          .replaceAll("{commit}", "a".repeat(40))
          .replaceAll(
            "{source_url}",
            `https://github.com/${p.repository}/blob/${"a".repeat(40)}/${p.path}`,
          ),
      );
    }
  }
  private automationDecision(p: Profile, sha: string) {
    const history = this.deliveries().filter(
      (d) => d.automatic && d.status !== "cancelled" && d.status !== "failed",
    );
    const other = history.filter(
      (d) =>
        d.project === p.project ||
        this.store.get<Profile>("profile:" + d.policy)?.family === p.family,
    );
    const start = Math.max(
      this.now(),
      ...other.map(
        (d) =>
          d.dueAt + Math.max(p.minSpacingMinutes, p.intervalMinutes) * 60000,
      ),
    );
    return {
      profile: p.id,
      sourceSha: sha,
      previousSha: p.sha || null,
      action: !p.sha ? "baseline" : sha === p.sha ? "unchanged" : "replenish",
      firstSlot: new Date(start).toISOString(),
      withinHorizon: start <= this.now() + 75 * 60000,
      reason:
        "Reviewed templates only; source change is evidence of a development update, not proof of deployed functionality.",
    };
  }
  private async captureScheduledMetrics(p: Profile) {
    const attemptAt = this.now();
    let failures = 0;
    for (const d of this.deliveries()
      .filter((d) => d.policy === p.id && d.postId)
      .sort((a, b) => b.dueAt - a.dueAt)
      .slice(0, 10)) {
      const current = this.get<Profile>("profile:", p.id);
      if (
        !current.enabled ||
        current.revision !== p.revision ||
        !this.paid()
      )
        return false;
      try {
        const a = this.connected(d.account);
        requireValue(
          a.identity.id === d.identity.id,
          "ACCOUNT_DRIFT",
          "Account changed since publication.",
          409,
        );
        const metrics = await this.providers.metrics(
          d,
          await this.credential(a),
        );
        const latest = this.get<Profile>("profile:", p.id);
        if (
          !latest.enabled ||
          latest.revision !== p.revision ||
          !this.paid()
        )
          return false;
        this.update(d, { metrics });
      } catch {
        failures++;
      }
    }
    p.lastMetricsAttempt = attemptAt;
    if (failures) {
      p.metricsError = "METRICS_UNAVAILABLE";
      p.nextMetrics = attemptAt + 15 * 60000;
    } else {
      p.metricsError = undefined;
      p.lastMetricsSuccess = attemptAt;
      p.nextMetrics = attemptAt + 86400000;
    }
    return true;
  }
  private async automationTick() {
    for (const p of this.store
      .list<Profile>("profile:")
      .filter((p) => p.enabled)
      .slice(0, 10)) {
      if (!this.paid() || !(await this.options.authorized(p.authority))) {
        this.pauseProfile(
          p.id,
          "Advanced access or delegated authority expired. Explicit resume required.",
        );
        continue;
      }
      const sourceDue = p.nextRun <= this.now();
      const metricsDue = p.nextMetrics <= this.now();
      if (!sourceDue && !metricsDue) continue;

      if (sourceDue) {
        try {
          const source = await this.options.source(p),
            decision = this.automationDecision(p, source.sha);
          const current = this.get<Profile>("profile:", p.id);
          if (
            !current.enabled ||
            current.revision !== p.revision ||
            !this.paid()
          )
            continue;
          if (p.sha && p.sha !== source.sha) {
            for (const d of this.deliveries().filter(
              (d) =>
                d.policy === p.id &&
                ["scheduled", "waiting_container"].includes(d.status),
            ))
              this.update(d, {
                status: "cancelled",
                reason: "Source snapshot changed.",
              });
            const text = Object.fromEntries(
              Object.entries(p.templates).map(([alias, template]) => [
                alias,
                template
                  .replaceAll("{repository}", p.repository)
                  .replaceAll("{commit}", source.sha)
                  .replaceAll(
                    "{source_url}",
                    `https://github.com/${p.repository}/blob/${source.sha}/${p.path}`,
                  ),
              ]),
            );
            const c: Campaign = {
              id: await digest({ profile: p.id, sha: source.sha }),
              project: p.project,
              text,
              digest: await digest(text),
              createdAt: this.now(),
              source: { profile: p.id, sha: source.sha, family: p.family },
            };
            this.validate(c);
            this.store.put("campaign:" + c.id, c);
            this.store.put("inventory:" + p.id, {
              campaign: c.id,
              sha: source.sha,
            });
          }
          p.sha = source.sha;
          p.error = undefined;
          p.lastCheck = this.now();
          const inventory = this.store.get<{ campaign: string; sha: string }>(
            "inventory:" + p.id,
          );
          if (
            inventory &&
            inventory.sha === source.sha &&
            decision.withinHorizon
          ) {
            const c = this.get<Campaign>("campaign:", inventory.campaign);
            let slot = Date.parse(decision.firstSlot);
            let remaining = false;
            for (const alias of Object.keys(c.text)) {
              const account = this.connected(alias);
              const fingerprint = await digest({
                provider: account.provider,
                identity: account.identity.id,
                text: c.text[alias],
              });
              const existingId = this.store.get<string>(
                "fingerprint:" + fingerprint,
              );
              const existing = existingId
                ? this.store.get<Delivery>("delivery:" + existingId)
                : undefined;
              if (existing && existing.status !== "cancelled") continue;
              if (slot > this.now() + 75 * 60000) {
                remaining = true;
                continue;
              }
              const prepared = await this.prepare(
                c,
                slot,
                "UTC",
                p.authority,
                true,
                p.id,
                [alias],
              );
              this.store.tx(() => {
                const latest = this.get<Profile>("profile:", p.id);
                requireValue(
                  latest.enabled &&
                    latest.revision === p.revision &&
                    this.paid(),
                  "AUTOMATION_CHANGED",
                  "Automation authority changed during allocation.",
                  409,
                );
                return this.reservePrepared(prepared);
              });
              slot +=
                Math.max(p.minSpacingMinutes, p.intervalMinutes) * 60000;
            }
            if (!remaining) this.store.delete("inventory:" + p.id);
          }
        } catch (e) {
          p.error = e instanceof Fault ? e.code : "SOURCE_UNAVAILABLE";
        }
        p.nextRun = this.now() + 15 * 60000;
      }

      let latestProfile = this.get<Profile>("profile:", p.id);
      if (latestProfile.revision !== p.revision || !latestProfile.enabled)
        continue;

      if (metricsDue && !(await this.captureScheduledMetrics(p))) continue;

      latestProfile = this.get<Profile>("profile:", p.id);
      if (latestProfile.revision !== p.revision || !latestProfile.enabled)
        continue;
      p.enabled = true;
      this.store.put("profile:" + p.id, p);
    }
  }
  private async dispatch(id: string) {
    let d = this.get<Delivery>("delivery:", id);
    if (d.status === "executing") {
      if ((d.claimUntil || 0) > this.now()) return;
      this.update(d, {
        status: d.postId
          ? "published_unverified"
          : d.phase === "publish"
            ? "ambiguous_effect"
            : "failed",
        reason: d.postId
          ? "Creation ID survived interruption; readback was not completed."
          : d.phase === "publish"
            ? "Execution interrupted after write boundary. Do not resubmit."
            : "Execution interrupted before publication. No automatic retry.",
      });
      return;
    }
    if (!["scheduled", "waiting_container"].includes(d.status) || this.paused())
      return;
    if (
      d.automatic &&
      (!this.paid() ||
        !this.store.get<Profile>("profile:" + d.policy)?.enabled ||
        this.store.get<Profile>("profile:" + d.policy)?.revision !==
          d.policyVersion)
    ) {
      this.update(d, {
        status: "cancelled",
        reason: "Automation authority inactive.",
      });
      return;
    }
    const initiallyAuthorized = await this.options.authorized(d.actor);
    d = this.get<Delivery>("delivery:", id);
    if (!["scheduled", "waiting_container"].includes(d.status)) return;
    if (!initiallyAuthorized) {
      this.update(d, {
        status: "drift_blocked",
        reason: "Delegated publishing authority revoked or expired.",
      });
      return;
    }
    if (d.automatic) {
      const p = this.get<Profile>("profile:", d.policy!);
      try {
        const source = await this.options.source(p);
        const c = this.get<Campaign>("campaign:", d.campaign);
        if (source.sha !== c.source?.sha) {
          this.update(d, {
            status: "drift_blocked",
            reason: "Source snapshot changed before execution.",
          });
          return;
        }
      } catch {
        this.update(d, {
          status: "drift_blocked",
          reason: "Source freshness could not be verified.",
        });
        return;
      }
    }
    d = this.get<Delivery>("delivery:", id);
    if (!["scheduled", "waiting_container"].includes(d.status)) return;
    const previousPhase = d.phase;
    const claimId = uid();
    this.store.tx(() =>
      this.update(d, {
        status: "executing",
        phase:
          previousPhase === "container_wait" ? "container_wait" : "identity",
        claimId,
        claimUntil: this.now() + 60000,
      }),
    );
    const demandClaim = () => {
      const current = this.get<Delivery>("delivery:", id);
      requireValue(
        current.claimId === claimId &&
          current.status === "executing" &&
          (current.claimUntil || 0) > this.now(),
        "CLAIM_LOST",
        "The execution claim expired or was recovered. No further provider write is permitted.",
        409,
      );
    };
    try {
      const a = this.connected(d.account);
      requireValue(
        a.version === d.binding &&
          a.provider === d.provider &&
          a.identity.id === d.identity.id,
        "ACCOUNT_DRIFT",
        "Account binding changed.",
        409,
      );
      requireValue(
        (await digest(d.text)) === d.digest,
        "PAYLOAD_DRIFT",
        "Captured content integrity failed.",
        409,
      );
      const credential = await this.credential(a),
        identity = await this.providers.identity(d.provider, credential);
      demandClaim();
      requireValue(
        identity.id === d.identity.id,
        "ACCOUNT_DRIFT",
        "Provider identity no longer matches the authorised account.",
        409,
      );
      requireValue(
        !d.reviewedRelease || d.reviewedRelease === this.env.RELEASE_SHA,
        "AUTHORITY_CHANGED",
        "The runtime changed after owner approval.",
        409,
      );
      if (d.provider === "threads") {
        if (!d.containerId) {
          this.update(d, {
            phase: "container_create",
            claimUntil: this.now() + 60000,
          });
          const containerId = await this.providers.createContainer(d, credential);
          demandClaim();
          this.update(d, {
            containerId,
            phase: "container_wait",
            status: "waiting_container",
            containerChecks: 0,
            nextCheck: this.now() + 30000,
          });
          return;
        }
        const status = await this.providers.containerStatus(
          d.containerId,
          credential,
        );
        demandClaim();
        d.containerChecks = (d.containerChecks || 0) + 1;
        if (
          ["IN_PROGRESS", "NOT_VISIBLE"].includes(status) &&
          d.containerChecks < 10
        ) {
          this.update(d, {
            status: "waiting_container",
            phase: "container_wait",
            nextCheck: this.now() + 30000,
          });
          return;
        }
        if (status === "PUBLISHED") {
          this.update(d, {
            status: "ambiguous_effect",
            reason:
              "Threads container is already PUBLISHED; inspect existing provider evidence.",
          });
          return;
        }
        requireValue(
          status === "FINISHED",
          "CONTAINER_NOT_READY",
          `Threads container status ${status}; no publication was attempted.`,
          409,
        );
      }
      const authorized = await this.options.authorized(d.actor);
      demandClaim();
      const latest = this.connected(d.account);
      requireValue(
        latest.version === d.binding &&
          !this.paused() &&
          authorized &&
          (!d.reviewedRelease || d.reviewedRelease === this.env.RELEASE_SHA),
        "AUTHORITY_CHANGED",
        "Publication authority changed before provider write.",
        409,
      );
      if (d.automatic)
        requireValue(
          this.paid() &&
            this.store.get<Profile>("profile:" + d.policy)?.enabled &&
            this.store.get<Profile>("profile:" + d.policy)?.revision ===
              d.policyVersion,
          "AUTOMATION_INACTIVE",
          "Continuing authority is inactive.",
          409,
        );
      this.update(d, { phase: "publish", claimUntil: this.now() + 60000 });
      const published = await this.providers.publish(d, credential);
      const current = this.get<Delivery>("delivery:", id);
      if (current.claimId !== claimId) return;
      d = current;
      this.update(d, {
        postId: published.id,
        url: published.url,
        phase: "readback",
        status: "published_unverified",
      });
      try {
        const evidence = await this.providers.verify(d, credential);
        this.update(d, {
          status: evidence.verified
            ? "published_verified"
            : "published_unverified",
          url: evidence.url || d.url,
          reason: evidence.verified
            ? undefined
            : "Provider creation ID recorded; exact readback unavailable.",
        });
      } catch {
        this.update(d, {
          reason: "Provider creation ID recorded; readback unavailable.",
        });
      }
    } catch (e) {
      const code = e instanceof Fault ? e.code : "UNEXPECTED_FAILURE";
      const latest = this.get<Delivery>("delivery:", id);
      if (
        code === "CLAIM_LOST" ||
        latest.claimId !== claimId ||
        latest.status !== "executing"
      )
        return;
      this.update(d, {
        status:
          code === "AMBIGUOUS_PROVIDER_WRITE" ||
          (!(e instanceof Fault) && d.phase === "publish")
            ? "ambiguous_effect"
            : [
                  "ACCOUNT_DRIFT",
                  "PAYLOAD_DRIFT",
                  "AUTHORITY_CHANGED",
                  "CONNECTION_INACTIVE",
                ].includes(code)
              ? "drift_blocked"
              : "failed",
        reason: code,
      });
    }
  }
  async tick() {
    await this.automationTick();
    const due = this.deliveries()
      .filter((d) =>
        d.status === "scheduled"
          ? d.dueAt <= this.now()
          : d.status === "waiting_container"
            ? (d.nextCheck || 0) <= this.now()
            : d.status === "executing" && (d.claimUntil || 0) <= this.now(),
      )
      .sort((a, b) => a.dueAt - b.dueAt)
      .slice(0, 10);
    for (const d of due) await this.dispatch(d.id);
    await this.scheduleNext();
  }
  async scheduleNext() {
    const times = this.deliveries()
      .filter((d) => active.has(d.status))
      .map((d) =>
        d.status === "scheduled"
          ? d.dueAt
          : d.status === "waiting_container"
            ? d.nextCheck!
            : d.claimUntil!,
      );
    times.push(
      ...this.store
        .list<Profile>("profile:")
        .filter((p) => p.enabled)
        .flatMap((p) => [p.nextRun, p.nextMetrics]),
    );
    if (times.length)
      await this.options.wake(
        Math.max(
          this.now() + (this.paused() ? 60000 : 1000),
          Math.min(...times),
        ),
      );
  }
}
