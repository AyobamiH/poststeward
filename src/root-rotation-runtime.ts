import { Fault, requireValue } from "./common.ts";
import { envelopeVersion, unseal } from "./crypto.ts";
import {
  rewrapCredentialEnvelope,
  verifyRewrappedEnvelope,
} from "./root-rotation.ts";
import type { Account, Env, Store } from "./types.ts";

const BATCH_SIZE = 25;
const rotationIdPattern = /^[a-z0-9][a-z0-9._-]{7,79}$/;
const versionPattern = /^[1-9][0-9]{0,5}$/;

type RotationMode = "off" | "rewrap" | "verify";
type OAuthEnvelope = { alias: string; secret?: string };
type RotationConfig = {
  id: string;
  mode: Exclude<RotationMode, "off">;
  currentRoot: string;
  nextRoot?: string;
  targetVersion: string;
};
type EnvelopeRotation = {
  next: string;
  changed: boolean;
};
type RunRow = {
  rotation_id: string;
  source_release: string;
  target_version: string;
  status: "active" | "ready_for_cutover" | "verifying" | "completed";
  workspace_total: number;
  workspace_completed: number;
  workspace_credentials: number;
  github_total: number;
  github_completed: number;
  github_credentials: number;
};

function rootIsValid(value: string | undefined) {
  if (!value) return false;
  try {
    const decoded = Uint8Array.from(atob(value), (character) =>
      character.charCodeAt(0),
    );
    return decoded.length === 32 && btoa(String.fromCharCode(...decoded)) === value;
  } catch {
    return false;
  }
}

export function rootRotationMode(env: Env): RotationMode | "invalid" {
  const value = env.ROOT_ROTATION_MODE || "off";
  return ["off", "rewrap", "verify"].includes(value)
    ? (value as RotationMode)
    : "invalid";
}

/**
 * Any non-off value is maintenance. An invalid deployment therefore fails
 * closed instead of accidentally re-enabling credential use.
 */
export function rootRotationMaintenance(env: Env) {
  return rootRotationMode(env) !== "off";
}

function configuration(env: Env): RotationConfig {
  const mode = rootRotationMode(env);
  requireValue(
    mode === "rewrap" || mode === "verify",
    "ROOT_ROTATION_MODE_INVALID",
    "Root rotation mode must be rewrap or verify while maintenance is active.",
    503,
  );
  requireValue(
    env.DEPLOY_ENV === "staging",
    "ROOT_ROTATION_ENVIRONMENT_INVALID",
    "Root-secret acceptance is restricted to staging.",
    503,
  );
  requireValue(
    env.PUBLISHING_PAUSED === "true",
    "ROOT_ROTATION_NOT_PAUSED",
    "Publishing must remain paused throughout root-secret rotation.",
    503,
  );
  const id = env.ROOT_ROTATION_ID || "";
  requireValue(
    rotationIdPattern.test(id),
    "ROOT_ROTATION_ID_INVALID",
    "Root rotation requires a reviewed non-secret rotation identifier.",
    503,
  );
  const targetVersion = env.ENCRYPTION_KEY_VERSION || "2";
  requireValue(
    versionPattern.test(targetVersion) && Number(targetVersion) >= 2,
    "ROOT_ROTATION_VERSION_INVALID",
    "Root rotation cannot downgrade the credential envelope key schedule.",
    503,
  );
  requireValue(
    rootIsValid(env.ENCRYPTION_KEY),
    "ROOT_ROTATION_CURRENT_ROOT_INVALID",
    "The current credential root is unavailable or invalid.",
    503,
  );
  if (mode === "rewrap") {
    requireValue(
      rootIsValid(env.ENCRYPTION_KEY_NEXT) &&
        env.ENCRYPTION_KEY_NEXT !== env.ENCRYPTION_KEY,
      "ROOT_ROTATION_NEXT_ROOT_INVALID",
      "Rewrap mode requires a distinct protected 32-byte next root.",
      503,
    );
    return {
      id,
      mode,
      currentRoot: env.ENCRYPTION_KEY,
      nextRoot: env.ENCRYPTION_KEY_NEXT,
      targetVersion,
    };
  }
  requireValue(
    !env.ENCRYPTION_KEY_NEXT,
    "ROOT_ROTATION_OLD_ROOT_STILL_PRESENT",
    "Verify mode requires the next-root slot to be removed after protected cutover.",
    503,
  );
  return {
    id,
    mode,
    currentRoot: env.ENCRYPTION_KEY,
    targetVersion,
  };
}

