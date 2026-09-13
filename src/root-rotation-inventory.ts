import { digest, requireValue } from "./common.ts";
import { seal } from "./crypto.ts";
import { rewrapCredentialEnvelope, verifyRewrappedEnvelope } from "./root-rotation.ts";

export interface CredentialInventoryEntry {
  id: string;
  context: string;
  envelope: string;
}
export interface RotationSnapshot {
  release: string;
  workspaceIds: string[];
  workspaces: { workspace: string; records: { key: string; value: any }[] }[];
  githubInstallations: {
    workspace: string; installation_id: number; credential: string;
    credential_revision: number; refresh_lease: string | null;
  }[];
  /** Set only after the exporter has exhausted every source page. */
  complete: { workspaces: boolean; records: boolean; githubInstallations: boolean };
}

export function credentialInventory(snapshot: RotationSnapshot): CredentialInventoryEntry[] {
  requireValue(/^[a-f0-9]{40}$/.test(snapshot.release) &&
    snapshot.complete.workspaces && snapshot.complete.records && snapshot.complete.githubInstallations,
    "ROTATION_INVENTORY_INCOMPLETE", "A complete, release-pinned inventory is required.", 409);
  const ids = new Set(snapshot.workspaceIds);
  requireValue(ids.size === snapshot.workspaceIds.length &&
    snapshot.workspaces.length === ids.size &&
    new Set(snapshot.workspaces.map((w) => w.workspace)).size === ids.size &&
    snapshot.workspaces.every((w) => ids.has(w.workspace)),
    "ROTATION_WORKSPACE_MISMATCH", "Every inventoried workspace must have exactly one full record snapshot.", 409);
  const entries: CredentialInventoryEntry[] = [];
  for (const workspace of snapshot.workspaces) {
    requireValue(new Set(workspace.records.map((r) => r.key)).size === workspace.records.length,
      "ROTATION_DUPLICATE_RECORD", "Duplicate workspace records are not allowed.", 409);
    for (const { key, value } of workspace.records) {
      if (key.startsWith("account:")) {
        requireValue(value.alias === key.slice(8) && typeof value.secret === "string",
          "ROTATION_RECORD_INVALID", "Invalid account credential record.", 409);
        if (value.secret) entries.push({ id: workspace.workspace + "/" + key,
          context: workspace.workspace + ":" + value.alias, envelope: value.secret });
      } else if (key.startsWith("oauth:") && value.secret) {
        requireValue(value.alias === key.slice(6) && typeof value.secret === "string",
          "ROTATION_RECORD_INVALID", "Invalid OAuth credential record.", 409);
        entries.push({ id: workspace.workspace + "/" + key,
          context: workspace.workspace + ":oauth:" + value.alias, envelope: value.secret });
      } else {
        // New credential-bearing record families must receive an explicit
        // authenticated context mapping before this tool can claim coverage.
        requireValue(!value?.secret && !value?.credential,
          "ROTATION_UNKNOWN_CREDENTIAL", "Unknown credential record family.", 409);
      }
    }
  }
  for (const row of snapshot.githubInstallations) {
    requireValue(ids.has(row.workspace) && Number.isSafeInteger(row.installation_id) &&
      row.installation_id > 0 && Number.isSafeInteger(row.credential_revision) &&
      row.credential_revision > 0 && !row.refresh_lease && typeof row.credential === "string",
      "ROTATION_GITHUB_INVALID", "GitHub inventory is incomplete or a credential refresh is in flight.", 409);
    entries.push({ id: row.workspace + "/github:" + row.installation_id,
      context: row.workspace + ":github:" + row.installation_id, envelope: row.credential });
  }
  requireValue(new Set(entries.map((e) => e.id)).size === entries.length,
    "ROTATION_DUPLICATE_CREDENTIAL", "Duplicate credentials in inventory.", 409);
  return entries.sort((a, b) => a.id.localeCompare(b.id));
}

export async function rehearseRootInventory(snapshot: RotationSnapshot,
  oldRoot: string, newRoot: string, targetVersion = "2") {
  requireValue(oldRoot !== newRoot, "ROTATION_SAME_ROOT", "The next root must differ.", 409);
  const inventory = credentialInventory(snapshot);
  // Validate both protected roots even when the inventory is empty.
  const canaryContext = "poststeward:root-inventory-rehearsal";
  const canary = await seal({ purpose: "root-rehearsal" }, oldRoot, canaryContext);
  const nextCanary = await rewrapCredentialEnvelope(canary, oldRoot, newRoot, canaryContext, targetVersion);
  await verifyRewrappedEnvelope(canary, nextCanary, oldRoot, newRoot, canaryContext);
  const replacements: { id: string; context: string; expectedDigest: string; replacement: string }[] = [];
  for (const entry of inventory) {
    const replacement = await rewrapCredentialEnvelope(entry.envelope, oldRoot, newRoot, entry.context, targetVersion);
    await verifyRewrappedEnvelope(entry.envelope, replacement, oldRoot, newRoot, entry.context);
    replacements.push({ id: entry.id, context: entry.context,
      expectedDigest: await digest(entry.envelope), replacement });
  }
  return {
    // Replacements are private encrypted migration material, not public evidence.
    replacements,
    evidence: { release: snapshot.release, inventoryDigest: await digest(snapshot),
      workspaceCount: snapshot.workspaceIds.length, credentialCount: inventory.length,
      verifiedCount: replacements.length, targetVersion,
      newRootReads: true, oldRootRejectedForRewrapped: true,
      liveInventoryIndependentlyVerified: false, liveCutoverPerformed: false,
      boundary: "Rehearsal of the supplied complete snapshot. Requires fresh live inventory comparison, protected staging, and rollout verification before root replacement." },
  };
}
