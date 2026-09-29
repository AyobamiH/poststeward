CREATE TABLE runtime_pairings (
  id TEXT PRIMARY KEY,
  poll_token_hash TEXT NOT NULL UNIQUE,
  user_code_hash TEXT NOT NULL,
  installation_id TEXT NOT NULL,
  label TEXT NOT NULL,
  platform TEXT NOT NULL,
  runtime_version TEXT NOT NULL,
  source_revision TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending','approved','claimed','expired','revoked')),
  workspace TEXT,
  approved_by TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  approved_at INTEGER,
  claimed_at INTEGER
);
CREATE INDEX runtime_pairings_expires_at ON runtime_pairings(expires_at);
CREATE INDEX runtime_pairings_installation ON runtime_pairings(installation_id);
CREATE INDEX runtime_pairings_workspace_status ON runtime_pairings(workspace,status);

CREATE TABLE runtime_installations (
  installation_id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  label TEXT NOT NULL,
  platform TEXT NOT NULL,
  runtime_version TEXT NOT NULL,
  source_revision TEXT,
  token_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('active','revoked')),
  created_at INTEGER NOT NULL,
  approved_at INTEGER NOT NULL,
  token_expires_at INTEGER NOT NULL,
  last_seen_at INTEGER,
  revoked_at INTEGER
);
CREATE INDEX runtime_installations_workspace_status
  ON runtime_installations(workspace,status);
CREATE INDEX runtime_installations_token_expiry
  ON runtime_installations(token_expires_at);

CREATE TABLE workspace_executors (
  workspace TEXT PRIMARY KEY,
  executor_mode TEXT NOT NULL CHECK (executor_mode IN ('hosted','local')),
  active_installation_id TEXT,
  authority_generation INTEGER NOT NULL CHECK (authority_generation >= 1),
  lease_expires_at INTEGER,
  updated_at INTEGER NOT NULL,
  reason TEXT,
  CHECK (
    (executor_mode='hosted' AND active_installation_id IS NULL AND lease_expires_at IS NULL)
    OR
    (executor_mode='local' AND active_installation_id IS NOT NULL)
  )
);
