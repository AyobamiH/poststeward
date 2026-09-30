from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from ocpf_post import replenisher
from ocpf_post.portfolio_source_loader import merged_source_profiles
from ocpf_post.source_pipeline import _items

UTC = timezone.utc
NOW = datetime(2026, 9, 13, 21, tzinfo=UTC)
OCPF_SHA = "671b1c07c82058c8fb9293d1297d657adce04d1e"


class RevisionBoundLearningInventoryTests(unittest.TestCase):
    def profile(self):
        base = merged_source_profiles()["projects"]["oneclickpostfactory"]
        return {**base, "project": "oneclickpostfactory"}

    def observation(self, sha):
        return {
            "readme_sha": sha,
            "readme_observed_at": NOW.isoformat(),
            "pending": [],
        }

    def test_current_reviewed_revision_exposes_new_topics_with_stable_arms(self):
        rows = list(_items(self.profile(), self.observation(OCPF_SHA)))
        ids = {row["source_id"] for row in rows}

        for index in range(1, 9):
            self.assertIn(
                f"oneclickpostfactory-README-{OCPF_SHA[:12]}-{index}-insight",
                ids,
            )

        # Deterministic arm assignments for the newly reviewed topics.
        self.assertIn(f"oneclickpostfactory-README-{OCPF_SHA[:12]}-4-question", ids)
        self.assertNotIn(f"oneclickpostfactory-README-{OCPF_SHA[:12]}-5-question", ids)
        self.assertNotIn(f"oneclickpostfactory-README-{OCPF_SHA[:12]}-5-practical", ids)
        self.assertIn(f"oneclickpostfactory-README-{OCPF_SHA[:12]}-6-practical", ids)
        self.assertIn(f"oneclickpostfactory-README-{OCPF_SHA[:12]}-7-practical", ids)
        self.assertIn(f"oneclickpostfactory-README-{OCPF_SHA[:12]}-8-question", ids)

    def test_reviewed_extension_copy_fits_every_deterministic_x_template(self):
        profile = self.profile()
        added = profile["inventory"][3:]
        self.assertEqual(len(added), 5)
        for item in added:
            for variant in ("insight", "question", "practical"):
                with self.subTest(title=item["title"], variant=variant):
                    rendered = replenisher._render_static(profile, item, "x", variant)
                    self.assertLessEqual(len(rendered), 280)

    def test_legacy_generator_obeys_same_revision_pin(self):
        profile = self.profile()
        with patch.object(replenisher, "campaign_ids", return_value=[]):
            current = replenisher._static_campaigns_for_profile(
                profile,
                source_sha=OCPF_SHA,
                now=NOW,
                apply=False,
            )
            future = replenisher._static_campaigns_for_profile(
                profile,
                source_sha="b" * 40,
                now=NOW,
                apply=False,
            )

        current_ids = {row["campaign"] for row in current}
        future_ids = {row["campaign"] for row in future}
        self.assertTrue(any(campaign.startswith("OCPF-AUTO-04") for campaign in current_ids))
        self.assertTrue(any(campaign.startswith("OCPF-AUTO-08") for campaign in current_ids))
        self.assertFalse(any(campaign.startswith("OCPF-AUTO-04") for campaign in future_ids))
        self.assertFalse(any(campaign.startswith("OCPF-AUTO-08") for campaign in future_ids))
        self.assertTrue(any(campaign.startswith("OCPF-AUTO-01") for campaign in future_ids))
        self.assertTrue(any(campaign.startswith("OCPF-AUTO-03") for campaign in future_ids))

    def test_future_readme_revision_does_not_silently_reuse_reviewed_extension_copy(self):
        future_sha = "b" * 40
        rows = list(_items(self.profile(), self.observation(future_sha)))
        ids = {row["source_id"] for row in rows}

        # Original unpinned inventory remains available under the new source revision.
        for index in range(1, 4):
            self.assertIn(
                f"oneclickpostfactory-README-{future_sha[:12]}-{index}-insight",
                ids,
            )

        # The reviewed extension is fail-closed until editorial revalidation.
        for index in range(4, 9):
            self.assertFalse(any(
                source_id.startswith(
                    f"oneclickpostfactory-README-{future_sha[:12]}-{index}-"
                )
                for source_id in ids
            ))


if __name__ == "__main__":
    unittest.main()
