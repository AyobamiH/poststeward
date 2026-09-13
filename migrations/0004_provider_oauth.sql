CREATE TABLE provider_oauth_states (
  state_hash TEXT PRIMARY KEY,
  session_hash TEXT NOT NULL REFERENCES sessions(token_hash) ON DELETE CASCADE,
  workspace TEXT NOT NULL,
  actor TEXT NOT NULL,
  provider TEXT NOT NULL CHECK (provider IN ('x','threads','linkedin')),
  alias TEXT NOT NULL,
  verifier TEXT,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX provider_oauth_states_expiry ON provider_oauth_states(expires_at);
CREATE INDEX provider_oauth_states_session ON provider_oauth_states(session_hash);
