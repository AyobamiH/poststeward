CREATE TABLE provider_identity_bindings (
  provider TEXT NOT NULL CHECK (provider IN ('x','threads','linkedin')),
  identity_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  alias TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (provider, identity_id, workspace, alias)
);
CREATE INDEX provider_identity_bindings_lookup
  ON provider_identity_bindings(provider, identity_id);
CREATE INDEX provider_identity_bindings_workspace
  ON provider_identity_bindings(workspace, provider, alias);

CREATE TABLE provider_deletion_requests (
  confirmation_code TEXT PRIMARY KEY,
  provider TEXT NOT NULL CHECK (provider IN ('threads')),
  identity_hash TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('completed')),
  matched_accounts INTEGER NOT NULL,
  requested_at INTEGER NOT NULL,
  completed_at INTEGER NOT NULL
);
CREATE INDEX provider_deletion_requests_identity
  ON provider_deletion_requests(provider, identity_hash, completed_at);
