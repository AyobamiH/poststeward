import { requireValue } from "./common.ts";
import type { Store } from "./types.ts";

/** Enforced by the canonical engine after schema/scope checks, before effects. */
export function guardControlledPublication(store: Store, name: string, input: { campaign?: string; delivery?: string }) {
  if (!["publish_now", "schedule_create", "schedule_replace"].includes(name)) return;
  const pilot = store.get<{ id: string; deliveryId?: string }>("pilot:first");
  if (!pilot?.deliveryId) return;
  requireValue(input.campaign !== pilot.id && input.delivery !== pilot.deliveryId,
    "CONTROLLED_PUBLICATION", "This campaign/delivery belongs to the one-shot owner acceptance. Inspect, cancel or read back its existing receipt; do not republish or replace it.", 409);
}
