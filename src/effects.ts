import { Fault, requireValue } from "./common.ts";
import type { Credential, ProviderAPI, Published } from "./providers.ts";
import type { Delivery, Identity, Provider } from "./types.ts";

export type EffectStatus =
  | "intent"
  | "uncertain"
  | "created"
  | "verified"
  | "unverified";
type ContainerEffectStatus = "intent" | "uncertain" | "created";

interface EffectRow {
  workspace: string;
  fingerprint: string;
  delivery_id: string;
  provider: Provider;
  text_digest: string;
  status: EffectStatus;
  claim_id?: string;
  post_id?: string;
  url?: string;
  reason?: string;
  created_at: number;
  updated_at: number;
}
interface ContainerEffectRow {
  workspace: string;
  fingerprint: string;
  delivery_id: string;
  status: ContainerEffectStatus;
  container_id?: string;
  reason?: string;
  created_at: number;
  updated_at: number;
}

function knownNoEffect(error: unknown) {
  return (
    error instanceof Fault &&
    (error.code === "CONTAINER_REQUIRED" ||
      /^PROVIDER_HTTP_(400|401|403|404|422|429)$/.test(error.code))
  );
}
function safeReason(error: unknown) {
  return error instanceof Fault ? error.code : "UNKNOWN_AFTER_WRITE_FENCE";
}
function event(name: string, fields: Record<string, unknown>) {
  console.warn(JSON.stringify({ event: name, ...fields }));
}

export async function workspaceQuarantined(db: D1Database, workspace: string) {
  const row = await db
    .prepare(
      "SELECT publishing_quarantined,reason,quarantined_at,updated_at FROM workspace_controls WHERE workspace=?",
    )
    .bind(workspace)
    .first<{
      publishing_quarantined: number;
      reason?: string;
      quarantined_at?: number;
      updated_at: number;
    }>();
  return {
    quarantined: row?.publishing_quarantined === 1,
    reason: row?.reason || undefined,
    quarantinedAt: row?.quarantined_at || undefined,
    updatedAt: row?.updated_at || undefined,
  };
}

export async function setWorkspaceQuarantine(
  db: D1Database,
  workspace: string,
  quarantined: boolean,
  reason: string,
  now = Date.now(),
) {
  requireValue(
    typeof reason === "string" && reason.trim().length >= 3 && reason.length <= 240,
    "RECOVERY_REASON_REQUIRED",
    "Supply a concise recovery reason.",
    400,
  );
  await db
    .prepare(
      "INSERT INTO workspace_controls(workspace,publishing_quarantined,reason,quarantined_at,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(workspace) DO UPDATE SET publishing_quarantined=excluded.publishing_quarantined,reason=excluded.reason,quarantined_at=excluded.quarantined_at,updated_at=excluded.updated_at",
    )
    .bind(
      workspace,
      quarantined ? 1 : 0,
      reason.trim(),
      quarantined ? now : null,
      now,
    )
    .run();
  event(quarantined ? "workspace_recovery_quarantined" : "workspace_recovery_resumed", {
    workspace,
    at: now,
  });
  return workspaceQuarantined(db, workspace);
}

export async function effectSummary(db: D1Database, workspace: string) {
  const [rows, containers] = await Promise.all([
    db
      .prepare(
        "SELECT status,count(*) AS count FROM external_effects WHERE workspace=? GROUP BY status",
      )
      .bind(workspace)
      .all<{ status: EffectStatus; count: number }>(),
    db
      .prepare(
        "SELECT status,count(*) AS count FROM external_containers WHERE workspace=? GROUP BY status",
      )
      .bind(workspace)
      .all<{ status: ContainerEffectStatus; count: number }>(),
  ]);
  const counts: Record<EffectStatus, number> = {
    intent: 0,
    uncertain: 0,
    created: 0,
    verified: 0,
    unverified: 0,
  };
  const containerCounts: Record<ContainerEffectStatus, number> = {
    intent: 0,
    uncertain: 0,
    created: 0,
  };
  for (const row of rows.results || []) counts[row.status] = Number(row.count || 0);
  for (const row of containers.results || [])
    containerCounts[row.status] = Number(row.count || 0);
  return {
    ...counts,
    containerIntent: containerCounts.intent,
    containerUncertain: containerCounts.uncertain,
    containerCreated: containerCounts.created,
  };
}

