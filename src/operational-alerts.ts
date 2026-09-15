import type { Env } from "./types.ts";

export const operationalAlertClasses = [
  "provider_or_oauth_failure",
  "recovery_state",
  "stripe_reconciliation",
  "capacity_threshold",
] as const;
export type OperationalAlertClass = (typeof operationalAlertClasses)[number];
export type OperationalAlertSeverity = "warning" | "critical";

interface AlertRow {
  id: string;
  dedupe_key: string;
  class: OperationalAlertClass;
  severity: OperationalAlertSeverity;
  code: string;
  release: string;
  subject_fingerprint: string | null;
  occurrences: number;
  first_seen_at: number;
  last_seen_at: number;
  status: "pending" | "sending" | "sent" | "dead";
  attempts: number;
  next_attempt_at: number;
  lease_until: number | null;
  sent_at: number | null;
  last_http_status: number | null;
  last_error_code: string | null;
  created_at: number;
  updated_at: number;
}

interface AlertHttpResponse {
  ok: boolean;
  status: number;
  headers: { get(name: string): string | null };
  body?: { cancel(): Promise<void> } | null;
}
type AlertSender = (url: string, init: RequestInit) => Promise<AlertHttpResponse>;

const maxAttempts = 8;
const leaseMs = 60_000;
const defaultWindowMs = 15 * 60_000;
const maxRetryMs = 6 * 60 * 60_000;

function demand(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function codeLabel(value: string) {
  demand(
    typeof value === "string" && /^[A-Z0-9_.:-]{1,80}$/.test(value),
    "Operational alert code must be a bounded machine label.",
  );
  return value;
}

function webhookUrl(value: string | undefined) {
  if (!value) return undefined;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("Operational alert webhook must be a valid HTTPS URL.");
  }
  demand(
    parsed.protocol === "https:" &&
      !parsed.username &&
      !parsed.password &&
      value.length <= 2048,
    "Operational alert webhook must be a bounded HTTPS URL without credentials.",
  );
  return parsed.href;
}

