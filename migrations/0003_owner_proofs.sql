-- Additive expansion: preserve the original login_states columns so the
-- currently deployed Worker and a rollback can continue their existing flow.
CREATE TABLE login_return_paths (
  state_hash TEXT PRIMARY KEY REFERENCES login_states(state_hash) ON DELETE CASCADE,
  return_path TEXT NOT NULL CHECK (return_path IN ('/app', '/pilot'))
);
CREATE TABLE owner_proofs (
  session_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
  id TEXT NOT NULL UNIQUE,
  issuer TEXT NOT NULL,
  client_id TEXT NOT NULL,
  email_hash TEXT NOT NULL,
  email_verified INTEGER NOT NULL CHECK (email_verified IN (0, 1)),
  authenticated_at INTEGER NOT NULL,
  release TEXT NOT NULL
);
