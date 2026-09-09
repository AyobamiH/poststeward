import { z } from "zod";
import { digest, Fault, requireValue, uid } from "./common.ts";
import { unseal } from "./crypto.ts";
import { Engine } from "./engine.ts";
import { demandFreshOwner, type OwnerAuthority, type OwnerProof } from "./owner-proof.ts";
import { validateText, type Credential, type ProviderAPI } from "./providers.ts";
import type { Account, Actor, Campaign, Delivery, Env, Store } from "./types.ts";

const slot = "pilot:first";
const prepareInput = z.strictObject({ alias: z.string().regex(/^[a-zA-Z0-9_-]{1,100}$/), text: z.string().min(1).max(3000) });
const confirmInput = z.strictObject({ reviewId: z.uuid(), reviewDigest: z.string().regex(/^[a-f0-9]{64}$/), approve: z.literal(true) });
const emptyInput = z.strictObject({});
interface Observation { at: number; verified: boolean; outcome: string; url?: string }
export interface PilotRecord {
  id: string;
  state: "prepared" | "reserved";
  owner: OwnerProof;
  release: string;
  createdAt: number;
  expiresAt: number;
  account: Pick<Account, "alias" | "provider" | "identity" | "version">;
  text: string;
  textDigest: string;
  campaignDigest: string;
  fingerprint: string;
  reviewDigest: string;
  approvedAt?: number;
  deliveryId?: string;
  observations: Observation[];
  firstVerified?: Observation;
  nextReadbackAt?: number;
  readbackAttempts: number;
}
const reviewFields = (r: Omit<PilotRecord, "reviewDigest"> | PilotRecord) => ({
  id: r.id, owner: r.owner, release: r.release, account: r.account,
  text: r.text, textDigest: r.textDigest, campaignDigest: r.campaignDigest,
  fingerprint: r.fingerprint, createdAt: r.createdAt, expiresAt: r.expiresAt,
  delaySeconds: 30,
});

export class Pilot {
  private now: () => number;
  constructor(private store: Store, private env: Env, private engine: Engine,
    private providers: ProviderAPI,
    private authorized: (actor: Actor) => Promise<boolean>, now = Date.now) { this.now = now; }