async function sha256(value: string) {
  const bytes = new Uint8Array(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
  );
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function retryDelayMs(attempt: number, retryAfterSeconds?: number) {
  if (
    Number.isFinite(retryAfterSeconds) &&
    retryAfterSeconds! >= 1 &&
    retryAfterSeconds! <= maxRetryMs / 1000
  )
    return retryAfterSeconds! * 1000;
  const exponent = Math.max(0, Math.min(8, attempt - 1));
  const base = Math.min(maxRetryMs, 60_000 * 2 ** exponent);
  return Math.min(maxRetryMs, base + ((attempt * 7919) % 30_000));
}

function retryAfter(response: AlertHttpResponse, now: number) {
  const raw = response.headers.get("retry-after")?.trim();
  if (!raw) return undefined;
  if (/^\d+(?:\.\d+)?$/.test(raw)) {
    const seconds = Number(raw);
    return Number.isFinite(seconds) ? seconds : undefined;
  }
  const date = Date.parse(raw);
  if (!Number.isFinite(date)) return undefined;
  return Math.max(1, Math.ceil((date - now) / 1000));
}

export async function enqueueOperationalAlert(
  env: Env,
  input: {
    class: OperationalAlertClass;
    severity: OperationalAlertSeverity;
    code: string;
    subject?: string;
    dedupe?: string;
    windowMs?: number;
  },
  now = Date.now(),
) {
  demand(
    operationalAlertClasses.includes(input.class),
    "Unknown operational alert class.",
  );
  demand(
    input.severity === "warning" || input.severity === "critical",
    "Unknown operational alert severity.",
  );
  const code = codeLabel(input.code);
  const windowMs = input.windowMs || defaultWindowMs;
  demand(
    Number.isInteger(windowMs) && windowMs >= 60_000 && windowMs <= 24 * 60 * 60_000,
    "Operational alert dedupe window is invalid.",
  );
  const bucket = Math.floor(now / windowMs);
  const subject = input.subject || "global";
  const subjectFingerprint = input.subject
    ? (await sha256(input.subject)).slice(0, 24)
    : null;
  // Severity is part of event identity. Repeated warnings dedupe, while a
  // critical escalation cannot disappear behind an already-delivered warning.
  const dedupeKey = await sha256(
    [
      input.class,
      input.severity,
      code,
      input.dedupe || subject,
      String(bucket),
    ].join("\0"),
  );
  const id = crypto.randomUUID();
  const result = await env.IDENTITY.prepare(
    `INSERT INTO operational_alerts(
      id,dedupe_key,class,severity,code,release,subject_fingerprint,occurrences,
      first_seen_at,last_seen_at,status,attempts,next_attempt_at,lease_until,
      sent_at,last_http_status,last_error_code,created_at,updated_at
    ) VALUES (?,?,?,?,?,?,?,1,?,?,'pending',0,?,NULL,NULL,NULL,NULL,?,?)
    ON CONFLICT(dedupe_key) DO UPDATE SET
      occurrences=operational_alerts.occurrences+1,
      last_seen_at=excluded.last_seen_at,
      release=excluded.release,
      updated_at=excluded.updated_at
    RETURNING id,dedupe_key,class,severity,code,release,subject_fingerprint,
      occurrences,first_seen_at,last_seen_at,status,attempts,next_attempt_at,
      lease_until,sent_at,last_http_status,last_error_code,created_at,updated_at`,
  )
    .bind(
      id,
      dedupeKey,
      input.class,
      input.severity,
      code,
      env.RELEASE_SHA,
      subjectFingerprint,
      now,
      now,
      now,
      now,
      now,
    )
    .first<AlertRow>();
  demand(result, "Operational alert could not be durably recorded.");
  return publicAlert(result);
}

function publicAlert(row: AlertRow) {
  return {
    id: row.id,
    class: row.class,
    severity: row.severity,
    code: row.code,
    release: row.release,
    subjectFingerprint: row.subject_fingerprint,
    occurrences: row.occurrences,
    firstSeenAt: row.first_seen_at,
    lastSeenAt: row.last_seen_at,
    status: row.status,
    attempts: row.attempts,
    nextAttemptAt: row.next_attempt_at,
    sentAt: row.sent_at,
    lastHttpStatus: row.last_http_status,
    lastErrorCode: row.last_error_code,
  };
}

async function claimDueAlert(env: Env, id: string, now: number) {
  return env.IDENTITY.prepare(
    `UPDATE operational_alerts SET
       status='sending', attempts=attempts+1, lease_until=?, updated_at=?
     WHERE id=? AND attempts < ? AND (
       (status='pending' AND next_attempt_at<=?) OR
       (status='sending' AND lease_until IS NOT NULL AND lease_until<=?)
     )
     RETURNING id,dedupe_key,class,severity,code,release,subject_fingerprint,
       occurrences,first_seen_at,last_seen_at,status,attempts,next_attempt_at,
       lease_until,sent_at,last_http_status,last_error_code,created_at,updated_at`,
  )
    .bind(now + leaseMs, now, id, maxAttempts, now, now)
    .first<AlertRow>();
}

async function markSent(env: Env, row: AlertRow, status: number, now: number) {
  await env.IDENTITY.prepare(
    `UPDATE operational_alerts SET status='sent',sent_at=?,lease_until=NULL,
       last_http_status=?,last_error_code=NULL,updated_at=?
     WHERE id=? AND status='sending' AND attempts=?`,
  )
    .bind(now, status, now, row.id, row.attempts)
    .run();
}

async function markFailure(
  env: Env,
  row: AlertRow,
  options: {
    status?: number;
    code: string;
    retryable: boolean;
    retryAfterSeconds?: number;
  },
  now: number,
) {
  const exhausted = row.attempts >= maxAttempts;
  const nextStatus = options.retryable && !exhausted ? "pending" : "dead";
  const nextAttempt =
    nextStatus === "pending"
      ? now + retryDelayMs(row.attempts, options.retryAfterSeconds)
      : row.next_attempt_at;
  await env.IDENTITY.prepare(
    `UPDATE operational_alerts SET status=?,next_attempt_at=?,lease_until=NULL,
       last_http_status=?,last_error_code=?,updated_at=?
     WHERE id=? AND status='sending' AND attempts=?`,
  )
    .bind(
      nextStatus,
      nextAttempt,
      options.status || null,
      codeLabel(options.code),
      now,
      row.id,
      row.attempts,
    )
    .run();
}

function alertPayload(row: AlertRow) {
  return {
    schemaVersion: 1,
    alertId: row.id,
    class: row.class,
    severity: row.severity,
    code: row.code,
    release: row.release,
    subjectFingerprint: row.subject_fingerprint,
    occurrences: row.occurrences,
    firstSeenAt: row.first_seen_at,
    lastSeenAt: row.last_seen_at,
    attempt: row.attempts,
  };
}

export async function flushOperationalAlerts(
  env: Env,
  options: {
    now?: number;
    limit?: number;
    send?: AlertSender;
  } = {},
) {
  const url = webhookUrl(env.OPERATIONAL_ALERT_WEBHOOK_URL);
  if (!url)
    return {
      configured: false,
      considered: 0,
      claimed: 0,
      sent: 0,
      retried: 0,
      dead: 0,
    };

  const now = options.now ?? Date.now();
  const limit = Math.max(1, Math.min(50, options.limit || 20));
  const send: AlertSender =
    options.send || ((target, init) => fetch(target, init));
  const due = await env.IDENTITY.prepare(
    `SELECT id FROM operational_alerts
     WHERE attempts < ? AND (
       (status='pending' AND next_attempt_at<=?) OR
       (status='sending' AND lease_until IS NOT NULL AND lease_until<=?)
     )
     ORDER BY next_attempt_at ASC,created_at ASC,id ASC LIMIT ?`,
  )
    .bind(maxAttempts, now, now, limit)
    .all<{ id: string }>();

  const counts = {
    configured: true,
    considered: due.results?.length || 0,
    claimed: 0,
    sent: 0,
    retried: 0,
    dead: 0,
  };
  for (const candidate of due.results || []) {
    const row = await claimDueAlert(env, candidate.id, now);
    if (!row) continue;
    counts.claimed++;
    try {
      const response = await send(url, {
        method: "POST",
        redirect: "error",
        signal: AbortSignal.timeout(10_000),
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": row.id,
          "X-PostSteward-Alert-Class": row.class,
          "X-PostSteward-Release": row.release,
          ...(env.OPERATIONAL_ALERT_WEBHOOK_TOKEN
            ? { Authorization: `Bearer ${env.OPERATIONAL_ALERT_WEBHOOK_TOKEN}` }
            : {}),
        },
        body: JSON.stringify(alertPayload(row)),
      });
      const status = response.status;
      const retryable =
        status === 408 || status === 425 || status === 429 || status >= 500;
      if (response.ok) {
        await markSent(env, row, status, now);
        counts.sent++;
      } else {
        await markFailure(
          env,
          row,
          {
            status,
            code: retryable
              ? "WEBHOOK_RETRYABLE_HTTP"
              : "WEBHOOK_PERMANENT_HTTP",
            retryable,
            retryAfterSeconds:
              status === 429 ? retryAfter(response, now) : undefined,
          },
          now,
        );
        if (retryable && row.attempts < maxAttempts) counts.retried++;
        else counts.dead++;
      }
      void response.body?.cancel().catch(() => {});
    } catch {
      await markFailure(
        env,
        row,
        { code: "WEBHOOK_NETWORK_ERROR", retryable: true },
        now,
      );
      if (row.attempts < maxAttempts) counts.retried++;
      else counts.dead++;
    }
  }
  return counts;
}

