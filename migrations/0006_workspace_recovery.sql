CREATE TABLE workspace_recovery_plans (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  actor TEXT NOT NULL,
  target_time INTEGER NOT NULL,
  target_bookmark TEXT NOT NULL,
  pre_restore_bookmark TEXT NOT NULL,
  undo_bookmark TEXT,
  reason TEXT NOT NULL,
  digest TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('prepared','armed','reconciled','cancelled')),
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX workspace_recovery_plans_workspace_state ON workspace_recovery_plans(workspace, state, created_at);
