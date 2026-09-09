ALTER TABLE login_states ADD COLUMN return_path TEXT NOT NULL DEFAULT '/app' CHECK (return_path IN ('/app', '/pilot'));
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