  private checkOwner(actor: Actor, authority: OwnerAuthority) {
    requireValue(!actor.grant && actor.scopes.includes("admin") && authority?.sessionHash &&
      authority.proof.workspace === actor.workspace && authority.proof.subject === actor.id &&
      this.store.get("workspace") === actor.workspace,
      "OWNER_SESSION_REQUIRED", "This acceptance is bound to the authenticated workspace owner.", 403);
  }
  private account(alias: string) {
    const account = this.store.get<Account>("account:" + alias);
    requireValue(account?.active, "CONNECTION_INACTIVE", "Connect this account before preparing a publication.", 409);
    return account;
  }
  private sameAccount(current: Account, expected: PilotRecord["account"]) {
    requireValue(current.version === expected.version && current.provider === expected.provider && current.identity.id === expected.identity.id,
      "ACCOUNT_DRIFT", "The reviewed account binding changed. Prepare a new review; nothing new was published.", 409);
  }
  private credential(account: Account) {
    return unseal<Credential>(account.secret, this.env.ENCRYPTION_KEY, this.store.get<string>("workspace") + ":" + account.alias);
  }
  status() {
    const record = this.store.get<PilotRecord>(slot);
    const delivery = record?.deliveryId ? this.store.get<Delivery>("delivery:" + record.deliveryId) : undefined;
    return { record: record || null, delivery: delivery ? this.engine.publicDelivery(delivery) : null,
      completed: !!record?.firstVerified,
      reviewExpired: !!record && record.state === "prepared" && record.expiresAt <= this.now(),
      limits: { oneControlledDelivery: true, reviewMinutes: 10, freshSigninMinutes: 15, cancellationWindowSeconds: 30, maxReadbackAttempts: 8 },
      notVerified: ["public launch", "provider OAuth refresh", "native browser WebMCP", "payments", "disaster recovery"] };
  }
  async run(action: string, input: unknown, actor: Actor, authority: OwnerAuthority) {
    this.checkOwner(actor, authority);
    if (action === "status") { this.parse(emptyInput, input); return this.status(); }
    if (action === "prepare") return this.prepare(this.parse(prepareInput, input), actor, authority);
    if (action === "confirm") return this.confirm(this.parse(confirmInput, input), actor, authority);
    if (action === "recheck") { this.parse(emptyInput, input); return this.recheck(); }
    if (action === "cancel") { this.parse(emptyInput, input); return this.cancel(actor); }
    throw new Fault("NOT_FOUND", "Unknown owner acceptance operation.", 404);
  }
  private parse<T>(schema: z.ZodType<T>, input: unknown): T {
    const result = schema.safeParse(input);
    requireValue(result.success, "INVALID_INPUT", "Acceptance inputs do not match the documented form.");
    return result.data;
  }
  private async prepare(input: z.infer<typeof prepareInput>, actor: Actor, authority: OwnerAuthority) {
    demandFreshOwner(authority, this.now());
    requireValue(!this.store.get<PilotRecord>(slot)?.deliveryId, "PILOT_CONSUMED", "This workspace already reserved its controlled publication. Inspect the existing receipt; do not publish again.", 409);
    requireValue(!this.engine.paused(), "PUBLISHING_PAUSED", "Publishing is paused. No preview or publication was created.", 409);
    const account = this.account(input.alias);
    requireValue(account.provider === "threads" || account.provider === "x", "READBACK_UNSUPPORTED", "Choose X or Threads for this verified-publication milestone. LinkedIn member readback is not implemented.", 409);
    validateText(account.provider, input.text);
    const liveIdentity = await this.providers.identity(account.provider, await this.credential(account));
    requireValue(liveIdentity.id === account.identity.id, "ACCOUNT_DRIFT", "The provider identity changed. Reconnect before preparing a review.", 409);
    const base = {
      id: uid(), state: "prepared" as const, owner: authority.proof, release: this.env.RELEASE_SHA,
      createdAt: this.now(), expiresAt: this.now() + 10 * 60000,
      account: { alias: account.alias, provider: account.provider, identity: liveIdentity, version: account.version },
      text: input.text, textDigest: await digest(input.text),
      campaignDigest: await digest({ [account.alias]: input.text }),
      fingerprint: await digest({ provider: account.provider, identity: liveIdentity.id, text: input.text }),
      observations: [], readbackAttempts: 0,
    };
    const record: PilotRecord = { ...base, reviewDigest: await digest(reviewFields(base)) };
    this.store.tx(() => {
      demandFreshOwner(authority, this.now());
      requireValue(!this.store.get<PilotRecord>(slot)?.deliveryId, "PILOT_CONSUMED", "A controlled delivery already exists.", 409);
      this.sameAccount(this.account(input.alias), record.account);
      this.store.put(slot, record);
    });
    return this.status();
  }
  private async confirm(input: z.infer<typeof confirmInput>, actor: Actor, authority: OwnerAuthority) {
    const record = this.store.get<PilotRecord>(slot);
    requireValue(record && record.id === input.reviewId && record.reviewDigest === input.reviewDigest,
      "REVIEW_CHANGED", "The review changed or is missing. Inspect the current exact preview.", 409);
    // Recovery of an already committed reservation only re-arms its existing alarm.
    if (record.deliveryId) { await this.engine.scheduleNext(); return this.status(); }
    demandFreshOwner(authority, this.now());
    requireValue(record.owner.id === authority.proof.id && record.expiresAt > this.now() && record.release === this.env.RELEASE_SHA,
      "REVIEW_EXPIRED", "The sign-in, runtime revision or review expired. Prepare a new review.", 409);
    requireValue(record.reviewDigest === await digest(reviewFields(record)), "REVIEW_INTEGRITY", "Stored review integrity failed.", 409);
    const scopedActor: Actor = { ...actor, ownerSession: authority.sessionHash };
    requireValue(await this.authorized(scopedActor), "OWNER_SIGNIN_REQUIRED", "Owner session is no longer authorised.", 409);
    const at = this.now();
    const delivery: Delivery = {
      id: uid(), fingerprint: record.fingerprint, campaign: record.id, project: "pilot-" + record.id,
      account: record.account.alias, provider: record.account.provider, identity: record.account.identity,
      binding: record.account.version, text: record.text, digest: record.textDigest,
      dueAt: at + 30000, timezone: "UTC", status: "scheduled", createdAt: at, updatedAt: at,
      actor: scopedActor, automatic: false, reviewedRelease: record.release,
    };
    this.engine.reserveReviewed(delivery, () => {
      const latest = this.store.get<PilotRecord>(slot);
      requireValue(latest?.id === record.id && latest.reviewDigest === record.reviewDigest && !latest.deliveryId,
        "REVIEW_CHANGED", "Another request changed or reserved this review. Inspect the existing receipt.", 409);
      demandFreshOwner(authority, this.now());
      requireValue(latest.expiresAt > this.now(), "REVIEW_EXPIRED", "Prepare a new review.", 409);
      const campaign: Campaign = { id: record.id, project: delivery.project,
        text: { [delivery.account]: delivery.text }, digest: record.campaignDigest, createdAt: at };
      this.store.put("project:" + delivery.project, { id: delivery.project, name: "Controlled publication", accounts: [delivery.account] });
      this.store.put("campaign:" + campaign.id, campaign);
      this.store.put(slot, { ...latest, state: "reserved", approvedAt: at, deliveryId: delivery.id });
    });
    await this.engine.scheduleNext();
    return this.status();
  }
  private async cancel(actor: Actor) {
    const record = this.store.get<PilotRecord>(slot);
    requireValue(record?.deliveryId, "NO_DELIVERY", "There is no reserved delivery to cancel.", 409);
    const cancellation = await this.engine.run("schedule_cancel", {
      delivery: record.deliveryId, idempotencyKey: "pilot-cancel-" + record.id,
    }, actor);
    return { ...this.status(), cancellation };
  }
  private async recheck() {
    const record = this.store.get<PilotRecord>(slot);
    const delivery = record?.deliveryId ? this.store.get<Delivery>("delivery:" + record.deliveryId) : undefined;
    requireValue(record && delivery?.postId && ["published_verified", "published_unverified"].includes(delivery.status),
      "NO_READBACK_TARGET", "No recorded publication ID is ready for verification. Never resubmit a publication to obtain one.", 409);
    const attempt = this.store.tx(() => {
      const current = this.store.get<PilotRecord>(slot)!;
      requireValue(current.readbackAttempts < 8 && (current.nextReadbackAt || 0) <= this.now(),
        "READBACK_LIMIT", "Readback is bounded to eight attempts, at least thirty seconds apart. Inspect the existing receipt.", 429);
      current.readbackAttempts++;
      // Longer than the bounded provider request; another recheck cannot overlap it.
      current.nextReadbackAt = this.now() + 30000;
      this.store.put(slot, current);
      return current.readbackAttempts;
    });
    let observation: Observation;
    try {
      const account = this.account(delivery.account);
      this.sameAccount(account, record.account);
      const evidence = await this.providers.verify(delivery, await this.credential(account));
      this.sameAccount(this.account(delivery.account), record.account);
      observation = { at: this.now(), verified: evidence.verified,
        outcome: evidence.verified ? "EXACT_PROVIDER_READBACK" : "READBACK_NOT_MATCHED", ...(evidence.url ? { url: evidence.url } : {}) };
    } catch (error) {
      observation = { at: this.now(), verified: false,
        outcome: error instanceof Fault ? error.code : "READBACK_UNAVAILABLE" };
    }
    this.store.tx(() => {
      const current = this.store.get<PilotRecord>(slot)!;
      requireValue(current.id === record.id && current.deliveryId === delivery.id && current.readbackAttempts === attempt,
        "READBACK_CHANGED", "Readback observation no longer belongs to the active attempt.", 409);
      current.observations.push(observation);
      if (observation.verified && !current.firstVerified) current.firstVerified = observation;
      this.store.put(slot, current);
    });
    return this.status();
  }
}
