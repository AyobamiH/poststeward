CREATE TABLE github_install_states (
  state_hash TEXT PRIMARY KEY,
  session_hash TEXT NOT NULL REFERENCES sessions(token_hash) ON DELETE CASCADE,
  workspace TEXT NOT NULL,
  actor TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX github_install_states_expiry ON github_install_states(expires_at);
CREATE INDEX github_install_states_session ON github_install_states(session_hash);

CREATE TABLE github_installations (
  workspace TEXT NOT NULL,
  installation_id INTEGER NOT NULL,
  account_id INTEGER NOT NULL,
  account_login TEXT NOT NULL,
  account_type TEXT NOT NULL,
  repository_selection TEXT NOT NULL CHECK (repository_selection IN ('all','selected')),
  status TEXT NOT NULL CHECK (status IN ('syncing','linked','stale')),
  linked_at INTEGER NOT NULL,
  last_verified_at INTEGER,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (workspace, installation_id)
);
CREATE INDEX github_installations_workspace ON github_installations(workspace);

CREATE TABLE github_repository_links (
  workspace TEXT NOT NULL,
  repository_id INTEGER NOT NULL,
  installation_id INTEGER NOT NULL,
  full_name TEXT NOT NULL,
  private INTEGER NOT NULL CHECK (private IN (0,1)),
  linked_at INTEGER NOT NULL,
  verified_at INTEGER,
  PRIMARY KEY (workspace, repository_id),
  UNIQUE (workspace, full_name)
);
CREATE INDEX github_repository_links_installation ON github_repository_links(workspace, installation_id);
