CREATE TABLE operational_alerts (
  id TEXT PRIMARY KEY,
  dedupe_key TEXT NOT NULL UNIQUE,
  class TEXT NOT NULL CHECK (class IN (
    'provider_or_oauth_failure',
    'recovery_state',
    'stripe_reconciliation',
    'capacity_threshold'
  )),
  severity TEXT NOT NULL CHECK (severity IN ('warning','critical')),
  code TEXT NOT NULL,
  release TEXT NOT NULL,
  subject_fingerprint TEXT,
  occurrences INTEGER NOT NULL DEFAULT 1 CHECK (occurrences >= 1),
  first_seen_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','sending','sent','dead')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_attempt_at INTEGER NOT NULL,
  lease_until INTEGER,
  sent_at INTEGER,
  last_http_status INTEGER,
  last_error_code TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX operational_alerts_delivery_due
  ON operational_alerts(status, next_attempt_at, lease_until, created_at);
CREATE INDEX operational_alerts_class_status
  ON operational_alerts(class, status, last_seen_at);
