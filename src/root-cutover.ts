import { digest, json, requireValue } from "./common.ts";
import { credentialRoots, envelopeRoot, rootIdentifier, seal, unseal } from "./crypto.ts";
import { credentialInventory, type RotationSnapshot } from "./root-rotation-inventory.ts";
import type { SQLiteStore } from "./store.ts";
import type { Env } from "./types.ts";

export const ROOT_BATCH = 25;
type Action = { action: "inspect" | "migrate"; expectedDigest?: string };

export async function demandRootCutover(env: Env) {
  requireValue(env.DEPLOY_ENV === "staging" && env.ENCRYPTION_ROOT_WRITE === "next" &&
    env.ENCRYPTION_KEY_NEXT && env.ENCRYPTION_KEY_NEXT !== env.ENCRYPTION_KEY &&
    /^[a-f0-9]{40}$/.test(env.RELEASE_SHA),
    "ROOT_CUTOVER_UNCONFIGURED", "Root cutover requires the reviewed staging release and distinct protected roots with next-root writes enabled.", 409);
  return rootIdentifier(env.ENCRYPTION_KEY_NEXT);
}

/** Internal DO operation. No plaintext or encrypted replacement leaves the store. */
export async function cutoverWorkspace(store: Pick<SQLiteStore, "entries" | "get" | "put" | "tx">, env: Env, workspace: string, input: Action) {
  const targetRoot = await demandRootCutover(env);
  const records = store.entries("");
  requireValue(!records.length || store.get("workspace") === workspace,
    "ROOT_WORKSPACE_MISMATCH", "Credential inventory workspace does not match.", 409);
  const github = await env.IDENTITY.prepare(
    "SELECT workspace,installation_id,credential,credential_revision,refresh_lease FROM github_installations WHERE workspace=? ORDER BY installation_id LIMIT 201",
  ).bind(workspace).all<RotationSnapshot["githubInstallations"][number]>();
  requireValue(github.success && github.results.length <= 200,
    "ROOT_INVENTORY_LIMIT", "GitHub credential inventory exceeds the bounded operation.", 409);
  const snapshot: RotationSnapshot = { release: env.RELEASE_SHA, workspaceIds: [workspace],
    workspaces: [{ workspace, records }], githubInstallations: github.results,
    complete: { workspaces: true, records: true, githubInstallations: true } };
  const inventory = credentialInventory(snapshot);
  requireValue(inventory.length <= 200, "ROOT_VERIFY_LIMIT", "Credential inventory exceeds the bounded workspace operation.", 409);
  const inventoryDigest = await digest({ targetRoot, snapshot });
  if (input.action === "migrate") requireValue(input.expectedDigest === inventoryDigest,
    "ROOT_INVENTORY_CHANGED", "Credential inventory changed; inspect a fresh batch before continuing.", 409);
  const pending = inventory.filter(entry => envelopeRoot(entry.envelope) !== targetRoot);
  const selected = pending.slice(0, ROOT_BATCH);
  const roots = credentialRoots(env);
  const replacements: { id: string; context: string; envelope: string; replacement: string }[] = [];
  // Rehearse the entire selected batch before its first storage mutation.
  for (const entry of selected) {
    const plaintext = await unseal(entry.envelope, roots, entry.context);
    const replacement = await seal(plaintext, roots, entry.context, env.ENCRYPTION_KEY_VERSION || "2");
    requireValue(await digest(plaintext) === await digest(await unseal(replacement, roots, entry.context)),
      "ROOT_REWRAP_MISMATCH", "Credential rewrap did not preserve its content.", 409);
    let rejected = false;
    try { await unseal(replacement, env.ENCRYPTION_KEY, entry.context); } catch { rejected = true; }
    requireValue(rejected, "ROOT_OLD_KEY_ACCEPTED", "Old root still reads the replacement.", 409);
    replacements.push({ ...entry, replacement });
  }
  let changed = 0;
  if (input.action === "migrate") {
    // DO caller holds blockConcurrencyWhile. Compare full records as well so
    // this primitive fails closed if used without that fence in the future.
    store.tx(() => {
      requireValue(JSON.stringify(store.entries("")) === JSON.stringify(records),
        "ROOT_INVENTORY_CHANGED", "Workspace changed before credential commit.", 409);
      for (const entry of replacements.filter(e => !e.id.startsWith(workspace + "/github:"))) {
        const key = entry.id.slice(workspace.length + 1);
        const value = store.get<any>(key);
        requireValue(value?.secret === entry.envelope, "ROOT_INVENTORY_CHANGED", "Credential changed before commit.", 409);
        store.put(key, { ...value, secret: entry.replacement });
        changed++;
      }
    });
    for (const entry of replacements.filter(e => e.id.startsWith(workspace + "/github:"))) {
      const row = github.results.find(r => entry.id === workspace + "/github:" + r.installation_id)!;
      const result = await env.IDENTITY.prepare(
        "UPDATE github_installations SET credential=?,credential_revision=credential_revision+1 WHERE workspace=? AND installation_id=? AND credential_revision=? AND credential=? AND refresh_lease IS NULL",
      ).bind(entry.replacement, workspace, row.installation_id, row.credential_revision, row.credential).run();
      requireValue(result.meta.changes === 1, "ROOT_INVENTORY_CHANGED",
        "GitHub credential changed; committed batches remain readable. Inspect before resuming.", 409);
      changed++;
    }
    // Independently read committed ciphertext. Do not infer success from write acknowledgements.
    for (const entry of replacements) {
      const key = entry.id.slice(workspace.length + 1);
      const stored = key.startsWith("github:")
        ? (await env.IDENTITY.prepare("SELECT credential FROM github_installations WHERE workspace=? AND installation_id=?")
          .bind(workspace, Number(key.slice(7))).first<{ credential: string }>())?.credential
        : store.get<any>(key)?.secret;
      requireValue(stored === entry.replacement && envelopeRoot(stored) === targetRoot,
        "ROOT_READBACK_CHANGED", "Credential changed before cutover readback; inspect before resuming.", 409);
      await unseal(stored, roots, entry.context);
    }
    if (records.length) store.put("root-cutover:checkpoint", {
      release: env.RELEASE_SHA, targetRoot, priorInventoryDigest: inventoryDigest,
      changed, remaining: pending.length - changed, verifiedAt: Date.now(), oldRootRetained: true,
    });
  }
  // A zero-pending inventory still validates all retained credentials. Bound
  // verification to the documented workspace inventory ceiling.
  if (pending.length === 0) {
    for (const entry of inventory) await unseal(entry.envelope, roots, entry.context);
  }
  return { release: env.RELEASE_SHA, targetRoot, inventoryDigest,
    credentialCount: inventory.length, pending: pending.length - changed,
    rehearsed: replacements.length, changed, verifiedComplete: pending.length === 0,
    observedAt: Date.now(), oldRootRetained: true,
    boundary: "Current credential records only. Historical PITR snapshots still require the old protected root. This operation does not retire keys or prove provider grants." };
}