/**
 * External-effect fences live in D1 rather than inside a workspace Durable
 * Object. Restoring workspace state therefore cannot erase knowledge of a
 * Threads container or a provider publication and silently replay either.
 */
export class EffectLedgerProviders implements ProviderAPI {
  constructor(
    private inner: ProviderAPI,
    private db: D1Database,
    private workspace: string,
    private now: () => number = Date.now,
  ) {}

  identity(provider: Provider, credential: Credential): Promise<Identity> {
    return this.inner.identity(provider, credential);
  }
  containerStatus(id: string, credential: Credential): Promise<string> {
    return this.inner.containerStatus(id, credential);
  }
  metrics(delivery: Delivery, credential: Credential): Promise<unknown> {
    return this.inner.metrics(delivery, credential);
  }

  private existing(delivery: Delivery) {
    return this.db
      .prepare(
        "SELECT * FROM external_effects WHERE workspace=? AND fingerprint=?",
      )
      .bind(this.workspace, delivery.fingerprint)
      .first<EffectRow>();
  }
  private existingContainer(delivery: Delivery) {
    return this.db
      .prepare(
        "SELECT * FROM external_containers WHERE workspace=? AND fingerprint=?",
      )
      .bind(this.workspace, delivery.fingerprint)
      .first<ContainerEffectRow>();
  }
  private async assertNotQuarantined() {
    const control = await workspaceQuarantined(this.db, this.workspace);
    requireValue(
      !control.quarantined,
      "RECOVERY_QUARANTINED",
      "Workspace is in recovery quarantine; provider writes are disabled.",
      409,
    );
  }

  async createContainer(delivery: Delivery, credential: Credential): Promise<string> {
    await this.assertNotQuarantined();
    const now = this.now();
    const inserted = await this.db
      .prepare(
        "INSERT INTO external_containers(workspace,fingerprint,delivery_id,status,created_at,updated_at) SELECT ?,?,?,'intent',?,? WHERE COALESCE((SELECT publishing_quarantined FROM workspace_controls WHERE workspace=?),0)=0 ON CONFLICT(workspace,fingerprint) DO NOTHING",
      )
      .bind(
        this.workspace,
        delivery.fingerprint,
        delivery.id,
        now,
        now,
        this.workspace,
      )
      .run();
    if (inserted.meta.changes !== 1) {
      const control = await workspaceQuarantined(this.db, this.workspace);
      requireValue(
        !control.quarantined,
        "RECOVERY_QUARANTINED",
        "Workspace entered recovery quarantine before the Threads container intent was acquired.",
        409,
      );
      const prior = await this.existingContainer(delivery);
      requireValue(
        prior,
        "AMBIGUOUS_PROVIDER_WRITE",
        "Threads container fence exists but its evidence is unavailable. Do not recreate it.",
        502,
      );
      if (prior.container_id) return prior.container_id;
      throw new Fault(
        "AMBIGUOUS_PROVIDER_WRITE",
        "An earlier Threads container write is unresolved. Do not recreate it.",
        502,
      );
    }
    try {
      const containerId = await this.inner.createContainer(delivery, credential);
      await this.db
        .prepare(
          "UPDATE external_containers SET status='created',container_id=?,reason=NULL,updated_at=? WHERE workspace=? AND fingerprint=?",
        )
        .bind(containerId, this.now(), this.workspace, delivery.fingerprint)
        .run();
      return containerId;
    } catch (error) {
      if (knownNoEffect(error)) {
        await this.db
          .prepare(
            "DELETE FROM external_containers WHERE workspace=? AND fingerprint=? AND status='intent' AND container_id IS NULL",
          )
          .bind(this.workspace, delivery.fingerprint)
          .run();
      } else {
        const reason = safeReason(error);
        await this.db
          .prepare(
            "UPDATE external_containers SET status='uncertain',reason=?,updated_at=? WHERE workspace=? AND fingerprint=?",
          )
          .bind(reason, this.now(), this.workspace, delivery.fingerprint)
          .run();
        event("external_container_uncertain", {
          workspace: this.workspace,
          delivery: delivery.id,
          reason,
        });
      }
      throw error;
    }
  }

