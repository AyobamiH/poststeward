-- Additive source-only provenance migration. DO NOT execute without the
-- separate owner-approved staging/production migration release.
-- Historical evidence remains legacy/unverified; no backfill is fabricated.
ALTER TABLE advanced_slo_events ADD COLUMN canary_run_id TEXT;
ALTER TABLE advanced_slo_events
  ADD COLUMN evidence_origin TEXT NOT NULL DEFAULT 'legacy_unverified';
ALTER TABLE advanced_slo_events
  ADD COLUMN evidence_outcome TEXT NOT NULL DEFAULT 'unverified';
ALTER TABLE advanced_slo_events ADD COLUMN reference_hash TEXT;
CREATE UNIQUE INDEX advanced_slo_run_reference_unique
  ON advanced_slo_events(canary_run_id,workspace,event_type,reference_hash)
  WHERE canary_run_id IS NOT NULL AND reference_hash IS NOT NULL;
CREATE INDEX advanced_slo_run_origin
  ON advanced_slo_events(canary_run_id,event_type,evidence_origin,evidence_outcome);

-- The intent created_at is NOT the provider publication timestamp.
-- Only a successfully returned provider write sets published_at for new effects.
ALTER TABLE external_effects ADD COLUMN published_at INTEGER;
