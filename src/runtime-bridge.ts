import { z } from "zod";
import { isAuthorized } from "./auth.ts";
import { canonical, digest, requireValue, uid } from "./common.ts";
import { byName } from "./operations/catalog.ts";
import {
  executorStatus,
  requireLocalExecutor,
  type RuntimeAuth,
} from "./runtime-coordination.ts";
import type { Actor, Env } from "./types.ts";

export const runtimeOperations = new Set([
  "runtime_inspect",
  "runtime_schedule_create",
  "runtime_schedule_cancel",
]);
export const hostedRuntimeOperations = new Set([
  "project_put",
  "projects_list",
  "campaign_create",
  "campaign_get",
  "campaign_validate",
  "publish_now",
  "schedule_create",
  "schedule_cancel",
  "schedule_replace",
  "receipt_get",
  "receipts_list",
  "automation_configure",
  "automation_inspect",
  "automation_preview",
  "automation_enable",
  "automation_pause",
]);
interface CommandRow {
  id: string;
  workspace: string;
  installation_id: string;
  generation: number;
  actor: string;
  grant_hash: string | null;
  scope: Actor["scopes"][number];
  operation: string;
  input: string;
  request_digest: string;
  idempotency_key: string;
  status: string;
  expires_at: number;
  result: string | null;
}
function commandActor(row: CommandRow): Actor {
  return {
    workspace: row.workspace,
    id: row.actor,
    scopes: [row.scope],
    ...(row.grant_hash ? { grant: row.grant_hash } : {}),
  };
}
function projection(row: CommandRow) {
  return {
    commandId: row.id,
    executorMode: "local",
    installationId: row.installation_id,
    authorityGeneration: row.generation,
    operation: row.operation,
    status: row.status,
    expiresAt: row.expires_at,
    timedOut: row.status === "claimed" && row.expires_at <= Date.now(),
    ...(row.result ? { result: JSON.parse(row.result) } : {}),
    boundary:
      "A command receipt records local execution, never provider publication proof. Inspect schedule receipts for publication.",
  };
}
export async function bridgeOperation(
  env: Env,
  actor: Actor,
  name: string,
  raw: unknown,
) {
  const op = byName.get(name)!;
  requireValue(
    actor.scopes.includes("admin") || actor.scopes.includes(op.scope),
    "SCOPE_REQUIRED",
    `Operation requires ${op.scope}.`,
    403,
  );
  const parsed = op.schema.safeParse(raw);
  requireValue(
    parsed.success,
    "INVALID_INPUT",
    "Invalid runtime operation input.",
  );
  const input = parsed.data as Record<string, unknown>;
  if (name === "runtime_command_get") {
    const row = await env.IDENTITY.prepare(
      "SELECT * FROM runtime_commands WHERE workspace=? AND id=? AND (actor=? OR ?=1)",
    )
      .bind(
        actor.workspace,
        input.commandId,
        actor.id,
        actor.scopes.includes("admin") ? 1 : 0,
      )
      .first<CommandRow>();
    requireValue(
      row,
      "RUNTIME_COMMAND_UNKNOWN",
      "Command is unknown for this actor.",
      404,
    );
    return projection(row);
  }
  const key = String(input.idempotencyKey);
  const requestDigest = await digest({ name, input });
  const previous = await env.IDENTITY.prepare(
    "SELECT * FROM runtime_commands WHERE workspace=? AND actor=? AND idempotency_key=?",
  )
    .bind(actor.workspace, actor.id, key)
    .first<CommandRow>();
  if (previous) {
    requireValue(
      previous.request_digest === requestDigest,
      "IDEMPOTENCY_MISMATCH",
      "This key already binds different command inputs.",
      409,
    );
    return projection(previous);
  }
  const executor = await executorStatus(env.IDENTITY, actor.workspace);
  requireValue(
    executor.executorMode === "local" &&
      executor.executorStatus === "active" &&
      Number(executor.leaseExpiresAt || 0) > Date.now(),
    "RUNTIME_EXECUTOR_UNAVAILABLE",
    "The reviewed local executor must be online before queuing a command.",
    409,
  );
  const now = Date.now();
  await env.IDENTITY.prepare(
    "UPDATE runtime_commands SET status='expired' WHERE workspace=? AND status='queued' AND expires_at<=?",
  )
    .bind(actor.workspace, now)
    .run();
  const id = uid();
  const inserted = await env.IDENTITY.prepare(
    `INSERT INTO runtime_commands
    (id,workspace,installation_id,generation,actor,grant_hash,scope,operation,input,request_digest,idempotency_key,status,created_at,expires_at)
    SELECT ?,?,?,?,?,?,?,?,?,?,?,'queued',?,? WHERE
    (SELECT count(*) FROM runtime_commands WHERE workspace=? AND status IN ('queued','claimed') AND expires_at>?) < 100
    ON CONFLICT(workspace,actor,idempotency_key) DO NOTHING`,
  )
    .bind(
      id,
      actor.workspace,
      executor.activeInstallationId!,
      executor.authorityGeneration,
      actor.id,
      actor.grant || null,
      op.scope,
      name,
      canonical(input),
      requestDigest,
      key,
      now,
      now + 5 * 60_000,
      actor.workspace,
      now,
    )
    .run();
  const row = await env.IDENTITY.prepare(
    "SELECT * FROM runtime_commands WHERE workspace=? AND actor=? AND idempotency_key=?",
  )
    .bind(actor.workspace, actor.id, key)
    .first<CommandRow>();
  requireValue(
    row,
    "RUNTIME_COMMAND_LIMIT",
    "Local command queue is full.",
    429,
  );
  requireValue(
    row.request_digest === requestDigest,
    "IDEMPOTENCY_MISMATCH",
    "Concurrent command inputs differ.",
    409,
  );
  return { ...projection(row), reserved: inserted.meta.changes === 1 };
}
export async function claimCommand(
  env: Env,
  auth: RuntimeAuth,
  generation: number,
) {
  await requireLocalExecutor(env.IDENTITY, auth, generation);
  const row = await env.IDENTITY.prepare(
    "SELECT * FROM runtime_commands WHERE workspace=? AND installation_id=? AND generation=? AND status='queued' AND expires_at>? ORDER BY created_at,id LIMIT 1",
  )
    .bind(auth.workspace, auth.installationId, generation, Date.now())
    .first<CommandRow>();
  if (!row) return { command: null };
  if (!(await isAuthorized(commandActor(row), env))) {
    await env.IDENTITY.prepare(
      "UPDATE runtime_commands SET status='failed',result=? WHERE id=? AND status='queued'",
    )
      .bind(
        canonical({ error: { code: "RUNTIME_COMMAND_AUTHORITY_REVOKED" } }),
        row.id,
      )
      .run();
    return { command: null };
  }
  const updated = await env.IDENTITY.prepare(
    "UPDATE runtime_commands SET status='claimed',claimed_at=? WHERE id=? AND status='queued' AND expires_at>?",
  )
    .bind(Date.now(), row.id, Date.now())
    .run();
  if (updated.meta.changes !== 1) return { command: null };
  return {
    command: {
      commandId: row.id,
      installationId: row.installation_id,
      authorityGeneration: row.generation,
      operation: row.operation,
      input: JSON.parse(row.input),
      expiresAt: row.expires_at,
    },
  };
}
export async function authorizeCommand(
  env: Env,
  auth: RuntimeAuth,
  generation: number,
  id: string,
) {
  await requireLocalExecutor(env.IDENTITY, auth, generation);
  const row = await env.IDENTITY.prepare(
    "SELECT * FROM runtime_commands WHERE id=? AND workspace=? AND installation_id=? AND generation=?",
  )
    .bind(id, auth.workspace, auth.installationId, generation)
    .first<CommandRow>();
  requireValue(
    row && row.status === "claimed" && row.expires_at > Date.now(),
    "RUNTIME_COMMAND_NOT_EXECUTABLE",
    "Command is expired, consumed or belongs to another executor.",
    409,
  );
  requireValue(
    await isAuthorized(commandActor(row), env),
    "RUNTIME_COMMAND_AUTHORITY_REVOKED",
    "The command's original authority was revoked or expired.",
    403,
  );
  return { authorized: true };
}
export const commandCompletionSchema = z.strictObject({
  commandId: z.uuid(),
  authorityGeneration: z.number().int().min(1),
  status: z.enum(["completed", "failed"]),
  result: z.record(z.string(), z.unknown()),
});
export async function completeCommand(
  env: Env,
  auth: RuntimeAuth,
  input: z.infer<typeof commandCompletionSchema>,
) {
  await requireLocalExecutor(env.IDENTITY, auth, input.authorityGeneration);
  const result = canonical(input.result);
  requireValue(
    new TextEncoder().encode(result).length <= 24 * 1024,
    "RUNTIME_RESULT_TOO_LARGE",
    "Command result exceeds 24 KiB.",
  );
  const updated = await env.IDENTITY.prepare(
    "UPDATE runtime_commands SET status=?,result=?,completed_at=? WHERE id=? AND workspace=? AND installation_id=? AND generation=? AND status='claimed'",
  )
    .bind(
      input.status,
      result,
      Date.now(),
      input.commandId,
      auth.workspace,
      auth.installationId,
      input.authorityGeneration,
    )
    .run();
  const row = await env.IDENTITY.prepare(
    "SELECT * FROM runtime_commands WHERE id=? AND workspace=? AND installation_id=? AND generation=?",
  )
    .bind(
      input.commandId,
      auth.workspace,
      auth.installationId,
      input.authorityGeneration,
    )
    .first<CommandRow>();
  requireValue(
    row && row.status === input.status && row.result === result,
    "RUNTIME_COMMAND_COMPLETION_MISMATCH",
    "Completion cannot rewrite a prior result.",
    409,
  );
  return { ...projection(row), recorded: updated.meta.changes === 1 };
}
export async function requireCommandEffect(
  env: Env,
  auth: RuntimeAuth,
  id: string,
  campaign: string,
  provider: string,
  generation: number,
) {
  const row = await env.IDENTITY.prepare(
    "SELECT * FROM runtime_commands WHERE id=? AND workspace=? AND installation_id=? AND generation=? AND status='completed' AND operation='runtime_schedule_create'",
  )
    .bind(id, auth.workspace, auth.installationId, generation)
    .first<CommandRow>();
  requireValue(
    row,
    "RUNTIME_COMMAND_EFFECT_REFUSED",
    "No completed scheduling command authorizes this effect.",
    403,
  );
  const input = JSON.parse(row.input);
  requireValue(
    campaign ===
      input.campaign + ":" + JSON.parse(row.result || "{}").scheduleId &&
      provider === input.provider &&
      (await isAuthorized(commandActor(row), env)),
    "RUNTIME_COMMAND_EFFECT_REFUSED",
    "Scheduled agent authority or destination no longer matches.",
    403,
  );
}