export async function sweepOperationalConditions(env: Env, now = Date.now()) {
  const queued = [];
  const staleRecoveryBefore = now - 15 * 60_000;
  const recovery = await env.IDENTITY.prepare(
    `SELECT id,workspace,state FROM workspace_recovery_plans
     WHERE state IN ('prepared','armed','reconciled') AND updated_at<=?
     ORDER BY updated_at ASC LIMIT 100`,
  )
    .bind(staleRecoveryBefore)
    .all<{ id: string; workspace: string; state: string }>();
  for (const row of recovery.results || [])
    queued.push(
      await enqueueOperationalAlert(
        env,
        {
          class: "recovery_state",
          severity: row.state === "armed" ? "critical" : "warning",
          code: `RECOVERY_${row.state.toUpperCase()}_STALE`,
          subject: `${row.workspace}:${row.id}`,
          dedupe: `${row.workspace}:${row.id}:${row.state}`,
          windowMs: 60 * 60_000,
        },
        now,
      ),
    );

  const uncertainBefore = now - 5 * 60_000;
  const uncertain = await env.IDENTITY.prepare(
    `SELECT workspace,fingerprint,provider FROM external_effects
     WHERE status='uncertain' AND updated_at<=?
     ORDER BY updated_at ASC LIMIT 100`,
  )
    .bind(uncertainBefore)
    .all<{ workspace: string; fingerprint: string; provider: string }>();
  for (const row of uncertain.results || [])
    queued.push(
      await enqueueOperationalAlert(
        env,
        {
          class: "provider_or_oauth_failure",
          severity: "critical",
          code: "AMBIGUOUS_PROVIDER_EFFECT",
          subject: `${row.workspace}:${row.provider}:${row.fingerprint}`,
          dedupe: `${row.workspace}:${row.fingerprint}`,
          windowMs: 60 * 60_000,
        },
        now,
      ),
    );

  try {
    const stripeBefore = now - 10 * 60_000;
    const incomplete = await env.IDENTITY.prepare(
      `SELECT id,workspace FROM stripe_events
       WHERE completed_at IS NULL AND received_at<=?
       ORDER BY received_at ASC LIMIT 100`,
    )
      .bind(stripeBefore)
      .all<{ id: string; workspace: string }>();
    for (const row of incomplete.results || [])
      queued.push(
        await enqueueOperationalAlert(
          env,
          {
            class: "stripe_reconciliation",
            severity: "critical",
            code: "STRIPE_EVENT_RECONCILIATION_STALE",
            subject: `${row.workspace}:${row.id}`,
            dedupe: row.id,
            windowMs: 60 * 60_000,
          },
          now,
        ),
      );
  } catch {
    // Platform/D1 errors are surfaced by Cloudflare-level monitoring; a failed
    // D1 read cannot reliably enqueue another D1-backed alert about itself.
  }

  return { queued: queued.length };
}
