import { credentialRoots, seal } from "./crypto.ts";
import type { Account, Delivery, Env, Profile, Store } from "./types.ts";

export interface RecoveryLocalInvalidation {
  accounts: string[];
  profiles: string[];
  deliveries: string[];
  quotes: string[];
  billingReset: boolean;
}

const marker = "recovery:authority-invalidated";

/**
 * A point-in-time restore can resurrect locally stored credentials, automation
 * authority and billing state that were revoked or changed after the target
 * time. Invalidate those capabilities inside the restored Durable Object before
 * the owner may clear global recovery quarantine. Content/history remain intact.
 *
 * The marker is part of the restored Durable Object state, so a restored snapshot
 * gets exactly one invalidation pass. Repeated reconciliation probes in that same
 * snapshot do not keep bumping versions or rewriting terminal receipts.
 */
export async function invalidateRestoredAuthority(
  store: Store,
  env: Env,
  workspace: string,
  now = Date.now(),
): Promise<RecoveryLocalInvalidation> {
  if (store.get(marker))
    return {
      accounts: [],
      profiles: [],
      deliveries: [],
      quotes: [],
      billingReset: false,
    };

  const restoredAccounts = store.list<Account>("account:");
  const tombstones = new Map(
    await Promise.all(
      restoredAccounts.map(
        async (account) =>
          [
            account.alias,
            await seal(
              { accessToken: "recovery-invalidated", expiresAt: 0 },
              credentialRoots(env),
              workspace + ":" + account.alias,
              env.ENCRYPTION_KEY_VERSION,
            ),
          ] as const,
      ),
    ),
  );
  const accounts: string[] = [];
  const profiles: string[] = [];
  const deliveries: string[] = [];
  const quotes: string[] = [];
  let performed = false;

  store.tx(() => {
    if (store.get(marker)) return;
    performed = true;
    // Recovery must not resurrect a disconnected workspace model key or an
    // earlier spend consent. Content remains reviewable; reconnect explicitly.
    const model = store.get<any>("model:openai");
    if (model)
      store.put("model:openai", {
        ...model,
        secret: "",
        revision: (model.revision || 0) + 1,
        verifiedAt: undefined,
      });
    for (const job of store.list<any>("preparation:")) {
      if (!["queued", "running", "approved"].includes(job.status)) continue;
      // Release only unattempted allowance. The independent model-attempt ledger
      // retains in-flight/uncertain costs even if restored authority is fenced.
      if (job.budget?.reservedMicros) {
        const usageKey = "preparation-usage:" + job.budget.day;
        const usage = store.get<any>(usageKey);
        if (usage)
          store.put(usageKey, {
            ...usage,
            reservedMicros: Math.max(
              0,
              (usage.reservedMicros || 0) - job.budget.reservedMicros,
            ),
          });
        job.budget = { ...job.budget, reservedMicros: 0 };
      }
      store.put("preparation:" + job.id, {
        ...job,
        status: "failed",
        claim: undefined,
        claimUntil: undefined,
        error: {
          code: "RECOVERY_REAUTHORIZATION_REQUIRED",
          message:
            "Recovery invalidated preparation authority. Reconnect and review explicitly.",
        },
      });
    }

    for (const restored of restoredAccounts) {
      const account = store.get<Account>("account:" + restored.alias);
      if (!account) continue;
      store.delete("oauth:" + account.alias);
      if (account.active) account.version++;
      account.active = false;
      account.secret = tombstones.get(account.alias)!;
      account.verifiedAt = now;
      store.put("account:" + account.alias, account);
      accounts.push(account.alias);
    }

    for (const policy of store.list<any>("autonomy:")) {
      policy.enabled = false;
      policy.revision++;
      policy.error = "RECOVERY_REAUTHORIZATION_REQUIRED";
      store.put("autonomy:" + policy.project, policy);
    }

    for (const profile of store.list<Profile>("profile:")) {
      if (!profile.enabled) continue;
      profile.enabled = false;
      profile.revision++;
      profile.error = "RECOVERY_REAUTHORIZATION_REQUIRED";
      store.put("profile:" + profile.id, profile);
      profiles.push(profile.id);
    }

    for (const delivery of store.list<Delivery>("delivery:")) {
      if (!["scheduled", "waiting_container"].includes(delivery.status))
        continue;
      delivery.status = "drift_blocked";
      delivery.reason =
        "Workspace recovery invalidated captured provider authority. Reconnect and create a fresh reviewed schedule.";
      delivery.updatedAt = now;
      store.put("delivery:" + delivery.id, delivery);
      deliveries.push(delivery.id);
    }

    for (const quote of store.list<{ id?: string }>("quote:")) {
      if (!quote.id) continue;
      store.delete("quote:" + quote.id);
      quotes.push(quote.id);
    }

    // Stripe is the provider of record. Never trust entitlement, checkout or
    // customer state resurrected from the Durable Object's historical snapshot.
    // Advanced therefore stays fail-closed until current Stripe evidence is
    // reconciled again; Free controls remain available.
    store.delete("entitlement");
    store.delete("billing:attempt");
    store.delete("billing:customer");
    store.delete("billing:renewing");
    store.delete("billing:next");
    store.delete("billing:recovery-complete");
    store.put(marker, {
      at: now,
      accounts: accounts.sort(),
      profiles: profiles.sort(),
      deliveries: deliveries.sort(),
    });
  });

  return {
    accounts: accounts.sort(),
    profiles: profiles.sort(),
    deliveries: deliveries.sort(),
    quotes: quotes.sort(),
    billingReset: performed,
  };
}
