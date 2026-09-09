import type { Account, Delivery, Profile, Store } from "./types.ts";

export interface RecoveryLocalInvalidation {
  accounts: string[];
  profiles: string[];
  deliveries: string[];
  quotes: string[];
  billingReset: boolean;
}

/**
 * A point-in-time restore can resurrect locally stored credentials, automation
 * authority and billing state that were revoked or changed after the target
 * time. Invalidate those capabilities inside the restored Durable Object before
 * the owner may clear global recovery quarantine. Content/history remain intact.
 *
 * This function is deliberately idempotent: repeated reconciliation probes do
 * not keep bumping versions or rewrite already-terminal receipts.
 */
export function invalidateRestoredAuthority(
  store: Store,
  now = Date.now(),
): RecoveryLocalInvalidation {
  const accounts: string[] = [];
  const profiles: string[] = [];
  const deliveries: string[] = [];
  const quotes: string[] = [];

  store.tx(() => {
    for (const account of store.list<Account>("account:")) {
      store.delete("oauth:" + account.alias);
      if (!account.active) continue;
      account.active = false;
      account.version++;
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

    // External payment state is not rolled back with this Durable Object. Never
    // trust a restored local entitlement, checkout attempt or customer binding.
    store.delete("entitlement");
    store.delete("billing:attempt");
    store.delete("billing:customer");
    store.delete("billing:renewing");
    store.delete("billing:next");
    store.put("recovery:authority-invalidated", {
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
    billingReset: true,
  };
}