/** Narrow, temporary protected-workflow capability; never accepts credentials or arbitrary operations. */
export async function rootCutoverRoute(request: Request, env: Env) {
  requireValue(request.method === "POST" && !request.headers.has("cookie") && !request.headers.has("origin"),
    "ROOT_OPERATOR_REQUIRED", "A protected workflow is required.", 403);
  const supplied = request.headers.get("authorization") || "";
  const expires = Number(env.ROOT_ROTATION_EXPIRES_AT);
  requireValue(env.ROOT_ROTATION_TOKEN && /^[a-f0-9]{64}$/.test(env.ROOT_ROTATION_TOKEN) &&
    expires > Date.now() && expires <= Date.now() + 60 * 60 * 1000 &&
    await digest(supplied) === await digest("Bearer " + env.ROOT_ROTATION_TOKEN),
    "ROOT_OPERATOR_REQUIRED", "Root cutover capability is absent, expired or invalid.", 403);
  const targetRoot = await demandRootCutover(env);
  const text = await request.text();
  requireValue(text.length <= 2048, "ROOT_INPUT_INVALID", "Root request is too large.", 400);
  const input = JSON.parse(text);
  requireValue(input.release === env.RELEASE_SHA && ["list", "inspect", "migrate"].includes(input.action),
    "ROOT_RELEASE_MISMATCH", "Root cutover must target this exact deployed release.", 409);
  if (input.action === "list") {
    requireValue(typeof (input.cursor ?? "") === "string" && (input.cursor || "").length <= 200,
      "ROOT_INPUT_INVALID", "Invalid inventory cursor.");
    const rows = await env.IDENTITY.prepare("SELECT workspace FROM workspace_registry WHERE workspace>? ORDER BY workspace LIMIT 101")
      .bind(input.cursor || "").all<{ workspace: string }>();
    requireValue(rows.success, "ROOT_INVENTORY_FAILED", "Workspace enumeration failed.", 503);
    return json({ release: env.RELEASE_SHA, targetRoot, workspaces: rows.results.slice(0, 100).map(r => r.workspace),
      next: rows.results.length > 100 ? rows.results[99].workspace : null });
  }
  requireValue(typeof input.workspace === "string" && input.workspace.length <= 200 &&
    await env.IDENTITY.prepare("SELECT workspace FROM workspace_registry WHERE workspace=?").bind(input.workspace).first(),
    "ROOT_WORKSPACE_UNKNOWN", "Workspace is outside the durable inventory.", 409);
  const stub = env.WORKSPACES.get(env.WORKSPACES.idFromName(input.workspace));
  return stub.fetch(new Request("https://workspace/maintenance/root-cutover", { method: "POST",
    body: JSON.stringify({ workspace: input.workspace, release: env.RELEASE_SHA,
      input: { action: input.action, expectedDigest: input.expectedDigest } }) }));
}