async function canRead<T>(envelope: string, root: string, context: string) {
  try {
    await unseal<T>(envelope, root, context);
    return true;
  } catch {
    return false;
  }
}

export async function rotateCredentialEnvelope<T>(
  envelope: string,
  oldRoot: string,
  newRoot: string,
  context: string,
  targetVersion = "2",
): Promise<EnvelopeRotation> {
  requireValue(
    oldRoot !== newRoot,
    "ROOT_ROTATION_ROOT_REUSED",
    "Root-secret rotation requires distinct roots.",
    503,
  );
  const [oldReads, newReads] = await Promise.all([
    canRead<T>(envelope, oldRoot, context),
    canRead<T>(envelope, newRoot, context),
  ]);
  requireValue(
    oldReads !== newReads,
    "ROOT_ROTATION_ENVELOPE_AMBIGUOUS",
    "A protected credential is unreadable by exactly one expected root.",
    503,
  );
  if (newReads) {
    requireValue(
      envelopeVersion(envelope) === targetVersion,
      "ROOT_ROTATION_VERSION_MISMATCH",
      "An already rewrapped credential has the wrong envelope version.",
      503,
    );
    return { next: envelope, changed: false };
  }
  const rewrapped = await rewrapCredentialEnvelope<T>(
    envelope,
    oldRoot,
    newRoot,
    context,
    targetVersion,
  );
  await verifyRewrappedEnvelope<T>(
    envelope,
    rewrapped,
    oldRoot,
    newRoot,
    context,
  );
  requireValue(
    envelopeVersion(rewrapped) === targetVersion,
    "ROOT_ROTATION_VERSION_MISMATCH",
    "Rewrapped credential has the wrong envelope version.",
    503,
  );
  return { next: rewrapped, changed: true };
}

export async function rewrapWorkspaceCredentials(
  store: Store,
  env: Env,
  workspace: string,
) {
  const c = configuration(env);
  requireValue(
    c.mode === "rewrap" && c.nextRoot,
    "ROOT_ROTATION_MODE_INVALID",
    "Workspace rewrap is available only during rewrap mode.",
    503,
  );
  const accounts = store.list<Account>("account:");
  const oauth = store
    .list<OAuthEnvelope>("oauth:")
    .filter((meta) => typeof meta.secret === "string");
  const updates: Array<{
    key: string;
    original: string;
    next: string;
    changed: boolean;
  }> = [];

  for (const account of accounts) {
    requireValue(
      /^[A-Za-z0-9_-]{1,100}$/.test(account.alias) &&
        typeof account.secret === "string",
      "ROOT_ROTATION_CREDENTIAL_INVALID",
      "Workspace account credential inventory is malformed.",
      503,
    );
    const rotated = await rotateCredentialEnvelope(
      account.secret,
      c.currentRoot,
      c.nextRoot,
      `${workspace}:${account.alias}`,
      c.targetVersion,
    );
    updates.push({
      key: `account:${account.alias}`,
      original: account.secret,
      ...rotated,
    });
  }
  for (const meta of oauth) {
    requireValue(
      /^[A-Za-z0-9_-]{1,100}$/.test(meta.alias) &&
        typeof meta.secret === "string",
      "ROOT_ROTATION_CREDENTIAL_INVALID",
      "Workspace OAuth credential inventory is malformed.",
      503,
    );
    const rotated = await rotateCredentialEnvelope(
      meta.secret,
      c.currentRoot,
      c.nextRoot,
      `${workspace}:oauth:${meta.alias}`,
      c.targetVersion,
    );
    updates.push({
      key: `oauth:${meta.alias}`,
      original: meta.secret,
      ...rotated,
    });
  }

  store.tx(() => {
    for (const update of updates) {
      if (!update.changed) continue;
      const current = store.get<{ secret?: string }>(update.key);
      requireValue(
        current?.secret === update.original,
        "ROOT_ROTATION_CONCURRENT_MUTATION",
        "Protected workspace credentials changed during rewrap. Retry after maintenance settles.",
        503,
      );
      store.put(update.key, { ...current, secret: update.next });
    }
  });

  for (const update of updates) {
    const current = store.get<{ secret?: string }>(update.key);
    requireValue(
      current?.secret === update.next &&
        (await canRead(current.secret, c.nextRoot, update.key.startsWith("oauth:")
          ? `${workspace}:oauth:${update.key.slice("oauth:".length)}`
          : `${workspace}:${update.key.slice("account:".length)}`)) &&
        !(await canRead(current.secret, c.currentRoot, update.key.startsWith("oauth:")
          ? `${workspace}:oauth:${update.key.slice("oauth:".length)}`
          : `${workspace}:${update.key.slice("account:".length)}`)),
      "ROOT_ROTATION_POSTWRITE_VERIFY_FAILED",
      "A rewrapped workspace credential failed root separation verification.",
      503,
    );
  }

  return {
    credentialCount: updates.length,
    changedCount: updates.filter((update) => update.changed).length,
  };
}

