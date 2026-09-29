from __future__ import annotations

import unittest

from ocpf_post.portfolio_source_loader import merged_source_profiles
from ocpf_post.registry import project_summary


OCPF_README_SHA = "671b1c07c82058c8fb9293d1297d657adce04d1e"


class ExpandedPortfolioSourceTests(unittest.TestCase):
    def test_new_source_repositories_are_loaded(self) -> None:
        projects = merged_source_profiles()["projects"]
        expected = {
            "opstruth-chatgpt-plugin",
            "evidence-explorer",
            "oneclick-chatgpt-plugin",
            "lovable-architecture-auditor",
            "relay-live-business-engagement",
            "coding-agent-skills",
            "public-decision-intelligence",
        }
        self.assertTrue(expected.issubset(projects))

    def test_supporting_surfaces_keep_clear_product_labels(self) -> None:
        projects = merged_source_profiles()["projects"]
        self.assertIn("OpsTruth", projects["opstruth-chatgpt-plugin"]["label"])
        self.assertIn("Public Decision Intelligence", projects["evidence-explorer"]["label"])
        self.assertEqual(projects["oneclick-chatgpt-plugin"]["label"], "One Click for ChatGPT")

    def test_ocpf_extension_adds_only_revision_bound_source_backed_topics(self) -> None:
        profile = merged_source_profiles()["projects"]["oneclickpostfactory"]
        inventory = profile["inventory"]
        self.assertEqual(len(inventory), 8)
        added = inventory[3:]
        self.assertEqual(len(added), 5)
        self.assertTrue(all(row["source_sha"] == OCPF_README_SHA for row in added))
        self.assertEqual(
            {row["title"] for row in added},
            {
                "Planning and publishing should stay separate",
                "One repository should own the product truth",
                "Validation is not deployment proof",
                "Production deployment should be explicit",
                "Live traffic needs separate evidence",
            },
        )
        self.assertEqual(len({row["hook"] for row in inventory}), len(inventory))

    def test_registry_authority_exists_for_every_added_source(self) -> None:
        projects = merged_source_profiles()["projects"]
        for project_id in (
            "opstruth-chatgpt-plugin",
            "evidence-explorer",
            "oneclick-chatgpt-plugin",
            "lovable-architecture-auditor",
            "relay-live-business-engagement",
            "coding-agent-skills",
            "public-decision-intelligence",
        ):
            summary = project_summary(project_id)
            self.assertTrue(summary["accounts"], project_id)
            self.assertEqual(set(summary["default_accounts"]), set(projects[project_id]["providers"]))

    def test_opstruth_plugin_does_not_widen_existing_ops_truth_provider_scope(self) -> None:
        projects = merged_source_profiles()["projects"]
        self.assertEqual(projects["opstruth-chatgpt-plugin"]["providers"], ["x"])
        self.assertEqual(set(project_summary("opstruth-chatgpt-plugin")["default_accounts"]), {"x"})


if __name__ == "__main__":
    unittest.main()
