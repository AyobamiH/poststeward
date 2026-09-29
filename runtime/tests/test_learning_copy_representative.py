from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from ocpf_post import portfolio, portfolio_diversity, source_pipeline

NOW = datetime(2026, 9, 13, 20, tzinfo=timezone.utc)
COPY_KEY = ("x", "123", "same-sha")
OLD = "SAMPLE-AUTO-03I-OLD"
LEARN = "SAMPLE-AUTO-03I-NEW-X"


def manifest(campaign, *, priority, learning=False):
    source = {
        "type": "repository_product_truth",
        "source_id": "sample-README-newrev-3-insight" if learning else "sample-README-oldrev-3-insight",
        "source_sha": "newrev" if learning else "oldrev",
    }
    allocation = {
        "enabled": True,
        "lane": "evergreen",
        "priority": priority,
        "prepared_at": NOW.isoformat(),
        "expires_at": "2026-09-20T20:00:00+00:00",
    }
    result = {
        "campaign": campaign,
        "project": "sample",
        "title": campaign,
        "status": "COPY-READY",
        "providers": ["x"],
        "payload_sha256": {"x": "same-sha"},
        "allocation": allocation,
        "source": source,
    }
    if learning:
        result["payload_frozen"] = True
        source["comparison_variant"] = "practical"
        allocation["learning_base_priority"] = 70
        allocation["learning_dedupe_priority_bump"] = 1
    return result


