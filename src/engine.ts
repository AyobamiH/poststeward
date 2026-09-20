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
import { credentialRoots, seal, unseal } from "./crypto.ts";
import {
  providerActorForIdentity,
  validateText,
  type Credential,
  type ProviderAPI,
} from "./providers.ts";
import {
  freezePublication,
  publicationParts,
  validateFrozenPublication,
  type FrozenPublication,
} from "./publications.ts";
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
      publishing_capabilities: () => {
        const accounts = this.store
          .list<Account>("account:")
          .filter((account) => account.active)
          .map(({ secret, ...account }) => account);
        const connected = (provider: Account["provider"]) =>
          accounts
            .filter((account) => account.provider === provider)
            .map((account) => ({
              alias: account.alias,
              identity: account.identity,
              binding: account.version,
              capabilities: account.capabilities || null,
            }));
        const configured = (id?: string, secret?: string) =>
          Boolean(id && secret);
        return {
          workspaceUrl: `${this.env.PUBLIC_ORIGIN}/app`,
          oneShotPilotUrl: `${this.env.PUBLIC_ORIGIN}/pilot`,
          identityRule:
            "The stable identity returned by the provider—not the local alias and not PostSteward—is the public author.",
          providers: {
            x: {
              applicationConfigured: configured(
                this.env.X_OAUTH_CLIENT_ID,
                this.env.X_OAUTH_CLIENT_SECRET,
              ),
              connections: connected("x"),
              formats: {
                text: "implemented",
                multipartText: "implemented",
                images: "not_implemented",
                video: "not_implemented",
                replies: "not_implemented",
              },
            },
            threads: {
              applicationConfigured: configured(
                this.env.THREADS_OAUTH_CLIENT_ID,
                this.env.THREADS_OAUTH_CLIENT_SECRET,
              ),
              connections: connected("threads"),
              formats: {
                text: "implemented",
                multipartText: "implemented",
                images: "not_implemented",
                video: "not_implemented",
                replies: "not_implemented",
              },
            },
            linkedin: {
              memberApplicationConfigured: configured(
                this.env.LINKEDIN_OAUTH_CLIENT_ID,
                this.env.LINKEDIN_OAUTH_CLIENT_SECRET,
              ),
              pageApplicationConfigured: configured(
                this.env.LINKEDIN_ORGANIZATION_OAUTH_CLIENT_ID,
                this.env.LINKEDIN_ORGANIZATION_OAUTH_CLIENT_SECRET,
              ),
              connections: connected("linkedin"),
              formats: {
                memberText: "implemented_app_configuration_required",
                pageText: "implemented_external_approval_required",
                images: "not_implemented",
                video: "not_implemented",
                carousel: "not_implemented",
                replies: "not_implemented",
              },
            },
          },
          controls: {
            immutableCampaigns: "implemented",
            contentDigests: "implemented",
            explicitScheduling: "implemented",
            providerReadback: "implemented_permission_dependent",
            agentOwnerApproval: "implemented",
            linkPreviewManagement: "not_implemented",
            editDeletionMonitoring: "not_implemented",
            sourceMonitoring:
              this.env.ADVANCED_ENABLED === "true"
                ? "enabled_for_workspace"
                : "implemented_rollout_disabled",
          },
        };
      },
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
              ["pending_approval", "scheduled", "waiting_container"].includes(
                d.status,
              ),
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
        requireValue(
          Object.keys(i.text).length > 0,
          "EMPTY_CAMPAIGN",
          "Provide at least one destination.",
        );
        const text: Record<string, string> = {};
        const publications: Record<string, FrozenPublication> = {};
        for (const [alias, value] of Object.entries<string>(i.text)) {
          requireValue(
            p.accounts.includes(alias),
            "ACCOUNT_NOT_BOUND",
            "Campaign account is not bound to this project.",
          );
          const publication = await freezePublication(
            this.connected(alias).provider,
            value,
          );
          text[alias] = publication.text;
          publications[alias] = publication;
        }
        const c: Campaign = {
          id: uid(),
          project: p.id,
          text,
          publications,
          digest: await digest(text),
          createdAt: this.now(),
        };
        this.store.put("campaign:" + c.id, c);
        return c;
      },
      campaign_get: (i) => this.get<Campaign>("campaign:", i.campaign),
      campaign_validate: async (i) =>
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
          if (
            ["pending_approval", "scheduled", "waiting_container"].includes(
              d.status,
            )
          ) {
            this.update(d, {
              status: "cancelled",
              reason: "Cancelled by authorised actor.",
              ...(d.approval?.status === "pending"
                ? {
                    approval: {
                      ...d.approval,
                      status: "rejected" as const,
                      reviewedAt: this.now(),
                      reviewer: "cancelled",
                    },
                  }
                : {}),
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
      delivery_approve: async (i, a) => {
        requireValue(
          !a.grant && a.scopes.includes("admin"),
          "OWNER_APPROVAL_REQUIRED",
          "A signed-in workspace owner must approve agent deliveries.",
          403,
        );
        const candidate = this.get<Delivery>("delivery:", i.delivery);
        requireValue(
          candidate.status === "pending_approval" &&
            candidate.approval?.required === true &&
            candidate.approval.status === "pending",
          "APPROVAL_NOT_PENDING",
          "This delivery is not awaiting owner approval.",
          409,
        );
        requireValue(
          await this.options.authorized(candidate.actor),
          "AUTHORITY_CHANGED",
          "The requesting agent authority was revoked or expired.",
          409,
        );
        const result = this.store.tx(() => {
          const d = this.get<Delivery>("delivery:", i.delivery);
          requireValue(
            d.status === "pending_approval" &&
              d.approval?.status === "pending" &&
              d.fingerprint === candidate.fingerprint,
            "APPROVAL_CHANGED",
            "The approval request changed before review completed.",
            409,
          );
          this.update(d, {
            status: "scheduled",
            approval: {
              ...d.approval,
              status: "approved",
              reviewedAt: this.now(),
              reviewer: a.id,
            },
            reason: undefined,
          });
          return this.publicDelivery(d);
        });
        await this.options.wake(Math.max(this.now() + 1, candidate.dueAt));
        return result;
      },
      delivery_reject: (i, a) => {
        requireValue(
          !a.grant && a.scopes.includes("admin"),
          "OWNER_APPROVAL_REQUIRED",
          "A signed-in workspace owner must reject agent deliveries.",
          403,
        );
        return this.store.tx(() => {
          const d = this.get<Delivery>("delivery:", i.delivery);
          requireValue(
            d.status === "pending_approval" && d.approval?.status === "pending",
            "APPROVAL_NOT_PENDING",
            "This delivery is not awaiting owner approval.",
            409,
          );
          this.update(d, {
            status: "cancelled",
            approval: {
              ...d.approval,
              status: "rejected",
              reviewedAt: this.now(),
              reviewer: a.id,
            },
            reason: "Rejected by workspace owner before provider dispatch.",
          });
          return this.publicDelivery(d);
        });
      },
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
            ["pending_approval", "scheduled", "waiting_container"].includes(
              d.status,
            ),
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
      receipt_recheck: (i) => this.recheckReceipt(i.delivery),
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
        const metrics = await this.captureMetrics(d, a);
        this.update(this.get<Delivery>("delivery:", d.id), { metrics });
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
      credentialRoots(this.env),
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
    const expected = JSON.stringify([
      this.store.get("account:" + input.alias),
      this.store.get("oauth:" + input.alias),
    ]);
    const identity = await this.providers.identity(input.provider, input);
    const encrypted = await seal(
      {
        accessToken: input.accessToken,
        expiresAt: input.expiresAt,
        funding: input.funding,
      },
      credentialRoots(this.env),
      actor.workspace + ":" + input.alias,
      this.env.ENCRYPTION_KEY_VERSION,
    );
    return this.store.tx(() => {
      requireValue(
        JSON.stringify([
          this.store.get("account:" + input.alias),
          this.store.get("oauth:" + input.alias),
        ]) === expected,
        "CONNECTION_CHANGED",
        "Connection changed during identity verification. Review the current connection before reconnecting.",
        409,
      );
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
      // Manual replacement must never inherit the previous OAuth refresh grant.
      this.store.delete("oauth:" + a.alias);
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
      requireValue(
        prior.status !== "archived",
        "OPERATION_ARCHIVED",
        "This completed operation was archived. Inspect the saved archive; it will not be executed again.",
        409,
      );
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
      this.store.put(key, {
        hash,
        status: "complete",
        result,
        completedAt: this.now(),
      });
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
  private async deliveryPublication(delivery: Delivery) {
    return delivery.publication
      ? validateFrozenPublication(delivery.provider, delivery.publication)
      : freezePublication(delivery.provider, delivery.text);
  }
  private async verifyRecordedPublication(
    delivery: Delivery,
    credential: Credential,
  ) {
    const publication = await this.deliveryPublication(delivery);
    const ids = delivery.partIds?.length
      ? delivery.partIds
      : delivery.postId
        ? [delivery.postId]
        : [];
    if (ids.length !== publication.parts.length)
      return { verified: false, verifiedParts: 0, url: delivery.url };
    let verifiedParts = 0;
    let url = delivery.url;
    for (let index = 0; index < publication.parts.length; index++) {
      const part = publication.parts[index];
      const evidence = await this.providers.verify(
        {
          ...delivery,
          postId: ids[index],
          text: part.text,
          digest: part.digest,
        },
        credential,
      );
      if (!evidence.verified) break;
      verifiedParts++;
      if (index === 0 && evidence.url) url = evidence.url;
    }
    return {
      verified: verifiedParts === publication.parts.length,
      verifiedParts,
      url,
    };
  }
  private async recheckReceipt(id: string) {
    const delivery = this.get<Delivery>("delivery:", id);
    requireValue(
      delivery.postId &&
        ["published_unverified", "published_verified"].includes(
          delivery.status,
        ),
      "NO_READBACK_TARGET",
      "Inspect the existing receipt. A recorded publication ID is required; never republish an uncertain write.",
      409,
    );
    if (delivery.status === "published_verified")
      return this.publicDelivery(delivery);
    const demandBinding = () => {
      const account = this.connected(delivery.account);
      requireValue(
        account.provider === delivery.provider &&
          account.version === delivery.binding &&
          account.identity.id === delivery.identity.id,
        "ACCOUNT_DRIFT",
        "The connection no longer matches the recorded publication.",
        409,
      );
      requireValue(
        delivery.provider !== "linkedin" ||
          account.capabilities?.readback === true,
        "READBACK_AUTHORITY_CHANGED",
        "LinkedIn readback authority is unavailable.",
        409,
      );
      return account;
    };
    const account = demandBinding();
    const attempt = this.store.tx(() => {
      const current = this.get<Delivery>("delivery:", id);
      requireValue(
        (current.readbackAttempts || 0) < 8 &&
          (current.nextReadbackAt || 0) <= this.now(),
        "READBACK_LIMIT",
        "Readback recovery permits eight attempts, at least sixty seconds apart.",
        429,
      );
      const attempt = (current.readbackAttempts || 0) + 1;
      this.update(current, {
        readbackAttempts: attempt,
        nextReadbackAt: this.now() + 60000,
      });
      return attempt;
    });
    let evidence: {
      verified: boolean;
      verifiedParts: number;
      url?: string;
    } = { verified: false, verifiedParts: 0 };
    try {
      const credential = await this.credential(account);
      demandBinding();
      evidence = await this.verifyRecordedPublication(delivery, credential);
    } catch {
      // A failed read cannot erase the durable creation ID or enable a write.
    }
    demandBinding();
    return this.store.tx(() => {
      const current = this.get<Delivery>("delivery:", id);
      requireValue(
        current.postId === delivery.postId &&
          current.fingerprint === delivery.fingerprint &&
          current.readbackAttempts === attempt,
        "READBACK_CHANGED",
        "A newer readback owns this receipt.",
        409,
      );
      if (current.status !== "published_unverified")
        return this.publicDelivery(current);
      this.update(current, {
        status: evidence.verified
          ? "published_verified"
          : "published_unverified",
        verifiedParts: evidence.verifiedParts,
        url: evidence.url || current.url,
        lastReadbackAt: this.now(),
        reason: evidence.verified
          ? undefined
          : "Provider creation ID retained; exact readback not confirmed.",
      });
      return this.publicDelivery(current);
    });
  }
  private async publication(
    c: Campaign,
    alias: string,
    provider: Account["provider"],
  ) {
    const frozen = c.publications?.[alias];
    if (frozen) return validateFrozenPublication(provider, frozen);
    return freezePublication(provider, c.text[alias]);
  }
  private async validate(c: Campaign) {
    const p = this.get<Project>("project:", c.project);
    requireValue(
      c.digest === (await digest(c.text)),
      "PAYLOAD_DRIFT",
      "Campaign integrity check failed.",
      409,
    );
    return {
      campaign: c.id,
      digest: c.digest,
      targets: await Promise.all(
        Object.entries(c.text).map(async ([alias]) => {
          requireValue(
            p.accounts.includes(alias),
            "ACCOUNT_NOT_BOUND",
            "Project routing changed.",
            409,
          );
          const a = this.connected(alias);
          const publication = await this.publication(c, alias, a.provider);
          return {
            alias,
            identity: a.identity,
            binding: a.version,
            provider: a.provider,
            publicationType: publication.type,
            partCount: publication.parts.length,
            publicationDigest: publication.publicationDigest,
            parts: publication.parts.map(({ index, digest, text }) => ({
              index,
              digest,
              ...validateText(a.provider, text),
            })),
          };
        }),
      ),
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
    await this.validate(c);
    return Promise.all(
      aliases.map(async (alias) => {
        const a = this.connected(alias);
        const publication = await this.publication(c, alias, a.provider);
        const approval = actor.grant
          ? {
              required: true,
              status: "pending" as const,
              requestedAt: this.now(),
            }
          : undefined;
        return {
          id: uid(),
          fingerprint: await digest({
            provider: a.provider,
            identity: a.identity.id,
            text: publication.text,
          }),
          campaign: c.id,
          project: c.project,
          account: alias,
          provider: a.provider,
          identity: a.identity,
          binding: a.version,
          text: publication.text,
          digest: publication.digest,
          publication,
          dueAt: at,
          timezone,
          status: actor.grant
            ? ("pending_approval" as const)
            : ("scheduled" as const),
          createdAt: this.now(),
          updatedAt: this.now(),
          actor,
          automatic,
          ...(approval ? { approval } : {}),
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
    if (
      result.deliveries.some((delivery) =>
        ["scheduled", "waiting_container"].includes(delivery.status),
      )
    )
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
        ["pending_approval", "scheduled", "waiting_container"].includes(
          d.status,
        ),
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
      publicationParts(
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
  private async captureMetrics(delivery: Delivery, account: Account) {
    const credential = await this.credential(account);
    const demandBinding = () => {
      const current = this.connected(delivery.account);
      requireValue(
        current.provider === delivery.provider &&
          current.version === delivery.binding &&
          current.identity.id === delivery.identity.id,
        "ACCOUNT_DRIFT",
        "Account changed since publication.",
        409,
      );
    };
    demandBinding();
    const metrics = await this.providers.metrics(delivery, credential);
    demandBinding();
    return metrics;
  }
  private async captureScheduledMetrics(p: Profile) {
    const attemptAt = this.now();
    let failures = 0;
    for (const d of this.deliveries()
      .filter((d) => d.policy === p.id && d.postId)
      .sort((a, b) => b.dueAt - a.dueAt)
      .slice(0, 10)) {
      const current = this.get<Profile>("profile:", p.id);
      if (!current.enabled || current.revision !== p.revision || !this.paid())
        return false;
      try {
        const a = this.connected(d.account);
        requireValue(
          a.identity.id === d.identity.id,
          "ACCOUNT_DRIFT",
          "Account changed since publication.",
          409,
        );
        const metrics = await this.captureMetrics(d, a);
        const latest = this.get<Profile>("profile:", p.id);
        if (!latest.enabled || latest.revision !== p.revision || !this.paid())
          return false;
        this.update(this.get<Delivery>("delivery:", d.id), { metrics });
        if (
          (metrics as { availability?: string } | null)?.availability !==
          "available"
        )
          failures++;
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
                ["pending_approval", "scheduled", "waiting_container"].includes(
                  d.status,
                ),
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
            const publications = Object.fromEntries(
              await Promise.all(
                Object.entries(text).map(async ([alias, value]) => [
                  alias,
                  await freezePublication(
                    this.connected(alias).provider,
                    value,
                  ),
                ]),
              ),
            );
            const c: Campaign = {
              id: await digest({ profile: p.id, sha: source.sha }),
              project: p.project,
              text,
              publications,
              digest: await digest(text),
              createdAt: this.now(),
              source: { profile: p.id, sha: source.sha, family: p.family },
            };
            await this.validate(c);
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
              const publication = await this.publication(
                c,
                alias,
                account.provider,
              );
              const fingerprint = await digest({
                provider: account.provider,
                identity: account.identity.id,
                text: publication.text,
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
              slot += Math.max(p.minSpacingMinutes, p.intervalMinutes) * 60000;
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
      const completed = d.partIds?.length || (d.postId ? 1 : 0);
      const partCount = d.publication?.parts.length || 1;
      this.update(d, {
        status: completed
          ? completed === partCount
            ? "published_unverified"
            : "partial_effect"
          : d.phase === "publish"
            ? "ambiguous_effect"
            : "failed",
        reason: completed
          ? completed === partCount
            ? "Every provider creation ID survived interruption; readback was not completed."
            : `Execution stopped after ${completed}/${partCount} durable thread parts. No completed part will be replayed.`
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
      const publication = await this.deliveryPublication(d);
      requireValue(
        publication.digest === d.digest,
        "PAYLOAD_DRIFT",
        "Captured publication no longer matches its text digest.",
        409,
      );
      const credential = await this.credential(a),
        identity = await this.providers.identity(
          d.provider,
          credential,
          providerActorForIdentity(d.provider, d.identity),
        );
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
      const completedParts = d.partIds?.length || 0;
      requireValue(
        completedParts < publication.parts.length,
        "PUBLICATION_COMPLETE",
        "All provider part IDs are already recorded. Use receipt readback; never publish again.",
        409,
      );
      const part = publication.parts[completedParts];
      const replyToId = completedParts
        ? d.partIds?.[completedParts - 1]
        : undefined;
      if (d.provider === "threads") {
        if (!d.containerId) {
          this.update(d, {
            phase: "container_create",
            claimUntil: this.now() + 60000,
          });
          const containerId = await this.providers.createContainer(
            d,
            credential,
            { text: part.text, ...(replyToId ? { replyToId } : {}) },
          );
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
      const published = await this.providers.publish(d, credential, {
        text: part.text,
        ...(replyToId ? { replyToId } : {}),
      });
      const current = this.get<Delivery>("delivery:", id);
      if (current.claimId !== claimId) return;
      d = current;
      const partIds = [...(d.partIds || []), published.id];
      this.update(d, {
        partIds,
        postId: d.postId || published.id,
        url: d.url || published.url,
        containerId: undefined,
        containerChecks: undefined,
        nextCheck: undefined,
        phase: "readback",
        status: "published_unverified",
      });
      let verified = false;
      try {
        const evidence = await this.providers.verify(
          {
            ...d,
            postId: published.id,
            text: part.text,
            digest: part.digest,
          },
          credential,
        );
        verified = evidence.verified;
        const contiguous = d.verifiedParts || 0;
        this.update(d, {
          verifiedParts:
            evidence.verified && contiguous === completedParts
              ? contiguous + 1
              : contiguous,
          url: completedParts === 0 ? evidence.url || d.url : d.url,
        });
      } catch {
        // The durable provider ID remains authoritative even if this read fails.
      }
      d = this.get<Delivery>("delivery:", id);
      if (partIds.length < publication.parts.length) {
        this.update(d, {
          status: "scheduled",
          phase: "identity",
          claimId: undefined,
          claimUntil: undefined,
          reason: verified
            ? `Thread part ${partIds.length}/${publication.parts.length} published and verified; the next frozen part remains.`
            : `Thread part ${partIds.length}/${publication.parts.length} has a durable provider ID; readback is pending while the next frozen part remains.`,
        });
        await this.options.wake(this.now() + 1);
        return;
      }
      const allVerified = (d.verifiedParts || 0) === publication.parts.length;
      this.update(d, {
        status: allVerified ? "published_verified" : "published_unverified",
        claimId: undefined,
        claimUntil: undefined,
        reason: allVerified
          ? undefined
          : "Every provider creation ID is recorded; one or more exact part readbacks remain unverified.",
      });
    } catch (e) {
      const code = e instanceof Fault ? e.code : "UNEXPECTED_FAILURE";
      const latest = this.get<Delivery>("delivery:", id);
      if (
        code === "CLAIM_LOST" ||
        latest.claimId !== claimId ||
        latest.status !== "executing"
      )
        return;
      const completed = latest.partIds?.length || 0;
      this.update(latest, {
        status:
          code === "AMBIGUOUS_PROVIDER_WRITE" ||
          (!(e instanceof Fault) && latest.phase === "publish")
            ? "ambiguous_effect"
            : completed > 0
              ? "partial_effect"
              : [
                    "ACCOUNT_DRIFT",
                    "PAYLOAD_DRIFT",
                    "AUTHORITY_CHANGED",
                    "CONNECTION_INACTIVE",
                  ].includes(code)
                ? "drift_blocked"
                : "failed",
        failedPartIndex: completed + 1,
        reason:
          completed > 0 && code !== "AMBIGUOUS_PROVIDER_WRITE"
            ? `Thread stopped after ${completed} durable part(s): ${code}. Completed parts will not be replayed.`
            : code,
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
      .filter((d) =>
        ["scheduled", "waiting_container", "executing"].includes(d.status),
      )
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
