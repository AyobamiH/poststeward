CREATE TABLE workspace_recovery_checkpoints (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  bookmark TEXT NOT NULL,
  captured_at INTEGER NOT NULL,
  release TEXT NOT NULL,
  root_write TEXT NOT NULL,
  state_digest TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('automatic','owner','release')),
  created_at INTEGER NOT NULL
);
CREATE INDEX workspace_recovery_checkpoints_workspace_time
  ON workspace_recovery_checkpoints(workspace, captured_at DESC);

ALTER TABLE workspace_recovery_plans ADD COLUMN checkpoint_id TEXT;
ALTER TABLE workspace_recovery_plans ADD COLUMN target_mode TEXT NOT NULL DEFAULT 'approximate_time';
