import { seal } from "./crypto.ts";
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
      restoredAccounts.map(async (account) => [
        account.alias,
        await seal(
          { accessToken: "recovery-invalidated", expiresAt: 0 },
          env.ENCRYPTION_KEY,
          workspace + ":" + account.alias,
          env.ENCRYPTION_KEY_VERSION,
        ),
      ] as const),
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

    for (const profile of store.list<Profile>("profile:")) {
      if (!profile.enabled) continue;
      profile.enabled = false;
      profile.revision++;
      profile.error = "RECOVERY_REAUTHORIZATION_REQUIRED";
      store.put("profile:" + profile.id, profile);
      profiles.push(profile.id);
    }

    for (const delivery of store.list<Delivery>("delivery:")) {
      if (!["scheduled", "waiting_container"].includes(delivery.status)) continue;
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
