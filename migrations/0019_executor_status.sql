ALTER TABLE workspace_executors
  ADD COLUMN executor_status TEXT NOT NULL DEFAULT 'active'
  CHECK (executor_status IN ('active','inactive','recovery_review'));

CREATE INDEX workspace_executors_status
  ON workspace_executors(executor_status);
