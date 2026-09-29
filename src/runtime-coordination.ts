import { digest, Fault, requireValue, uid } from "./common.ts";

export interface RuntimePairingInput {
  installationId: string;
  label: string;
  platform: string;
  runtimeVersion: string;
  sourceRevision?: string;
}
export interface RuntimeAuth {
  workspace: string;
  installationId: string;
  tokenHash: string;
}
export interface ExecutorState {
  workspace: string;
  executorMode: "hosted" | "local";
  activeInstallationId?: string;
  authorityGeneration: number;
  leaseExpiresAt?: number;
  updatedAt: number;
  reason?: string;
}

const PAIRING_TTL_MS = 10 * 60_000;
const RUNTIME_TOKEN_TTL_MS = 30 * 24 * 60 * 60_000;
export const EXECUTOR_LEASE_MS = 90_000;
const codeAlphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function randomToken(bytes = 32) {
  const value = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...value))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/g, "");
}
function randomCode(length = 8) {
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return [...bytes].map((value) => codeAlphabet[value % codeAlphabet.length]).join("");
}
function cleanLabel(value: unknown, name: string, max = 120) {
  requireValue(
    typeof value === "string" &&
      value.trim().length >= 1 &&
      value.trim().length <= max &&
      !/[\u0000-\u001f\u007f]/.test(value),
    "RUNTIME_PAIRING_INPUT_INVALID",
    `${name} is invalid.`,
    400,
  );
  return value.trim();
}
function installationId(value: unknown) {
  requireValue(
    typeof value === "string" &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value),
    "RUNTIME_INSTALLATION_ID_INVALID",
    "Installation identity must be a UUIDv4.",
    400,
  );
  return value.toLowerCase();
}
function bearer(request: Request) {
  const value = request.headers.get("authorization");
  requireValue(
    value !== null && /^Bearer [A-Za-z0-9_-]{32,512}$/.test(value),
    "RUNTIME_UNAUTHENTICATED",
    "Supply the runtime Bearer token.",
    401,
  );
  return value.slice(7);
}

export async function startRuntimePairing(
  db: D1Database,
  origin: string,
  input: RuntimePairingInput,
  now = Date.now(),
) {
  const id = uid();
  const pollToken = randomToken();
  const userCode = randomCode();
  const installation = installationId(input.installationId);
  const label = cleanLabel(input.label, "Installation label");
  const platform = cleanLabel(input.platform, "Platform", 160);
  const runtimeVersion = cleanLabel(input.runtimeVersion, "Runtime version", 80);
  const sourceRevision = input.sourceRevision
    ? cleanLabel(input.sourceRevision, "Source revision", 120)
    : null;
  const expiresAt = now + PAIRING_TTL_MS;

  await db.batch([
    db
      .prepare(
        "DELETE FROM runtime_pairings WHERE expires_at<=? AND status IN ('pending','approved','expired')",
      )
      .bind(now),
    db
      .prepare(
        `INSERT INTO runtime_pairings
         (id,poll_token_hash,user_code_hash,installation_id,label,platform,runtime_version,source_revision,status,workspace,approved_by,created_at,expires_at,approved_at,claimed_at)
         VALUES (?,?,?,?,?,?,?,?,'pending',NULL,NULL,?,?,NULL,NULL)`,
      )
      .bind(
        id,
        await digest(pollToken),
        await digest(userCode),
        installation,
        label,
        platform,
        runtimeVersion,
        sourceRevision,
        now,
        expiresAt,
      ),
  ]);

  return {
    schemaVersion: 1,
    pairingId: id,
    pollToken,
    userCode,
    expiresAt,
    pollAfterMs: 3000,
    verificationUrl: `${origin}/app?runtime_pairing=${encodeURIComponent(id)}&code=${encodeURIComponent(userCode)}`,
    boundary:
      "This is a short-lived bootstrap credential only. It is not provider, admin or publishing authority.",
  };
}

