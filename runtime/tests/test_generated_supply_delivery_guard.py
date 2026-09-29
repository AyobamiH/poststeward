from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from ocpf_post.generated_supply_guard import admission_state, delivery_guard, editorial_advisories
from ocpf_post.portfolio import delivery_candidates

NOW = datetime(2026, 9, 24, 13, 30, tzinfo=timezone.utc)
ACCOUNT = "urn:li:person:test"


def linkedin_copy() -> str:
    first = "A generated LinkedIn post should preserve explicit source, deployment and provider evidence boundaries."
    second = first
    while len(first) + 2 + len(second) < 360:
        second += " " + first
    return f"{first}\n\n{second}"


def manifest(*, admission=True) -> dict:
    value = {
        "campaign": "OCPF-GEN-LEGACY",
        "project": "oneclickpostfactory",
        "title": "Generated route",
        "status": "COPY-READY",
        "providers": ["linkedin"],
        "runtime_generated": True,
        "payload_frozen": True,
        "source": {"type": "evidence_grounded_generation", "source_sha": "a" * 40},
        "allocation": {
            "enabled": True,
            "lane": "evergreen",
            "priority": 72,
            "prepared_at": "2026-09-24T10:00:00Z",
            "expires_at": "2026-09-30T10:00:00Z",
        },
    }
    if admission:
        value["admission"] = {
            "schema_version": 1,
            "gate": "scoped_admission",
            "providers": {
                "linkedin": {
                    "account_id": ACCOUNT,
                    "admitted_at": "2026-09-24T10:00:00Z",
                    "scope": f"linkedin:{ACCOUNT}",
                }
            },
        }
    return value


class GeneratedSupplyDeliveryGuardTests(unittest.TestCase):
    def test_admission_state_distinguishes_legacy_invalid_and_attested(self):
        state, _ = admission_state(manifest(admission=False), "linkedin", ACCOUNT)
        self.assertEqual(state, "legacy_unattested")

        invalid = manifest()
        invalid["admission"]["providers"]["linkedin"]["account_id"] = "other"
        state, evidence = admission_state(invalid, "linkedin", ACCOUNT)
        self.assertEqual(state, "invalid_attestation")
        self.assertEqual(evidence["recorded_account_id"], "other")

        state, evidence = admission_state(manifest(), "linkedin", ACCOUNT)
        self.assertEqual(state, "attested")
        self.assertEqual(evidence["recorded_account_id"], ACCOUNT)

    def test_linkedin_editorial_targets_are_advisory_not_delivery_authority(self):
        dense = ("Dense generated LinkedIn copy can still be a complete intended payload. " * 8).strip()
        self.assertIn("linkedin_single_paragraph", editorial_advisories("linkedin", dense))
        self.assertIsNone(delivery_guard(manifest(), "linkedin", ACCOUNT, dense))

        short = "A complete but concise LinkedIn payload."
        self.assertIn("linkedin_below_editorial_target", editorial_advisories("linkedin", short))
        self.assertIsNone(delivery_guard(manifest(), "linkedin", ACCOUNT, short))

    def _candidates(self, value: dict, text: str):
        digest = hashlib.sha256(text.encode()).hexdigest()
        exclusions = []
        with patch("ocpf_post.portfolio._campaign_ids", return_value=["OCPF-GEN-LEGACY"]), \
             patch("ocpf_post.portfolio.builtin_manifest", return_value=value), \
             patch("ocpf_post.portfolio.builtin_text", return_value=text), \
             patch("ocpf_post.portfolio.schedule_records", return_value=[]), \
             patch("ocpf_post.portfolio.iter_receipts", return_value=[]), \
             patch("ocpf_post.source_guard.guard", return_value=None), \
             patch("ocpf_post.vault_sync.guard", return_value=None), \
             patch("ocpf_post.copy_guard.occupied_copies", return_value={}), \
             patch("ocpf_post.copy_guard.campaign_copy_key", return_value=("linkedin", ACCOUNT, digest)), \
             patch("ocpf_post.account_profiles.unavailable", return_value=None):
            rows = delivery_candidates(now=NOW, exclusions=exclusions)
        return rows, exclusions

    def test_allocator_rejects_legacy_unattested_generated_inventory(self):
        rows, exclusions = self._candidates(manifest(admission=False), linkedin_copy())
        self.assertEqual(rows, [])
        self.assertEqual(exclusions[-1]["reason"], "Generated route lacks scoped admission attestation")

    def test_allocator_accepts_attested_dense_linkedin_copy_despite_editorial_advisory(self):
        dense = ("Dense generated LinkedIn copy can still be complete and must not be confused with provider truncation. " * 7).strip()
        rows, exclusions = self._candidates(manifest(), dense)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["account_id"], ACCOUNT)
        self.assertEqual(exclusions, [])

    def test_allocator_accepts_attested_current_quality_generated_inventory(self):
        rows, exclusions = self._candidates(manifest(), linkedin_copy())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["account_id"], ACCOUNT)
        self.assertEqual(exclusions, [])


if __name__ == "__main__":
    unittest.main()
