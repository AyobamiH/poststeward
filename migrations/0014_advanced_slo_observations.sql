CREATE TABLE advanced_slo_events (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN (
    'advanced_operation',
    'source_change',
    'inventory_snapshot',
    'spaced_allocation',
    'publication_eligible',
    'verified_readback',
    'schedule_observation',
    'provider_reconciliation',
    'oauth_refresh_attempt',
    'oauth_refresh_success',
    'scheduled_metrics_capture',
    'webhook_reconciliation'
  )),
  observed_at INTEGER NOT NULL,
  value INTEGER NOT NULL DEFAULT 1,
  duration_ms INTEGER,
  release TEXT NOT NULL,
  canary_bps INTEGER NOT NULL CHECK (canary_bps BETWEEN 1 AND 1000)
);
CREATE INDEX advanced_slo_events_release_time
  ON advanced_slo_events(release, observed_at);
CREATE INDEX advanced_slo_events_type_time
  ON advanced_slo_events(event_type, observed_at);
CREATE INDEX advanced_slo_events_workspace_time
  ON advanced_slo_events(workspace, observed_at);

CREATE TABLE advanced_canary_runs (
  id TEXT PRIMARY KEY,
  release TEXT NOT NULL,
  canary_bps INTEGER NOT NULL CHECK (canary_bps BETWEEN 1 AND 1000),
  seed_hash TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  stopped_at INTEGER,
  created_at INTEGER NOT NULL
);
CREATE INDEX advanced_canary_runs_started
  ON advanced_canary_runs(started_at DESC);
CREATE INDEX advanced_canary_runs_active
  ON advanced_canary_runs(stopped_at, started_at DESC);