export async function verifyWorkspaceCredentials(
  store: Store,
  env: Env,
  workspace: string,
) {
  const c = configuration(env);
  requireValue(
    c.mode === "verify",
    "ROOT_ROTATION_MODE_INVALID",
    "Workspace credential verification is available only after protected cutover.",
    503,
  );
  const inventory = [
    ...store.list<Account>("account:").map((account) => ({
      envelope: account.secret,
      context: `${workspace}:${account.alias}`,
    })),
    ...store
      .list<OAuthEnvelope>("oauth:")
      .filter((meta) => typeof meta.secret === "string")
      .map((meta) => ({
        envelope: meta.secret!,
        context: `${workspace}:oauth:${meta.alias}`,
      })),
  ];
  for (const item of inventory) {
    requireValue(
      envelopeVersion(item.envelope) === c.targetVersion &&
        (await canRead(item.envelope, c.currentRoot, item.context)),
      "ROOT_ROTATION_CUTOVER_VERIFY_FAILED",
      "A protected workspace credential is not readable after root cutover.",
      503,
    );
  }
  return { credentialCount: inventory.length };
}

async function internalJson(response: Response) {
  const value = (await response.json().catch(() => ({}))) as any;
  if (!response.ok)
    throw new Fault(
      value.error?.code || "ROOT_ROTATION_INTERNAL_FAILED",
      value.error?.message || "Root rotation workspace operation failed.",
      response.status,
    );
  return value;
}

async function ensureRun(env: Env, c: RotationConfig, now: number) {
  let run = await env.IDENTITY.prepare(
    "SELECT rotation_id,source_release,target_version,status,workspace_total,workspace_completed,workspace_credentials,github_total,github_completed,github_credentials FROM root_rotation_runs WHERE rotation_id=?",
  )
    .bind(c.id)
    .first<RunRow>();
  if (!run) {
    requireValue(
      c.mode === "rewrap",
      "ROOT_ROTATION_RUN_MISSING",
      "Root rotation must begin in rewrap mode before cutover verification.",
      503,
    );
    await env.IDENTITY.prepare(
      "INSERT INTO root_rotation_runs(rotation_id,source_release,target_version,status,started_at,updated_at) VALUES (?,?,?,'active',?,?)",
    )
      .bind(c.id, env.RELEASE_SHA, c.targetVersion, now, now)
      .run();
    run = await env.IDENTITY.prepare(
      "SELECT rotation_id,source_release,target_version,status,workspace_total,workspace_completed,workspace_credentials,github_total,github_completed,github_credentials FROM root_rotation_runs WHERE rotation_id=?",
    )
      .bind(c.id)
      .first<RunRow>();
  }
  requireValue(
    run &&
      run.source_release === env.RELEASE_SHA &&
      run.target_version === c.targetVersion,
    "ROOT_ROTATION_RUN_DRIFT",
    "Root rotation release or target version changed mid-run. Review before continuing.",
    503,
  );
  requireValue(
    run.status !== "completed" || c.mode === "verify",
    "ROOT_ROTATION_ID_REUSED",
    "A completed root rotation identifier cannot start another rotation.",
    503,
  );
  return run;
}

