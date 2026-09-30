CREATE TABLE runtime_commands (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  installation_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  actor TEXT NOT NULL,
  grant_hash TEXT,
  scope TEXT NOT NULL,
  operation TEXT NOT NULL,
  input TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','claimed','completed','failed','expired')),
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  claimed_at INTEGER,
  completed_at INTEGER,
  result TEXT,
  UNIQUE(workspace,actor,idempotency_key)
);
CREATE INDEX runtime_commands_poll ON runtime_commands(workspace,installation_id,generation,status,created_at);
CREATE TABLE runtime_transitions (
  workspace TEXT NOT NULL,
  generation INTEGER NOT NULL,
  installation_id TEXT NOT NULL,
  source_installation_id TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK(purpose IN ('migrate','recover')),
  review_sha256 TEXT NOT NULL,
  reviewed_at INTEGER NOT NULL,
  PRIMARY KEY(workspace,generation)
);