export async function claimRuntimePairing(
  db: D1Database,
  request: Request,
  pairingId: string,
  now = Date.now(),
) {
  const token = bearer(request);
  const row = await db
    .prepare("SELECT * FROM runtime_pairings WHERE id=? AND poll_token_hash=?")
    .bind(pairingId, await digest(token))
    .first<any>();
  requireValue(row, "RUNTIME_PAIRING_UNKNOWN", "Pairing request is unknown.", 404);

  if (row.expires_at <= now && row.status !== "claimed") {
    await db
      .prepare(
        "UPDATE runtime_pairings SET status='expired' WHERE id=? AND status IN ('pending','approved')",
      )
      .bind(pairingId)
      .run();
    throw new Fault("RUNTIME_PAIRING_EXPIRED", "Pairing request expired. Start again.", 410);
  }
  if (row.status === "pending")
    return { schemaVersion: 1, status: "pending", pollAfterMs: 3000, expiresAt: row.expires_at };
  requireValue(
    row.status === "approved",
    "RUNTIME_PAIRING_ALREADY_CONSUMED",
    "Pairing bootstrap has already been consumed or revoked.",
    409,
  );

  const runtimeToken = randomToken(48);
  const runtimeTokenHash = await digest(runtimeToken);
  const tokenExpiresAt = now + RUNTIME_TOKEN_TTL_MS;

  const result = await db.batch([
    db
      .prepare(
        `INSERT INTO runtime_installations
         (installation_id,workspace,label,platform,runtime_version,source_revision,token_hash,status,created_at,approved_at,token_expires_at,last_seen_at,revoked_at)
         VALUES (?,?,?,?,?,?,?,'active',?,?,?,?,NULL)
         ON CONFLICT(installation_id) DO NOTHING`,
      )
      .bind(
        row.installation_id,
        row.workspace,
        row.label,
        row.platform,
        row.runtime_version,
        row.source_revision,
        runtimeTokenHash,
        row.created_at,
        row.approved_at || now,
        tokenExpiresAt,
        now,
      ),
    db
      .prepare(
        "UPDATE runtime_pairings SET status='claimed',claimed_at=? WHERE id=? AND status='approved'",
      )
      .bind(now, pairingId),
    db
      .prepare(
        `INSERT INTO workspace_executors
         (workspace,executor_mode,active_installation_id,authority_generation,lease_expires_at,updated_at,reason)
         VALUES (?,'hosted',NULL,1,NULL,?,'initial runtime pairing')
         ON CONFLICT(workspace) DO NOTHING`,
      )
      .bind(row.workspace, now),
  ]);
  requireValue(
    result[0].meta.changes === 1 && result[1].meta.changes === 1,
    "RUNTIME_PAIRING_RACE",
    "Pairing state changed before the runtime token was claimed. Start again.",
    409,
  );

  return {
    schemaVersion: 1,
    status: "paired",
    workspace: row.workspace,
    installationId: row.installation_id,
    runtimeToken,
    tokenExpiresAt,
    executor: await executorStatus(db, row.workspace, now),
    notice: "Runtime token is shown once. Store it with user-only permissions.",
  };
}

