from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import (
    learning_admission,
    learning_equivalence,
    learning_supply,
    performance,
    performance_feedback,
    performance_feedback_equivalent,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 13, 20, tzinfo=UTC)

CURRENT = "OCPF-AUTO-02I-NEWREV-X"
HISTORICAL = "OCPF-AUTO-02I-OLDREV"
SOURCE_CURRENT = "oneclickpostfactory-README-abcdef123456-2-insight"
SOURCE_OLD = "oneclickpostfactory-README-123456abcdef-2-insight"
PAYLOAD = "payload-sha"
ACCOUNT = "123"
POST = "post-1"


def current_manifest():
    return {
        "campaign": CURRENT,
        "project": "oneclickpostfactory",
        "providers": ["x"],
        "payload_frozen": True,
        "payload_sha256": {"x": PAYLOAD},
        "allocation": {"lane": "commercial", "enabled": True},
        "source": {
            "type": "repository_product_truth",
            "source_id": SOURCE_CURRENT,
            "source_sha": "abcdef1234567890",
            "comparison_variant": "practical",
        },
    }


def historical_manifest(*, index=2, payload=PAYLOAD):
    return {
        "campaign": HISTORICAL,
        "project": "oneclickpostfactory",
        "providers": ["x"],
        "payload_sha256": {"x": payload},
        "allocation": {"lane": "commercial"},
        "source": {
            "type": "repository_product_truth",
            "source_id": f"oneclickpostfactory-README-123456abcdef-{index}-insight",
            "source_sha": "123456abcdef0000",
        },
    }


def publication(*, hours=49):
    receipt = {
        "campaign": HISTORICAL,
        "provider": "x",
        "account_id": ACCOUNT,
        "post_id": POST,
        "status": "published_verified",
        "readback_verified": True,
        "text_sha256": PAYLOAD,
    }
    key = (HISTORICAL, "x", ACCOUNT, POST)
    return key, {
        "receipt": receipt,
        "at": NOW - timedelta(hours=hours),
        "verification_basis": "receipt",
    }


class LearningEffectEquivalenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=temp.name, OCPF_POST_CONFIG_DIR=temp.name)
        env.start(); self.addCleanup(env.stop)
        self.manifests = {CURRENT: current_manifest(), HISTORICAL: historical_manifest()}
        self.key, self.pub = publication()
        self.publications = {self.key: self.pub}
        manifest_patch = patch.object(
            learning_equivalence,
            "builtin_manifest",
            side_effect=lambda cid: self.manifests.get(cid, {}),
        )
        manifest_patch.start(); self.addCleanup(manifest_patch.stop)

    def projected_measurement(self):
        found = learning_equivalence.find_verified_equivalent(
            CURRENT, self.manifests[CURRENT], "x", ACCOUNT, self.publications
        )
        return {
            "campaign": HISTORICAL,
            "provider": "x",
            "account_id": ACCOUNT,
            "post_id": POST,
            "target_age_hours": 24,
            "exposure": 1000,
            "editorial": learning_equivalence.editorial_from_attestation(found["attestation"]),
        }

    def test_exact_verified_same_lineage_effect_is_reused_across_source_revision(self):
        found = learning_equivalence.find_verified_equivalent(
            CURRENT, self.manifests[CURRENT], "x", ACCOUNT, self.publications
        )
        self.assertEqual(found["status"], "verified_equivalent")
        att = found["attestation"]
        self.assertEqual(att["effect_campaign"], HISTORICAL)
        self.assertEqual(att["baseline_source_sha"], "abcdef1234567890")
        self.assertEqual(att["comparison_variant"], "practical")
        self.assertEqual(att["basis"], "verified_effect_exact_payload_stable_lineage")

    def test_wrong_inventory_slot_or_payload_fails_closed(self):
        self.manifests[HISTORICAL] = historical_manifest(index=3)
        self.assertEqual(
            learning_equivalence.find_verified_equivalent(
                CURRENT, self.manifests[CURRENT], "x", ACCOUNT, self.publications
            )["status"],
            "not_found",
        )
        self.manifests[HISTORICAL] = historical_manifest(payload="different")
        self.assertEqual(
            learning_equivalence.find_verified_equivalent(
                CURRENT, self.manifests[CURRENT], "x", ACCOUNT, self.publications
            )["status"],
            "not_found",
        )

    def test_direct_publication_does_not_equivalence_match_itself(self):
        direct_key = (CURRENT, "x", ACCOUNT, POST)
        direct = {direct_key: {**self.pub, "receipt": {**self.pub["receipt"], "campaign": CURRENT}}}
        self.assertEqual(
            learning_equivalence.find_verified_equivalent(
                CURRENT, self.manifests[CURRENT], "x", ACCOUNT, direct
            )["status"],
            "not_found",
        )

    def test_sidecar_is_idempotent_and_projects_current_frozen_editorial(self):
        found = learning_equivalence.find_verified_equivalent(
            CURRENT, self.manifests[CURRENT], "x", ACCOUNT, self.publications
        )
        first = learning_equivalence.record(found["attestation"], now=NOW)
        second = learning_equivalence.record(found["attestation"], now=NOW + timedelta(minutes=1))
        self.assertEqual(first["status"], "recorded")
        self.assertEqual(second["status"], "already_recorded")
        editorial = learning_equivalence.projected_editorial(
            HISTORICAL, "x", ACCOUNT, POST, PAYLOAD
        )
        self.assertEqual(editorial["variant"], "insight")
        self.assertEqual(editorial["revision"], "abcdef1234567890")
        self.assertEqual(editorial["comparison_variant"], "practical")
        self.assertEqual(editorial["historical_campaign"], HISTORICAL)

    def test_sampling_uses_equivalent_verified_effect_only_with_measurement(self):
        manifests = {CURRENT: self.manifests[CURRENT], HISTORICAL: self.manifests[HISTORICAL]}
        item = {
            "predecessor_source_id": SOURCE_CURRENT,
            "comparison_variant": "practical",
            "sha": "abcdef1234567890",
        }
        with patch.object(learning_supply, "enabled", return_value=True), \
             patch.object(learning_supply, "campaign_ids", return_value=list(manifests)), \
             patch.object(learning_supply, "builtin_manifest", side_effect=lambda cid: manifests[cid]), \
             patch.object(learning_supply, "publications", return_value=self.publications), \
             patch.object(learning_supply, "_feedback_observations", return_value=[self.projected_measurement()]):
            budget = learning_supply.SamplingBudget(NOW)
        self.assertIsNone(budget.reason(item, "x", ACCOUNT, "oneclickpostfactory"))

        with patch.object(learning_supply, "enabled", return_value=True), \
             patch.object(learning_supply, "campaign_ids", return_value=list(manifests)), \
             patch.object(learning_supply, "builtin_manifest", side_effect=lambda cid: manifests[cid]), \
             patch.object(learning_supply, "publications", return_value=self.publications), \
             patch.object(learning_supply, "_feedback_observations", return_value=[]):
            budget = learning_supply.SamplingBudget(NOW)
        self.assertEqual(
            budget.reason(item, "x", ACCOUNT, "oneclickpostfactory"),
            "predecessor_comparable_measurement_required",
        )

        key, pub = publication(hours=47)
        with patch.object(learning_supply, "enabled", return_value=True), \
             patch.object(learning_supply, "campaign_ids", return_value=list(manifests)), \
             patch.object(learning_supply, "builtin_manifest", side_effect=lambda cid: manifests[cid]), \
             patch.object(learning_supply, "publications", return_value={key: pub}), \
             patch.object(learning_supply, "_feedback_observations", return_value=[]):
            budget = learning_supply.SamplingBudget(NOW)
        self.assertEqual(
            budget.reason(item, "x", ACCOUNT, "oneclickpostfactory"),
            "sampling_spacing",
        )

    def test_equivalent_baseline_does_not_consume_learning_reserve_stock(self):
        budget = object.__new__(learning_admission.LearningBudget)
        budget.rows = [{
            "campaign": CURRENT,
            "provider": "x",
            "account_id": ACCOUNT,
            "project": "oneclickpostfactory",
            "learning_role": "baseline",
        }]
        budget._active_work = []
        with patch.object(learning_equivalence, "satisfied_baseline", return_value=True):
            self.assertEqual(budget._learning_stock(), 0)

    def test_feedback_wrapper_projects_without_rewriting_snapshot(self):
        row = {
            "campaign": HISTORICAL,
            "provider": "x",
            "account_id": ACCOUNT,
            "post_id": POST,
            "captured_at": (self.pub["at"] + timedelta(hours=24)).isoformat(),
            "availability": {"status": "available"},
            "metrics": {"impressions": 1000, "likes": 10, "reposts": 1, "quotes": 0},
            "editorial": None,
        }
        original = dict(row)
        with patch.object(learning_equivalence, "campaign_ids", return_value=[CURRENT]), \
             patch.object(learning_equivalence, "destination_binding", return_value={"account_id": ACCOUNT}), \
             patch("ocpf_post.performance_review.publications", return_value=self.publications), \
             patch.object(performance, "iter_snapshots", return_value=[row]), \
             patch.object(performance_feedback, "enabled", return_value=True):
            report = performance_feedback_equivalent.build(apply=False, now=NOW)
        self.assertEqual(len(report["observations"]), 1)
        editorial = report["observations"][0]["editorial"]
        self.assertEqual(editorial["revision"], "abcdef1234567890")
        self.assertEqual(editorial["comparison_variant"], "practical")
        self.assertEqual(report["effect_equivalence"]["projected_count"], 1)
        self.assertEqual(row, original)


if __name__ == "__main__":
    unittest.main()
