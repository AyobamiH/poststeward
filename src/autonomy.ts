import { Fault, requireValue, uid } from "./common.ts";
import type { Context, Selection } from "./preparation-contracts.ts";
import type { Preparation, PreparationJob } from "./preparation.ts";
import type {
  Account,
  Actor,
  Campaign,
  Delivery,
  Project,
  Store,
} from "./types.ts";

export interface StandingAuthority {
  project: string;
  revision: number;
  enabled: boolean;
  selection: Selection;
  context: Context;
  accounts: { alias: string; binding: number; identity: string }[];
  intervalMinutes: number;
  stockFloor: number;
  maxDailyDeliveries: number;
  authority: Actor;
  nextRun: number;
  job?: string;
  error?: string;
  deferred?: string;
}
export type StandingAdmission = {
  revision: number;
  preparation: string;
  digest: string;
};

/** One bounded supply controller. The existing engine remains the only publisher. */
export class Autonomy {
  constructor(
    private store: Store,
    private preparation: Preparation,
    private options: {
      now: () => number;
      wake: (at: number) => Promise<void>;
      authorized: (actor: Actor) => Promise<boolean>;
      paid: () => boolean;
      paused: () => boolean;
      reserve: (
        campaign: string,
        at: number,
        actor: Actor,
        admission: StandingAdmission,
      ) => Promise<unknown>;
    },
  ) {}