export async function approveRuntimePairing(
  db: D1Database,
  workspace: string,
  ownerActor: string,
  input: { pairingId?: unknown; userCode?: unknown },
  now = Date.now(),
) {
  requireValue(typeof input.pairingId === "string", "RUNTIME_PAIRING_ID_REQUIRED", "Pairing ID is required.");
  requireValue(typeof input.userCode === "string", "RUNTIME_PAIRING_CODE_REQUIRED", "Pairing code is required.");
  const code = input.userCode.trim().toUpperCase();
  requireValue(/^[A-Z2-9]{8}$/.test(code), "RUNTIME_PAIRING_CODE_INVALID", "Pairing code is invalid.");

  const row = await db
    .prepare("SELECT * FROM runtime_pairings WHERE id=?")
    .bind(input.pairingId)
    .first<any>();
  requireValue(row, "RUNTIME_PAIRING_UNKNOWN", "Pairing request is unknown.", 404);
  requireValue(row.status === "pending", "RUNTIME_PAIRING_NOT_PENDING", "Pairing request is not pending.", 409);
  requireValue(row.expires_at > now, "RUNTIME_PAIRING_EXPIRED", "Pairing request expired. Start again.", 410);
  requireValue(
    row.user_code_hash === (await digest(code)),
    "RUNTIME_PAIRING_CODE_MISMATCH",
    "Pairing code does not match this installation.",
    403,
  );

  const updated = await db
    .prepare(
      `UPDATE runtime_pairings
       SET status='approved',workspace=?,approved_by=?,approved_at=?
       WHERE id=? AND status='pending' AND expires_at>?`,
    )
    .bind(workspace, ownerActor, now, input.pairingId, now)
    .run();
  requireValue(updated.meta.changes === 1, "RUNTIME_PAIRING_RACE", "Pairing request changed before approval.", 409);
  return {
    schemaVersion: 1,
    status: "approved",
    pairingId: input.pairingId,
    installationId: row.installation_id,
    label: row.label,
    platform: row.platform,
    runtimeVersion: row.runtime_version,
  };
}

export async function authenticateRuntime(
  request: Request,
  db: D1Database,
  now = Date.now(),
): Promise<RuntimeAuth> {
  const raw = bearer(request);
  const tokenHash = await digest(raw);
  const row = await db
    .prepare(
      `SELECT workspace,installation_id,status,token_expires_at
       FROM runtime_installations WHERE token_hash=?`,
    )
    .bind(tokenHash)
    .first<any>();
  requireValue(
    row &&
      row.status === "active" &&
      row.token_expires_at > now,
    "RUNTIME_UNAUTHENTICATED",
    "Runtime token is invalid, expired or revoked.",
    401,
  );
  await db
    .prepare("UPDATE runtime_installations SET last_seen_at=? WHERE token_hash=?")
    .bind(now, tokenHash)
    .run();
  return {
    workspace: row.workspace,
    installationId: row.installation_id,
    tokenHash,
  };
}

export async function listRuntimeInstallations(db: D1Database, workspace: string) {
  const rows = await db
    .prepare(
      `SELECT installation_id,label,platform,runtime_version,source_revision,status,
              created_at,approved_at,token_expires_at,last_seen_at,revoked_at
       FROM runtime_installations WHERE workspace=? ORDER BY approved_at DESC`,
    )
    .bind(workspace)
    .all();
  return rows.results;
}

export async function revokeRuntimeInstallation(
  db: D1Database,
  workspace: string,
  installation: string,
  now = Date.now(),
) {
  const id = installationId(installation);
  const executor = await executorStatus(db, workspace, now);
  requireValue(
    executor.activeInstallationId !== id,
    "RUNTIME_EXECUTOR_ACTIVE",
    "Deactivate or move executor authority before revoking the active installation.",
    409,
  );
  const result = await db
    .prepare(
      "UPDATE runtime_installations SET status='revoked',revoked_at=? WHERE workspace=? AND installation_id=? AND status='active'",
    )
    .bind(now, workspace, id)
    .run();
  requireValue(result.meta.changes === 1, "RUNTIME_INSTALLATION_NOT_ACTIVE", "Installation is not active.", 404);
  return { revoked: true, installationId: id };
}

export async function executorStatus(
  db: D1Database,
  workspace: string,
  now = Date.now(),
): Promise<ExecutorState> {
  await db
    .prepare(
      `INSERT INTO workspace_executors
       (workspace,executor_mode,active_installation_id,authority_generation,lease_expires_at,updated_at,reason)
       VALUES (?,'hosted',NULL,1,NULL,?,'default hosted executor')
       ON CONFLICT(workspace) DO NOTHING`,
    )
    .bind(workspace, now)
    .run();
  const row = await db
    .prepare("SELECT * FROM workspace_executors WHERE workspace=?")
    .bind(workspace)
    .first<any>();
  requireValue(row, "RUNTIME_EXECUTOR_STATE_MISSING", "Executor state is unavailable.", 500);
  return {
    workspace,
    executorMode: row.executor_mode,
    ...(row.active_installation_id ? { activeInstallationId: row.active_installation_id } : {}),
    authorityGeneration: Number(row.authority_generation),
    ...(row.lease_expires_at ? { leaseExpiresAt: Number(row.lease_expires_at) } : {}),
    updatedAt: Number(row.updated_at),
    ...(row.reason ? { reason: String(row.reason) } : {}),
  };
}

