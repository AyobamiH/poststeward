CREATE TABLE workspace_capacity_observations (
  workspace_fingerprint TEXT NOT NULL,
  observation_date TEXT NOT NULL,
  observed_at INTEGER NOT NULL,
  release TEXT NOT NULL,
  records INTEGER NOT NULL CHECK (records >= 0),
  bytes INTEGER NOT NULL CHECK (bytes >= 0),
  max_value_bytes INTEGER NOT NULL CHECK (max_value_bytes >= 0),
  daily_deliveries INTEGER NOT NULL CHECK (daily_deliveries >= 0),
  active_schedules INTEGER NOT NULL CHECK (active_schedules >= 0),
  source_profiles INTEGER NOT NULL CHECK (source_profiles >= 0),
  workspace_requests INTEGER NOT NULL CHECK (workspace_requests >= 0),
  alarm_cycles INTEGER NOT NULL CHECK (alarm_cycles >= 0),
  PRIMARY KEY (workspace_fingerprint, observation_date)
);

CREATE INDEX workspace_capacity_observations_recent
  ON workspace_capacity_observations(observation_date, observed_at);
