CREATE TABLE external_effects (
  workspace TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  delivery_id TEXT NOT NULL,
  provider TEXT NOT NULL CHECK (provider IN ('x','threads','linkedin')),
  text_digest TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('intent','uncertain','created','verified','unverified')),
  claim_id TEXT,
  post_id TEXT,
  url TEXT,
  reason TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (workspace, fingerprint)
);
CREATE INDEX external_effects_workspace_status ON external_effects(workspace, status);
CREATE INDEX external_effects_workspace_delivery ON external_effects(workspace, delivery_id);

CREATE TABLE workspace_controls (
  workspace TEXT PRIMARY KEY,
  publishing_quarantined INTEGER NOT NULL DEFAULT 0 CHECK (publishing_quarantined IN (0,1)),
  reason TEXT,
  quarantined_at INTEGER,
  updated_at INTEGER NOT NULL
);
