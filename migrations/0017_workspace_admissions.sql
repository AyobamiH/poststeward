CREATE TABLE workspace_admissions (
  workspace TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  admission_mode TEXT NOT NULL CHECK (admission_mode IN ('preexisting','restricted','public'))
);
CREATE INDEX workspace_admissions_created_at
  ON workspace_admissions(created_at);

INSERT OR IGNORE INTO workspace_admissions(workspace,created_at,admission_mode)
SELECT workspace,created_at,'preexisting' FROM principals;