class LearningCopyRepresentativeTests(unittest.TestCase):
    def setUp(self):
        self.manifests = {
            OLD: manifest(OLD, priority=70),
            LEARN: manifest(LEARN, priority=71, learning=True),
        }

    def candidates(self, *, occupied=None):
        exclusions = []
        with patch.object(portfolio, "_campaign_ids", return_value=[OLD, LEARN]), \
             patch.object(portfolio, "builtin_manifest", side_effect=lambda cid: self.manifests[cid]), \
             patch.object(portfolio, "builtin_text", return_value="same text"), \
             patch.object(portfolio, "_eligible_manifest", return_value=(True, "eligible")), \
             patch.object(portfolio, "schedule_records", return_value=[]), \
             patch.object(portfolio, "iter_receipts", return_value=[]), \
             patch("ocpf_post.vault_sync.guard", return_value=None), \
             patch("ocpf_post.account_profiles.unavailable", return_value=None), \
             patch("ocpf_post.copy_guard.campaign_copy_key", return_value=COPY_KEY), \
             patch("ocpf_post.copy_guard.occupied_copies", return_value=occupied or {}):
            rows = portfolio.delivery_candidates(now=NOW, exclusions=exclusions)
        return rows, exclusions

    def test_learning_id_represents_one_unscheduled_identical_copy(self):
        rows, exclusions = self.candidates()
        self.assertEqual([row["campaign"] for row in rows], [LEARN])
        old = next(row for row in exclusions if row["campaign"] == OLD)
        self.assertEqual(old["reason"], "identical copy represented by another eligible campaign")
        self.assertEqual(old["matching"]["campaign"], LEARN)

    def test_active_or_terminal_copy_still_blocks_learning_representative(self):
        rows, exclusions = self.candidates(occupied={COPY_KEY: {
            "campaign": "ALREADY-EFFECTIVE",
            "provider": "x",
            "account_id": "123",
            "status": "published_verified",
        }})
        self.assertEqual(rows, [])
        reasons = {row["campaign"]: row["reason"] for row in exclusions}
        self.assertEqual(reasons[LEARN], "identical copy already reserved or recorded")
        self.assertEqual(reasons[OLD], "identical copy already reserved or recorded")

    def test_allocator_restores_base_priority_after_dedupe(self):
        candidate = {
            "campaign": LEARN,
            "project": "sample",
            "provider": "x",
            "lane": "evergreen",
            "priority": 71,
        }
        with patch.object(portfolio_diversity, "builtin_manifest", return_value=self.manifests[LEARN]):
            row = portfolio_diversity._enrich(candidate)
        self.assertEqual(row["priority"], 70)
        self.assertEqual(candidate["priority"], 71)

    def test_untrusted_priority_metadata_does_not_change_allocator_score(self):
        bad = manifest(LEARN, priority=71, learning=True)
        bad["allocation"]["learning_dedupe_priority_bump"] = 2
        candidate = {
            "campaign": LEARN,
            "project": "sample",
            "provider": "x",
            "lane": "evergreen",
            "priority": 71,
        }
        with patch.object(portfolio_diversity, "builtin_manifest", return_value=bad):
            row = portfolio_diversity._enrich(candidate)
        self.assertEqual(row["priority"], 71)

    def test_source_pipeline_emits_dedupe_only_priority_and_reader_restores_it(self):
        profile = {
            "project": "sample",
            "providers": ["x"],
            "destinations": {"x": "x"},
            "inventory": [{"hook": "three", "body": "body", "priority": 70, "ttl_hours": 336}],
        }
        observation = {
            "readme_sha": "b" * 40,
            "readme_observed_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "profile_sha256": "fp",
            "status": "observed",
            "source_ok": True,
            "pending": [],
        }
        baseline = {
            "id": "SAMPLE-AUTO-03I-BBBBBBB",
            "source_id": "sample-README-bbbbbbbbbbbb-3-insight",
            "sha": "b" * 40,
            "title": "three [insight]",
            "lane": "evergreen",
            "priority": 70,
            "ttl": 336,
            "at": NOW.isoformat(),
            "type": "repository_product_truth",
            "item": profile["inventory"][0],
            "variant": "insight",
            "comparison_variant": "practical",
        }
        written = {}

        class Budget:
            def __init__(self, now, apply):
                pass
            def admit(self, project, provider, account, **kwargs):
                return {"admitted": True, "project": project, "reasons": ["protected_learning_inventory"]}

        class Sampling:
            active = True
            def __init__(self, now):
                pass
            def reason(self, *args):
                return None
            def record(self, *args):
                pass

        def make_manifest(**kwargs):
            return {
                "campaign": kwargs["campaign"],
                "project": "sample",
                "title": kwargs["title"],
                "status": "COPY-READY",
                "providers": ["x"],
                "payload_sha256": {"x": "same-sha"},
                "allocation": {
                    "enabled": True,
                    "lane": kwargs["lane"],
                    "priority": kwargs["priority"],
                    "prepared_at": NOW.isoformat(),
                },
                "source": {
                    "type": kwargs["source_type"],
                    "source_id": kwargs["source_id"],
                    "source_sha": kwargs["source_sha"],
                },
            }

        with patch("ocpf_post.portfolio_source_loader.merged_source_profiles", return_value={"projects": {"sample": profile}}), \
             patch.object(source_pipeline.observations, "load", return_value={"schema_version": 1, "projects": {"sample": observation}}), \
             patch.object(source_pipeline.observations, "fingerprint", return_value="fp"), \
             patch("ocpf_post.runtime_sources.source_lock", side_effect=lambda **_kwargs: nullcontext()), \
             patch("ocpf_post.runtime_sources.effective_texts", side_effect=lambda profile, texts: texts), \
             patch("ocpf_post.registry.resolve_account", return_value={"account_id": "123"}), \
             patch.object(source_pipeline, "_known_deliveries", return_value=set()), \
             patch.object(source_pipeline, "_items", return_value=iter([baseline])), \
             patch.object(source_pipeline, "LearningBudget", Budget), \
             patch("ocpf_post.learning_supply.SamplingBudget", Sampling), \
             patch.object(source_pipeline.r, "_render_static", return_value="same text"), \
             patch.object(source_pipeline.r, "_manifest", side_effect=make_manifest), \
             patch.object(source_pipeline.r, "_write_runtime_campaign", side_effect=lambda cid, m, texts: written.update(manifest=m)), \
             patch.object(source_pipeline.local_store, "write", return_value=None):
            result = source_pipeline.refresh(apply=True, project="sample", now=NOW)

        self.assertEqual(result["static_campaigns"][0]["learning_role"], "baseline")
        produced = written["manifest"]
        self.assertEqual(produced["allocation"]["priority"], 71)
        self.assertEqual(produced["allocation"]["learning_base_priority"], 70)
        self.assertEqual(produced["allocation"]["learning_dedupe_priority_bump"], 1)
        candidate = {
            "campaign": produced["campaign"],
            "project": "sample",
            "provider": "x",
            "lane": "evergreen",
            "priority": 71,
        }
        with patch.object(portfolio_diversity, "builtin_manifest", return_value=produced):
            self.assertEqual(portfolio_diversity._enrich(candidate)["priority"], 70)


if __name__ == "__main__":
    unittest.main()
