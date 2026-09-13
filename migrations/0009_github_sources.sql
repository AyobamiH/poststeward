CREATE TABLE github_install_states (
  state_hash TEXT PRIMARY KEY,
  session_hash TEXT NOT NULL REFERENCES sessions(token_hash) ON DELETE CASCADE,
  workspace TEXT NOT NULL,
  actor TEXT NOT NULL,
  verifier TEXT NOT NULL,
  installation_id INTEGER,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  CHECK (length(verifier) BETWEEN 43 AND 128),
  CHECK (installation_id IS NULL OR installation_id > 0)
);
CREATE INDEX github_install_states_expiry ON github_install_states(expires_at);
CREATE INDEX github_install_states_session ON github_install_states(session_hash);

CREATE TABLE github_installations (
  workspace TEXT NOT NULL,
  installation_id INTEGER NOT NULL,
  account_id INTEGER NOT NULL,
  account_login TEXT NOT NULL,
  account_type TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  user_login TEXT NOT NULL,
  repository_selection TEXT NOT NULL CHECK (repository_selection IN ('all','selected')),
  status TEXT NOT NULL CHECK (status IN ('linked','stale')),
  last_error TEXT,
  credential TEXT NOT NULL,
  credential_revision INTEGER NOT NULL DEFAULT 1 CHECK (credential_revision > 0),
  refresh_lease TEXT,
  refresh_lease_until INTEGER,
  token_expires_at INTEGER NOT NULL,
  refresh_expires_at INTEGER NOT NULL,
  linked_at INTEGER NOT NULL,
  last_verified_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (workspace, installation_id),
  CHECK (
    (refresh_lease IS NULL AND refresh_lease_until IS NULL) OR
    (
      refresh_lease IS NOT NULL AND
      length(refresh_lease) BETWEEN 16 AND 100 AND
      refresh_lease_until IS NOT NULL AND
      refresh_lease_until > 0
    )
  )
);
CREATE INDEX github_installations_workspace ON github_installations(workspace);
CREATE INDEX github_installations_user ON github_installations(workspace, user_id);

CREATE TABLE github_repository_links (
  workspace TEXT NOT NULL,
  repository_id INTEGER NOT NULL,
  installation_id INTEGER NOT NULL,
  full_name TEXT NOT NULL,
  private INTEGER NOT NULL CHECK (private IN (0,1)),
  linked_at INTEGER NOT NULL,
  verified_at INTEGER NOT NULL,
  PRIMARY KEY (workspace, repository_id),
  UNIQUE (workspace, full_name),
  FOREIGN KEY (workspace, installation_id)
    REFERENCES github_installations(workspace, installation_id)
    ON DELETE CASCADE
);
CREATE INDEX github_repository_links_installation ON github_repository_links(workspace, installation_id);
CREATE INDEX github_repository_links_name ON github_repository_links(workspace, full_name);
