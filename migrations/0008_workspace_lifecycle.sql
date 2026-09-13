CREATE TABLE workspace_deletions (
  workspace TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('pending','completed')),
  requested_at INTEGER NOT NULL,
  completed_at INTEGER,
  updated_at INTEGER NOT NULL
);
CREATE INDEX workspace_deletions_state ON workspace_deletions(state, updated_at);
