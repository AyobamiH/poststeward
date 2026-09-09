import { Fault, requireValue } from "./common.ts";
import type { Credential, ProviderAPI, Published } from "./providers.ts";
import type { Delivery, Identity, Provider } from "./types.ts";

interface EffectRow {
  workspace: string;
  fingerprint: string;
  delivery_id: string;
  provider: Provider;
  text_digest: string;
  status: "intent" | "uncertain" | "created" | "verified" | "unverified";
  claim_id?: string;
  post_id?: string;
  url?: string;
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
  return workspaceQuarantined(db, workspace);
}

export async function effectSummary(db: D1Database, workspace: string) {
  const rows = await db
    .prepare(
      "SELECT status,count(*) AS count FROM external_effects WHERE workspace=? GROUP BY status",
    )
    .bind(workspace)
    .all<{ status: EffectRow["status"]; count: number }>();
  const counts = Object.fromEntries(
    ["intent", "uncertain", "created", "verified", "unverified"].map((status) => [
      status,
      0,
    ]),
  ) as Record<EffectRow["status"], number>;
  for (const row of rows.results || []) counts[row.status] = Number(row.count || 0);
  return counts;
}

/**
 * Provider wrapper whose publication fence lives in D1 rather than the workspace
 * Durable Object. A point-in-time restore of the workspace therefore cannot
 * forget a provider write and silently replay it.
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
  createContainer(delivery: Delivery, credential: Credential): Promise<string> {
    return this.inner.createContainer(delivery, credential);
  }
  containerStatus(id: string, credential: Credential): Promise<string> {
    return this.inner.containerStatus(id, credential);
  }
  metrics(delivery: Delivery, credential: Credential): Promise<unknown> {
    return this.inner.metrics(delivery, credential);
  }

  private async existing(delivery: Delivery) {
    return this.db
      .prepare(
        "SELECT * FROM external_effects WHERE workspace=? AND fingerprint=?",
      )
      .bind(this.workspace, delivery.fingerprint)
      .first<EffectRow>();
  }

  async publish(delivery: Delivery, credential: Credential): Promise<Published> {
    const control = await workspaceQuarantined(this.db, this.workspace);
    requireValue(
      !control.quarantined,
      "AUTHORITY_CHANGED",
      "Workspace is in recovery quarantine; provider writes are disabled.",
      409,
    );
    const now = this.now();
    const inserted = await this.db
      .prepare(
        "INSERT INTO external_effects(workspace,fingerprint,delivery_id,provider,text_digest,status,claim_id,created_at,updated_at) VALUES (?,?,?,?,?,'intent',?,?,?) ON CONFLICT(workspace,fingerprint) DO NOTHING",
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
      )
      .run();
    if (inserted.meta.changes !== 1) {
      const prior = await this.existing(delivery);
      requireValue(
        prior,
        "AMBIGUOUS_PROVIDER_WRITE",
        "External-effect fence exists but its evidence is unavailable. Do not resubmit.",
        502,
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
        await this.db
          .prepare(
            "UPDATE external_effects SET status='uncertain',reason=?,updated_at=? WHERE workspace=? AND fingerprint=?",
          )
          .bind(
            safeReason(error),
            this.now(),
            this.workspace,
            delivery.fingerprint,
          )
          .run();
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