  private policies() {
    return this.store.list<StandingAuthority>("autonomy:");
  }
  private public(policy: StandingAuthority) {
    const { authority, ...value } = policy;
    return value;
  }
  list() {
    return this.policies().map((policy) => this.public(policy));
  }
  private owner(actor: Actor) {
    requireValue(
      !actor.grant && actor.scopes.includes("admin"),
      "OWNER_AUTHORITY_REQUIRED",
      "The workspace owner must choose standing publishing authority.",
      403,
    );
  }
  configure(input: any, actor: Actor) {
    this.owner(actor);
    const project = this.store.get<Project>("project:" + input.project);
    requireValue(
      project && project.accounts.length <= 3,
      "AUTONOMY_PROJECT_REQUIRED",
      "Choose a project with one to three connected destinations.",
      409,
    );
    requireValue(
      this.preparation.status().configured,
      "MODEL_NOT_CONNECTED",
      "Connect your bounded workspace model account first.",
      409,
    );
    const accounts = project.accounts.map((alias) => {
      const account = this.store.get<Account>("account:" + alias);
      requireValue(
        account?.active,
        "ACCOUNT_NOT_CONNECTED",
        "Every selected destination must be connected.",
        409,
      );
      return { alias, binding: account.version, identity: account.identity.id };
    });
    const previous = this.store.get<StandingAuthority>(
      "autonomy:" + project.id,
    );
    const policy: StandingAuthority = {
      project: project.id,
      revision: (previous?.revision || 0) + 1,
      enabled: input.enabled,
      selection: input.selection,
      context: input.context,
      intervalMinutes: input.intervalMinutes,
      stockFloor: input.stockFloor,
      maxDailyDeliveries: input.maxDailyDeliveries,
      accounts,
      authority: actor,
      nextRun: this.options.now(),
    };
    this.store.put("autonomy:" + project.id, policy);
    return this.public(policy);
  }
  pause(project: string, actor: Actor) {
    this.owner(actor);
    const policy = this.store.get<StandingAuthority>("autonomy:" + project);
    requireValue(
      policy,
      "AUTONOMY_NOT_CONFIGURED",
      "This project has no standing authority.",
      404,
    );
    policy.enabled = false;
    policy.revision++;
    this.store.put("autonomy:" + project, policy);
    return this.public(policy);
  }
  assertCurrent(project: string, revision: number) {
    const policy = this.store.get<StandingAuthority>("autonomy:" + project);
    const routing = this.store.get<Project>("project:" + project);
    requireValue(
      policy?.enabled &&
        policy.revision === revision &&
        this.options.paid() &&
        !this.options.paused(),
      "STANDING_AUTHORITY_CHANGED",
      "Standing publishing authority is paused, expired or changed.",
      409,
    );
    requireValue(
      routing &&
        JSON.stringify([...routing.accounts].sort()) ===
          JSON.stringify(policy.accounts.map((a) => a.alias).sort()) &&
        policy.accounts.every((a) => {
          const current = this.store.get<Account>("account:" + a.alias);
          return (
            current?.active &&
            current.version === a.binding &&
            current.identity.id === a.identity
          );
        }),
      "STANDING_ACCOUNT_DRIFT",
      "Project or destination authority changed.",
      409,
    );
    return policy;
  }
  async assertAdmission(campaign: Campaign, admission: StandingAdmission) {
    const policy = this.assertCurrent(campaign.project, admission.revision);
    requireValue(
      await this.options.authorized(policy.authority),
      "STANDING_AUTHORITY_EXPIRED",
      "The original standing authority was revoked.",
      403,
    );
    this.assertCurrent(campaign.project, admission.revision);
    const job = this.store.get<PreparationJob>(
      "preparation:" + admission.preparation,
    );
    requireValue(
      job?.campaign === campaign.id &&
        job.digest === admission.digest &&
        job.standingRevision === admission.revision &&
        job.status === "handed_off",
      "STANDING_ADMISSION_REQUIRED",
      "Only checked, immutable editorial supply admitted under this project policy can publish autonomously.",
      409,
    );
    requireValue(
      await this.options.authorized(job!.actor),
      "PREPARATION_AUTHORITY_EXPIRED",
      "Original editorial request authority expired.",
      403,
    );
    this.assertCurrent(campaign.project, admission.revision);
  }
  async freshSource(campaign: Campaign, admission: StandingAdmission) {
    await this.assertAdmission(campaign, admission);
    await this.preparation.assertSourceCurrent(admission.preparation);
    await this.assertAdmission(campaign, admission);
  }
  async scheduleNext() {
    const policies = this.policies().filter((p) => p.enabled && !p.error);
    if (policies.length && this.options.paid() && !this.options.paused())
      await this.options.wake(
        Math.max(
          this.options.now() + 1000,
          Math.min(...policies.map((p) => p.nextRun)),
        ),
      );
  }
  private hold(policy: StandingAuthority, error: unknown) {
    const code = error instanceof Fault ? error.code : "AUTONOMY_UNAVAILABLE";
    const unstarted =
      !policy.job || !this.store.get("preparation:" + policy.job);
    if (
      unstarted &&
      ["PREPARATION_DAILY_LIMIT", "PREPARATION_COST_LIMIT"].includes(code)
    ) {
      policy.job = undefined;
      policy.error = undefined;
      policy.deferred = code;
      const date = new Date(this.options.now()).toISOString().slice(0, 10);
      policy.nextRun = Date.parse(date + "T00:00:00Z") + 86400000 + 1000;
    } else policy.error = code;
    this.store.put("autonomy:" + policy.project, policy);
  }
  private async beginRequest(policy: StandingAuthority, actor: Actor) {
    this.preparation.assertRequestAuthority(actor, policy.selection);
    policy.job = uid();
    policy.deferred = undefined;
    policy.nextRun = this.options.now() + 1000;
    // Reserve the durable request before any awaited work; concurrent requests reuse it.
    this.store.put("autonomy:" + policy.project, policy);
    this.preparation.trimConsumedStandingHistory();
    try {
      return await this.preparation.create(
        {
          id: policy.job,
          project: policy.project,
          selection: policy.selection,
          context: policy.context,
        },
        actor,
        policy.revision,
      );
    } catch (error) {
      if (
        this.store.get<StandingAuthority>("autonomy:" + policy.project)
          ?.revision === policy.revision
      ) {
        this.hold(policy, error);
      }
      throw error;
    }
  }
  async request(project: string, actor: Actor) {
    const current = this.store.get<StandingAuthority>("autonomy:" + project);
    requireValue(
      current,
      "AUTONOMY_NOT_CONFIGURED",
      "The owner must configure standing authority first.",
      409,
    );
    const policy = this.assertCurrent(project, current.revision);
    requireValue(
      !policy.error,
      "AUTONOMY_EDITORIAL_HOLD",
      "Inspect the existing editorial hold before requesting more work.",
      409,
    );
    requireValue(
      await this.options.authorized(actor),
      "PREPARATION_AUTHORITY_EXPIRED",
      "Requesting agent authority expired.",
      403,
    );
    this.assertCurrent(project, current.revision);
    const latest = this.store.get<StandingAuthority>("autonomy:" + project)!;
    if (latest.deferred && latest.nextRun > this.options.now())
      return { project, status: "budget_deferred", nextRun: latest.nextRun };
    if (latest.job) return { project, preparation: latest.job, reused: true };
    const active = this.store
      .list<Delivery>("delivery:")
      .filter(
        (d) =>
          d.project === project &&
          ["scheduled", "executing", "waiting_container"].includes(d.status),
      );
    if (
      latest.accounts.every(
        (a) =>
          active.filter((d) => d.account === a.alias).length >=
          latest.stockFloor,
      )
    )
      return { project, status: "stock_adequate" };
    const job = await this.beginRequest(latest, actor);
    return { project, preparation: job.id, reused: false };
  }
  async tick() {
    if (!this.options.paid() || this.options.paused()) return;
    // Fair round-robin by due time, at most one project per alarm.
    const policy = this.policies()
      .filter((p) => p.enabled && !p.error && p.nextRun <= this.options.now())
      .sort((a, b) => a.nextRun - b.nextRun)[0];
    if (!policy) return;
    const revision = policy.revision;
    try {
      this.assertCurrent(policy.project, revision);
      requireValue(
        await this.options.authorized(policy.authority),
        "STANDING_AUTHORITY_EXPIRED",
        "Standing authority was revoked.",
        403,
      );
      this.assertCurrent(policy.project, revision);
      if (policy.job) {
        const job = this.store.get<PreparationJob>("preparation:" + policy.job);
        requireValue(
          job,
          "AUTONOMY_JOB_MISSING",
          "The durable editorial request is unavailable.",
          409,
        );
        if (["queued", "running"].includes(job.status)) {
          policy.nextRun = this.options.now() + 60000;
        } else {
          requireValue(
            ["review", "approved", "handed_off"].includes(job.status),
            "AUTONOMY_EDITORIAL_HOLD",
            job.error?.message ||
              "Editorial supply needs attention; no automatic model retry.",
            409,
          );
          requireValue(
            JSON.stringify(job.selection) ===
              JSON.stringify(policy.selection) &&
              JSON.stringify(job.context) === JSON.stringify(policy.context),
            "AUTONOMY_CONTEXT_DRIFT",
            "Source or editorial context changed outside standing authority.",
            409,
          );
          const result = await this.preparation.admitStanding(
            job.id,
            revision,
            policy.authority,
          );
          this.assertCurrent(policy.project, revision);
          const campaign = this.store.get<Campaign>(
            "campaign:" + result.campaign,
          )!;
          const deliveries = this.store
            .list<Delivery>("delivery:")
            .filter((d) => d.project === policy.project);
          // Pace each destination after its last reservation; ambiguous effects consume capacity.
          const latest = Math.max(
            this.options.now(),
            ...deliveries
              .filter((d) => d.status !== "cancelled")
              .map((d) => d.dueAt + policy.intervalMinutes * 60000),
          );
          let at = latest;
          for (let day = 0; day < 8; day++) {
            const date = new Date(at).toISOString().slice(0, 10);
            if (
              policy.accounts.every(
                (a) =>
                  deliveries.filter(
                    (d) =>
                      d.account === a.alias &&
                      d.status !== "cancelled" &&
                      new Date(d.dueAt).toISOString().slice(0, 10) === date,
                  ).length < policy.maxDailyDeliveries,
              )
            )
              break;
            at = Date.parse(date + "T00:00:00Z") + 86400000;
          }
          const allocationDay = new Date(at).toISOString().slice(0, 10);
          requireValue(
            policy.accounts.every(
              (a) =>
                deliveries.filter(
                  (d) =>
                    d.account === a.alias &&
                    d.status !== "cancelled" &&
                    new Date(d.dueAt).toISOString().slice(0, 10) ===
                      allocationDay,
                ).length < policy.maxDailyDeliveries,
            ),
            "AUTONOMY_CAPACITY_HOLD",
            "No authorised daily slot exists in the bounded allocation horizon.",
            409,
          );
          await this.options.reserve(campaign.id, at, policy.authority, {
            revision,
            preparation: job.id,
            digest: result.digest!,
          });
          policy.job = undefined;
          policy.nextRun = this.options.now() + 1000;
        }
      } else {
        const active = this.store
          .list<Delivery>("delivery:")
          .filter(
            (d) =>
              d.project === policy.project &&
              ["scheduled", "waiting_container", "executing"].includes(
                d.status,
              ),
          );
        if (
          policy.accounts.some(
            (a) =>
              active.filter((d) => d.account === a.alias).length <
              policy.stockFloor,
          )
        ) {
          const result = await this.beginRequest(policy, policy.authority);
          policy.job = result.id;
          policy.nextRun = this.options.now() + 1000;
        } else policy.nextRun = Math.min(...active.map((d) => d.dueAt + 1000));
      }
      if (
        this.store.get<StandingAuthority>("autonomy:" + policy.project)
          ?.revision === revision
      )
        this.store.put("autonomy:" + policy.project, policy);
    } catch (error) {
      if (
        this.store.get<StandingAuthority>("autonomy:" + policy.project)
          ?.revision !== revision
      )
        return;
      this.hold(policy, error);
    }
  }
}
