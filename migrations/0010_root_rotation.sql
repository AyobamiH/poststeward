CREATE TABLE root_rotation_runs (
  rotation_id TEXT PRIMARY KEY,
  source_release TEXT NOT NULL,
  target_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','ready_for_cutover','verifying','completed')),
  workspace_total INTEGER NOT NULL DEFAULT 0 CHECK (workspace_total >= 0),
  workspace_completed INTEGER NOT NULL DEFAULT 0 CHECK (workspace_completed >= 0),
  workspace_credentials INTEGER NOT NULL DEFAULT 0 CHECK (workspace_credentials >= 0),
  github_total INTEGER NOT NULL DEFAULT 0 CHECK (github_total >= 0),
  github_completed INTEGER NOT NULL DEFAULT 0 CHECK (github_completed >= 0),
  github_credentials INTEGER NOT NULL DEFAULT 0 CHECK (github_credentials >= 0),
  started_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  completed_at INTEGER,
  last_error TEXT,
  CHECK (completed_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE root_rotation_workspaces (
  rotation_id TEXT NOT NULL REFERENCES root_rotation_runs(rotation_id) ON DELETE CASCADE,
  workspace TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','rewrapped','verified')),
  credential_count INTEGER NOT NULL DEFAULT 0 CHECK (credential_count >= 0),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (rotation_id, workspace)
);
CREATE INDEX root_rotation_workspaces_status
  ON root_rotation_workspaces(rotation_id, status, workspace);

CREATE TABLE root_rotation_github (
  rotation_id TEXT NOT NULL REFERENCES root_rotation_runs(rotation_id) ON DELETE CASCADE,
  workspace TEXT NOT NULL,
  installation_id INTEGER NOT NULL CHECK (installation_id > 0),
  status TEXT NOT NULL CHECK (status IN ('pending','rewrapped','verified')),
  credential_count INTEGER NOT NULL DEFAULT 0 CHECK (credential_count IN (0,1)),
  credential_revision INTEGER,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (rotation_id, workspace, installation_id)
);
CREATE INDEX root_rotation_github_status
  ON root_rotation_github(rotation_id, status, workspace, installation_id);