export async function setExecutor(
  db: D1Database,
  workspace: string,
  input: { mode: "hosted" | "local"; installationId?: string; reason: string },
  now = Date.now(),
) {
  const current = await executorStatus(db, workspace, now);
  const reason = cleanLabel(input.reason, "Executor transition reason", 240);
  let targetInstallation: string | null = null;
  if (input.mode === "local") {
    targetInstallation = installationId(input.installationId);
    const installation = await db
      .prepare(
        "SELECT installation_id FROM runtime_installations WHERE workspace=? AND installation_id=? AND status='active' AND token_expires_at>?",
      )
      .bind(workspace, targetInstallation, now)
      .first();
    requireValue(
      installation,
      "RUNTIME_INSTALLATION_NOT_READY",
      "Target runtime installation is not active.",
      409,
    );
  }

  const nextGeneration = current.authorityGeneration + 1;
  const updated = await db
    .prepare(
      `UPDATE workspace_executors
       SET executor_mode=?,active_installation_id=?,authority_generation=?,
           lease_expires_at=?,updated_at=?,reason=?
       WHERE workspace=? AND authority_generation=?`,
    )
    .bind(
      input.mode,
      targetInstallation,
      nextGeneration,
      input.mode === "local" ? now + EXECUTOR_LEASE_MS : null,
      now,
      reason,
      workspace,
      current.authorityGeneration,
    )
    .run();
  requireValue(
    updated.meta.changes === 1,
    "RUNTIME_EXECUTOR_TRANSITION_RACE",
    "Executor authority changed concurrently. Inspect current status and review again.",
    409,
  );
  return executorStatus(db, workspace, now);
}

export async function renewExecutorLease(
  db: D1Database,
  auth: RuntimeAuth,
  generation: number,
  now = Date.now(),
) {
  requireValue(
    Number.isInteger(generation) && generation >= 1,
    "RUNTIME_GENERATION_INVALID",
    "Authority generation is invalid.",
  );
  const result = await db
    .prepare(
      `UPDATE workspace_executors
       SET lease_expires_at=?,updated_at=?
       WHERE workspace=? AND executor_mode='local'
         AND active_installation_id=? AND authority_generation=?`,
    )
    .bind(now + EXECUTOR_LEASE_MS, now, auth.workspace, auth.installationId, generation)
    .run();
  requireValue(
    result.meta.changes === 1,
    "RUNTIME_EXECUTOR_FENCED",
    "This installation no longer owns the current executor generation.",
    409,
  );
  return executorStatus(db, auth.workspace, now);
}

export async function requireLocalExecutor(
  db: D1Database,
  auth: RuntimeAuth,
  generation: number,
  now = Date.now(),
) {
  const executor = await executorStatus(db, auth.workspace, now);
  requireValue(
    executor.executorMode === "local" &&
      executor.activeInstallationId === auth.installationId &&
      executor.authorityGeneration === generation &&
      Number(executor.leaseExpiresAt || 0) > now,
    "RUNTIME_EXECUTOR_FENCED",
    "This runtime is not the current leased executor. No provider effect was attempted.",
    409,
  );
  return executor;
}

export async function requireHostedExecutor(
  db: D1Database,
  workspace: string,
  now = Date.now(),
) {
  const executor = await executorStatus(db, workspace, now);
  requireValue(
    executor.executorMode === "hosted",
    "LOCAL_RUNTIME_EXECUTOR_ACTIVE",
    "This workspace is owned by a local PostSteward runtime. Hosted consequence operations are fenced.",
    409,
  );
  return executor;
}
