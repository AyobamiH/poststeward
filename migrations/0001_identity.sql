CREATE TABLE principals (subject TEXT PRIMARY KEY, workspace TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, workspace TEXT NOT NULL, actor TEXT NOT NULL, expires_at INTEGER NOT NULL, csrf TEXT NOT NULL);
CREATE INDEX sessions_expiry ON sessions(expires_at);
CREATE TABLE grants (token_hash TEXT PRIMARY KEY, workspace TEXT NOT NULL, actor TEXT NOT NULL, scopes TEXT NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER);
CREATE INDEX grants_workspace ON grants(workspace);
CREATE TABLE login_states (state_hash TEXT PRIMARY KEY, verifier TEXT NOT NULL, nonce TEXT NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE stripe_customers (customer TEXT PRIMARY KEY, workspace TEXT NOT NULL UNIQUE);
CREATE TABLE stripe_events (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, received_at INTEGER NOT NULL, completed_at INTEGER);