  async publish(delivery: Delivery, credential: Credential): Promise<Published> {
    await this.assertNotQuarantined();
    const now = this.now();
    const inserted = await this.db
      .prepare(
        "INSERT INTO external_effects(workspace,fingerprint,delivery_id,provider,text_digest,status,claim_id,created_at,updated_at) SELECT ?,?,?,?,?,'intent',?,?,? WHERE COALESCE((SELECT publishing_quarantined FROM workspace_controls WHERE workspace=?),0)=0 ON CONFLICT(workspace,fingerprint) DO NOTHING",
      )
      .bind(
        this.workspace,
        delivery.fingerprint,
        delivery.id,
        delivery.provider,
        delivery.digest,
        delivery.claimId || null,
        now,
        now,
        this.workspace,
      )
      .run();
    if (inserted.meta.changes !== 1) {
      const control = await workspaceQuarantined(this.db, this.workspace);
      requireValue(
        !control.quarantined,
        "RECOVERY_QUARANTINED",
        "Workspace entered recovery quarantine before the publication intent was acquired.",
        409,
      );
      const prior = await this.existing(delivery);
      requireValue(
        prior &&
          prior.provider === delivery.provider &&
          prior.text_digest === delivery.digest,
        "EXTERNAL_EFFECT_FENCE_MISMATCH",
        "External-effect fence exists but does not match this delivery. Do not resubmit.",
        409,
      );
      if (prior.post_id)
        return {
          id: prior.post_id,
          ...(prior.url ? { url: prior.url } : {}),
        };
      throw new Fault(
        "AMBIGUOUS_PROVIDER_WRITE",
        "An earlier provider-write fence exists without a durable provider ID. Do not resubmit.",
        502,
      );
    }
    try {
      const published = await this.inner.publish(delivery, credential);
      await this.db
        .prepare(
          "UPDATE external_effects SET status='created',post_id=?,url=?,reason=NULL,updated_at=? WHERE workspace=? AND fingerprint=?",
        )
        .bind(
          published.id,
          published.url || null,
          this.now(),
          this.workspace,
          delivery.fingerprint,
        )
        .run();
      return published;
    } catch (error) {
      if (knownNoEffect(error)) {
        await this.db
          .prepare(
            "DELETE FROM external_effects WHERE workspace=? AND fingerprint=? AND status='intent' AND post_id IS NULL",
          )
          .bind(this.workspace, delivery.fingerprint)
          .run();
      } else {
        const reason = safeReason(error);
        await this.db
          .prepare(
            "UPDATE external_effects SET status='uncertain',reason=?,updated_at=? WHERE workspace=? AND fingerprint=?",
          )
          .bind(reason, this.now(), this.workspace, delivery.fingerprint)
          .run();
        event("external_effect_uncertain", {
          workspace: this.workspace,
          delivery: delivery.id,
          provider: delivery.provider,
          reason,
        });
      }
      throw error;
    }
  }

  async verify(delivery: Delivery, credential: Credential) {
    const result = await this.inner.verify(delivery, credential);
    const prior = await this.existing(delivery);
    if (prior?.post_id && prior.post_id === delivery.postId) {
      await this.db
        .prepare(
          "UPDATE external_effects SET status=?,url=COALESCE(?,url),reason=?,updated_at=? WHERE workspace=? AND fingerprint=?",
        )
        .bind(
          result.verified ? "verified" : "unverified",
          result.url || null,
          result.verified ? null : "READBACK_NOT_MATCHED",
          this.now(),
          this.workspace,
          delivery.fingerprint,
        )
        .run();
    }
    return result;
  }
}