async function snapshotInventory(env: Env, c: RotationConfig, now: number) {
  await env.IDENTITY.batch([
    env.IDENTITY.prepare(
      "INSERT OR IGNORE INTO root_rotation_workspaces(rotation_id,workspace,status,credential_count,updated_at) SELECT ?,workspace,'pending',0,? FROM principals",
    ).bind(c.id, now),
    env.IDENTITY.prepare(
      "INSERT OR IGNORE INTO root_rotation_workspaces(rotation_id,workspace,status,credential_count,updated_at) SELECT ?,workspace,'pending',0,? FROM github_installations",
    ).bind(c.id, now),
    env.IDENTITY.prepare(
      "INSERT OR IGNORE INTO root_rotation_github(rotation_id,workspace,installation_id,status,credential_count,credential_revision,updated_at) SELECT ?,workspace,installation_id,'pending',0,credential_revision,? FROM github_installations",
    ).bind(c.id, now),
  ]);
}

async function workspaceBatch(
  env: Env,
  c: RotationConfig,
  status: "pending" | "rewrapped",
  nextStatus: "rewrapped" | "verified",
  route: "/root-rotation/rewrap" | "/root-rotation/verify",
  now: number,
) {
  const rows = await env.IDENTITY.prepare(
    "SELECT workspace FROM root_rotation_workspaces WHERE rotation_id=? AND status=? ORDER BY workspace LIMIT ?",
  )
    .bind(c.id, status, BATCH_SIZE)
    .all<{ workspace: string }>();
  let processed = 0;
  let credentials = 0;
  for (const row of rows.results || []) {
    const response = await env.WORKSPACES.get(
      env.WORKSPACES.idFromName(row.workspace),
    ).fetch(`https://workspace.internal${route}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace: row.workspace,
        input: { rotationId: c.id },
      }),
    });
    const value = await internalJson(response);
    const count = Number(value.credentialCount);
    requireValue(
      Number.isSafeInteger(count) && count >= 0,
      "ROOT_ROTATION_INTERNAL_INVALID",
      "Root rotation workspace returned invalid non-secret evidence.",
      503,
    );
    const updated = await env.IDENTITY.prepare(
      "UPDATE root_rotation_workspaces SET status=?,credential_count=?,updated_at=? WHERE rotation_id=? AND workspace=? AND status=?",
    )
      .bind(nextStatus, count, now, c.id, row.workspace, status)
      .run();
    requireValue(
      updated.meta.changes === 1,
      "ROOT_ROTATION_CHECKPOINT_RACE",
      "Root rotation workspace checkpoint changed concurrently.",
      503,
    );
    processed++;
    credentials += count;
  }
  return { processed, credentials };
}

async function rewrapGitHubBatch(env: Env, c: RotationConfig, now: number) {
  requireValue(c.nextRoot, "ROOT_ROTATION_NEXT_ROOT_INVALID", "Next root is required.", 503);
  const rows = await env.IDENTITY.prepare(
    "SELECT g.workspace,g.installation_id,i.credential,i.credential_revision,i.refresh_lease FROM root_rotation_github g LEFT JOIN github_installations i ON i.workspace=g.workspace AND i.installation_id=g.installation_id WHERE g.rotation_id=? AND g.status='pending' ORDER BY g.workspace,g.installation_id LIMIT ?",
  )
    .bind(c.id, BATCH_SIZE)
    .all<any>();
  let processed = 0;
  let credentials = 0;
  for (const row of rows.results || []) {
    if (!row.credential) {
      await env.IDENTITY.prepare(
        "UPDATE root_rotation_github SET status='rewrapped',credential_count=0,credential_revision=NULL,updated_at=? WHERE rotation_id=? AND workspace=? AND installation_id=? AND status='pending'",
      )
        .bind(now, c.id, row.workspace, row.installation_id)
        .run();
      processed++;
      continue;
    }
    requireValue(
      row.refresh_lease == null,
      "ROOT_ROTATION_GITHUB_REFRESH_ACTIVE",
      "A GitHub credential refresh is still leased. Retry after the lease settles.",
      503,
    );
    const context = `${row.workspace}:github:${row.installation_id}`;
    const rotated = await rotateCredentialEnvelope(
      row.credential,
      c.currentRoot,
      c.nextRoot,
      context,
      c.targetVersion,
    );
    let revision = Number(row.credential_revision);
    if (rotated.changed) {
      const changed = await env.IDENTITY.prepare(
        "UPDATE github_installations SET credential=?,credential_revision=credential_revision+1,updated_at=? WHERE workspace=? AND installation_id=? AND credential=? AND credential_revision=? AND refresh_lease IS NULL",
      )
        .bind(
          rotated.next,
          now,
          row.workspace,
          row.installation_id,
          row.credential,
          revision,
        )
        .run();
      requireValue(
        changed.meta.changes === 1,
        "ROOT_ROTATION_CONCURRENT_MUTATION",
        "GitHub protected credentials changed during rewrap. Retry after maintenance settles.",
        503,
      );
      revision++;
    }
    const checkpoint = await env.IDENTITY.prepare(
      "UPDATE root_rotation_github SET status='rewrapped',credential_count=1,credential_revision=?,updated_at=? WHERE rotation_id=? AND workspace=? AND installation_id=? AND status='pending'",
    )
      .bind(revision, now, c.id, row.workspace, row.installation_id)
      .run();
    requireValue(
      checkpoint.meta.changes === 1,
      "ROOT_ROTATION_CHECKPOINT_RACE",
      "GitHub root-rotation checkpoint changed concurrently.",
      503,
    );
    processed++;
    credentials++;
  }
  return { processed, credentials };
}

async function verifyGitHubBatch(env: Env, c: RotationConfig, now: number) {
  const rows = await env.IDENTITY.prepare(
    "SELECT g.workspace,g.installation_id,i.credential,i.credential_revision FROM root_rotation_github g LEFT JOIN github_installations i ON i.workspace=g.workspace AND i.installation_id=g.installation_id WHERE g.rotation_id=? AND g.status='rewrapped' ORDER BY g.workspace,g.installation_id LIMIT ?",
  )
    .bind(c.id, BATCH_SIZE)
    .all<any>();
  let processed = 0;
  let credentials = 0;
  for (const row of rows.results || []) {
    let count = 0;
    if (row.credential) {
      const context = `${row.workspace}:github:${row.installation_id}`;
      requireValue(
        envelopeVersion(row.credential) === c.targetVersion &&
          (await canRead(row.credential, c.currentRoot, context)),
        "ROOT_ROTATION_CUTOVER_VERIFY_FAILED",
        "A GitHub protected credential is not readable after root cutover.",
        503,
      );
      count = 1;
    }
    const checkpoint = await env.IDENTITY.prepare(
      "UPDATE root_rotation_github SET status='verified',credential_count=?,credential_revision=?,updated_at=? WHERE rotation_id=? AND workspace=? AND installation_id=? AND status='rewrapped'",
    )
      .bind(
        count,
        row.credential_revision == null ? null : Number(row.credential_revision),
        now,
        c.id,
        row.workspace,
        row.installation_id,
      )
      .run();
    requireValue(
      checkpoint.meta.changes === 1,
      "ROOT_ROTATION_CHECKPOINT_RACE",
      "GitHub root-rotation verification checkpoint changed concurrently.",
      503,
    );
    processed++;
    credentials += count;
  }
  return { processed, credentials };
}

async function counts(env: Env, c: RotationConfig) {
  const workspace = await env.IDENTITY.prepare(
    "SELECT count(*) total,sum(CASE WHEN status IN ('rewrapped','verified') THEN 1 ELSE 0 END) rewrapped,sum(CASE WHEN status='verified' THEN 1 ELSE 0 END) verified,COALESCE(sum(credential_count),0) credentials FROM root_rotation_workspaces WHERE rotation_id=?",
  )
    .bind(c.id)
    .first<any>();
  const github = await env.IDENTITY.prepare(
    "SELECT count(*) total,sum(CASE WHEN status IN ('rewrapped','verified') THEN 1 ELSE 0 END) rewrapped,sum(CASE WHEN status='verified' THEN 1 ELSE 0 END) verified,COALESCE(sum(credential_count),0) credentials FROM root_rotation_github WHERE rotation_id=?",
  )
    .bind(c.id)
    .first<any>();
  return {
    workspace: {
      total: Number(workspace?.total || 0),
      rewrapped: Number(workspace?.rewrapped || 0),
      verified: Number(workspace?.verified || 0),
      credentials: Number(workspace?.credentials || 0),
    },
    github: {
      total: Number(github?.total || 0),
      rewrapped: Number(github?.rewrapped || 0),
      verified: Number(github?.verified || 0),
      credentials: Number(github?.credentials || 0),
    },
  };
}

async function updateRun(
  env: Env,
  c: RotationConfig,
  status: RunRow["status"],
  totals: Awaited<ReturnType<typeof counts>>,
  now: number,
) {
  await env.IDENTITY.prepare(
    "UPDATE root_rotation_runs SET status=?,workspace_total=?,workspace_completed=?,workspace_credentials=?,github_total=?,github_completed=?,github_credentials=?,updated_at=?,completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END,last_error=NULL WHERE rotation_id=?",
  )
    .bind(
      status,
      totals.workspace.total,
      status === "completed" ? totals.workspace.verified : totals.workspace.rewrapped,
      totals.workspace.credentials,
      totals.github.total,
      status === "completed" ? totals.github.verified : totals.github.rewrapped,
      totals.github.credentials,
      now,
      status,
      now,
      c.id,
    )
    .run();
}

export async function runRootRotation(env: Env, now = Date.now()) {
  const c = configuration(env);
  const run = await ensureRun(env, c, now);
  if (c.mode === "rewrap") {
    requireValue(
      run.status === "active" || run.status === "ready_for_cutover",
      "ROOT_ROTATION_PHASE_INVALID",
      "Root rotation cannot re-enter rewrap after cutover verification began.",
      503,
    );
    await snapshotInventory(env, c, now);
    const workspace = await workspaceBatch(
      env,
      c,
      "pending",
      "rewrapped",
      "/root-rotation/rewrap",
      now,
    );
    const github = await rewrapGitHubBatch(env, c, now);
    const totals = await counts(env, c);
    const ready =
      totals.workspace.total === totals.workspace.rewrapped &&
      totals.github.total === totals.github.rewrapped;
    const status = ready ? "ready_for_cutover" : "active";
    await updateRun(env, c, status, totals, now);
    return {
      rotationId: c.id,
      mode: c.mode,
      status,
      batch: {
        workspaces: workspace.processed,
        workspaceCredentials: workspace.credentials,
        githubInstallations: github.processed,
        githubCredentials: github.credentials,
      },
      totals,
    };
  }

  requireValue(
    run.status === "ready_for_cutover" || run.status === "verifying" || run.status === "completed",
    "ROOT_ROTATION_NOT_READY_FOR_CUTOVER",
    "Root rotation rewrap has not reached the protected cutover boundary.",
    503,
  );
  if (run.status === "completed") {
    const totals = await counts(env, c);
    return {
      rotationId: c.id,
      mode: c.mode,
      status: "completed" as const,
      batch: { workspaces: 0, workspaceCredentials: 0, githubInstallations: 0, githubCredentials: 0 },
      totals,
    };
  }
  await env.IDENTITY.prepare(
    "UPDATE root_rotation_runs SET status='verifying',updated_at=? WHERE rotation_id=? AND status IN ('ready_for_cutover','verifying')",
  )
    .bind(now, c.id)
    .run();
  const pendingWorkspace = await env.IDENTITY.prepare(
    "SELECT count(*) count FROM root_rotation_workspaces WHERE rotation_id=? AND status='pending'",
  )
    .bind(c.id)
    .first<{ count: number }>();
  const pendingGitHub = await env.IDENTITY.prepare(
    "SELECT count(*) count FROM root_rotation_github WHERE rotation_id=? AND status='pending'",
  )
    .bind(c.id)
    .first<{ count: number }>();
  requireValue(
    Number(pendingWorkspace?.count || 0) === 0 && Number(pendingGitHub?.count || 0) === 0,
    "ROOT_ROTATION_NOT_READY_FOR_CUTOVER",
    "Unrewrapped credentials remain; restore the old root and resume rewrap.",
    503,
  );
  const workspace = await workspaceBatch(
    env,
    c,
    "rewrapped",
    "verified",
    "/root-rotation/verify",
    now,
  );
  const github = await verifyGitHubBatch(env, c, now);
  const totals = await counts(env, c);
  const completed =
    totals.workspace.total === totals.workspace.verified &&
    totals.github.total === totals.github.verified;
  const status = completed ? "completed" : "verifying";
  await updateRun(env, c, status, totals, now);
  return {
    rotationId: c.id,
    mode: c.mode,
    status,
    batch: {
      workspaces: workspace.processed,
      workspaceCredentials: workspace.credentials,
      githubInstallations: github.processed,
      githubCredentials: github.credentials,
    },
    totals,
  };
}
