from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from ocpf_post.provenance_audit import build

NOW = datetime(2026, 9, 24, 4, 30, tzinfo=timezone.utc)


class ProvenanceAuditTests(unittest.TestCase):
    def test_classifies_legacy_and_attested_generated_deliveries_without_mutation(self):
        candidates = [
            {"campaign": "GEN-OLD", "project": "p1", "provider": "x", "account_id": "1", "lane": "evergreen"},
            {"campaign": "GEN-NEW", "project": "p1", "provider": "threads", "account_id": "2", "lane": "evergreen"},
            {"campaign": "STATIC", "project": "p1", "provider": "linkedin", "account_id": "3", "lane": "commercial"},
        ]
        manifests = {
            "GEN-OLD": {"source": {"type": "evidence_grounded_generation", "source_sha": "a"}},
            "GEN-NEW": {
                "source": {"type": "evidence_grounded_generation", "source_sha": "b"},
                "admission": {
                    "schema_version": 1,
                    "gate": "scoped_admission",
                    "providers": {
                        "threads": {
                            "account_id": "2",
                            "admitted_at": "2026-09-24T04:00:00Z",
                            "scope": "threads:2",
                        }
                    },
                },
            },
            "STATIC": {"source": {"type": "repository_product_truth", "source_sha": "c"}},
        }
        with patch("ocpf_post.provenance_audit.delivery_candidates", return_value=candidates), \
             patch("ocpf_post.provenance_audit.builtin_manifest", side_effect=lambda cid: manifests[cid]), \
             patch("ocpf_post.provenance_audit.load_source_observations", return_value={
                 "schema_version": 1,
                 "projects": {
                     "p1": {"readme_sha": "b", "pending": []},
                 },
             }):
            result = build(now=NOW)
        self.assertEqual(result["eligible_delivery_count"], 3)
        self.assertEqual(result["generative_delivery_count"], 2)
        self.assertEqual(result["generative_attested_count"], 1)
        self.assertEqual(result["generative_legacy_unattested_count"], 1)
        self.assertEqual(result["generative_invalid_attestation_count"], 0)
        self.assertEqual(result["generative_source_revision_match_count"], 1)
        self.assertEqual(result["generative_source_revision_superseded_count"], 1)
        self.assertEqual(result["generative_source_revision_unavailable_count"], 0)
        self.assertEqual(result["admission_provenance_counts"]["not_applicable"], 1)
        self.assertEqual(result["boundary"].split(".")[0], "Read-only local provenance audit")


if __name__ == "__main__":
    unittest.main()
