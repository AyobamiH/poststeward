from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import acceptance_views, local_store

NOW = datetime(2026, 9, 17, 22, 0, tzinfo=timezone.utc)
PAYLOAD = acceptance_views.INDEX4_PAYLOAD_SHA256


def publication(campaign, provider, account, post_id):
    receipt = {
        "campaign": campaign,
        "provider": provider,
        "account_id": account,
        "post_id": post_id,
        "status": "published_verified",
        "readback_verified": True,
        "text_sha256": PAYLOAD,
    }
    return {
        "receipt": receipt,
        "at": NOW - timedelta(hours=72),
        "effective_verified": True,
        "verification_basis": "receipt",
    }


class AcceptanceViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(
            os.environ,
            OCPF_POST_STATE_DIR=self.temp.name,
            OCPF_POST_CONFIG_DIR=self.temp.name + "/config",
        )
        env.start()
        self.addCleanup(env.stop)

    def test_index4_keeps_measurement_and_qualifying_feedback_distinct(self):
        targets = acceptance_views.INDEX4_TARGETS
        pubs = {
            (targets[0]["campaign"], targets[0]["provider"], targets[0]["account_id"], "t-post"):
                publication(targets[0]["campaign"], targets[0]["provider"], targets[0]["account_id"], "t-post"),
            (targets[1]["campaign"], targets[1]["provider"], targets[1]["account_id"], "x-post"):
                publication(targets[1]["campaign"], targets[1]["provider"], targets[1]["account_id"], "x-post"),
        }
        snapshots = [
            {
                "campaign": targets[0]["campaign"], "provider": "threads",
                "account_id": targets[0]["account_id"], "post_id": "t-post",
                "captured_at": (NOW - timedelta(minutes=1)).isoformat(),
                "target_age_hours": 72,
                "availability": {"status": "available"},
                "metrics": {"views": 35, "likes": 1, "reposts": 0, "quotes": 0},
                "editorial": {"text_sha256": PAYLOAD},
            },
            {
                "campaign": targets[1]["campaign"], "provider": "x",
                "account_id": targets[1]["account_id"], "post_id": "x-post",
                "captured_at": (NOW - timedelta(minutes=2)).isoformat(),
                "target_age_hours": 72,
                "availability": {"status": "available"},
                "metrics": {"impressions": 180, "likes": 2, "reposts": 0, "quotes": 0},
                "editorial": {"text_sha256": PAYLOAD},
            },
        ]
        feedback = {
            "schema_version": 1,
            "enabled": True,
            "observed_at": NOW.isoformat(),
            "observations": [{
                "campaign": targets[1]["campaign"], "provider": "x",
                "account_id": targets[1]["account_id"], "post_id": "x-post",
                "target_age_hours": 72, "captured_at": snapshots[1]["captured_at"],
                "exposure": 180, "editorial": {"text_sha256": PAYLOAD},
            }],
        }
        baseline_manifest = {
            "project": "oneclickpostfactory",
            "source": {
                "source_id": "oneclickpostfactory-README-671b1c0-4-insight",
                "source_sha": "671b1c0",
                "comparison_variant": "question",
            },
        }

        class Sampling:
            def reason(self, item, provider, account, project):
                return None if provider == "x" else "predecessor_comparable_measurement_required"

        with patch("ocpf_post.performance_review.publications", return_value=pubs), \
             patch("ocpf_post.performance.iter_snapshots", return_value=snapshots), \
             patch("ocpf_post.acceptance_views.local_store.read", return_value=feedback), \
             patch("ocpf_post.learning_supply.SamplingBudget", return_value=Sampling()), \
             patch("ocpf_post.campaigns.campaign_ids", return_value=[row["campaign"] for row in targets]), \
             patch("ocpf_post.campaigns.builtin_manifest", return_value=baseline_manifest):
            value = acceptance_views.index4_view(NOW)

        threads = next(row for row in value["targets"] if row["provider"] == "threads")
        x = next(row for row in value["targets"] if row["provider"] == "x")
        t72 = next(row for row in threads["measurements"] if row["target_age_hours"] == 72)
        x72 = next(row for row in x["measurements"] if row["target_age_hours"] == 72)
        self.assertEqual(t72["status"], "low_exposure")
        self.assertFalse(t72["persisted_feedback"]["qualifying_observation"])
        self.assertEqual(x72["status"], "qualifying_feedback_observation")
        self.assertTrue(x72["persisted_feedback"]["qualifying_observation"])
        x168 = next(row for row in x["measurements"] if row["target_age_hours"] == 168)
        self.assertEqual(x168["status"], "not_due_yet")
        self.assertEqual(threads["challenger_sampling_gate"]["status"], "deferred")
        self.assertEqual(threads["challenger_sampling_gate"]["acceptance_status"], "deferred")
        self.assertEqual(x["challenger_sampling_gate"]["status"], "eligible")
        self.assertEqual(x["challenger_sampling_gate"]["acceptance_status"], "eligible")
        self.assertEqual(value["status"], "open")

    def test_inbound_coverage_never_equates_zero_items_with_complete_scope(self):
        private_text = "PRIVATE INBOUND COPY"
        raw = {
            "schema_version": 1,
            "polls": {
                "threads": {
                    "status": "partial",
                    "observed_at": NOW.isoformat(),
                    "root_count": 8,
                    "roots_not_yet_scanned": 3,
                    "root_cursors": {"123": "opaque-private-cursor"},
                }
            },
            "inbox": {
                "secret": {
                    "provider": "threads",
                    "account_id": "t-account",
                    "status": "drafted",
                    "first_seen_at": (NOW - timedelta(hours=2)).isoformat(),
                    "conversation_depth": 2,
                    "context": {"text": private_text},
                    "draft": {"text": "PRIVATE DRAFT"},
                }
            },
        }
        report = {
            "schema_version": 1,
            "polls": raw["polls"],
            "counts": {"drafted": 1},
            "linkedin": {"status": "unsupported"},
        }
        with patch("ocpf_post.capacity_experiment.ACCOUNTS", {"threads": "t-account"}), \
             patch("ocpf_post.account_profiles.profiles", return_value={}), \
             patch("ocpf_post.registry.load_registry", return_value={"projects": {}}), \
             patch("ocpf_post.engagement.read", return_value=raw), \
             patch("ocpf_post.engagement.report", return_value=report):
            value = acceptance_views.inbound_coverage_view(NOW)
        scope = value["scopes"][0]
        encoded = json.dumps(value)
        self.assertEqual(scope["status"], "partial")
        self.assertEqual(scope["reason"], "roots_not_yet_scanned")
        self.assertEqual(scope["roots_not_yet_scanned"], 3)
        self.assertEqual(scope["open_root_cursors"], 1)
        self.assertEqual(scope["max_conversation_depth"], 2)
        self.assertEqual(scope["nested_waiting"], 1)
        self.assertNotIn(private_text, encoded)
        self.assertNotIn("PRIVATE DRAFT", encoded)
        self.assertNotIn("opaque-private-cursor", encoded)

    def test_linkedin_permission_required_is_explicit_in_pc06_scope(self):
        account_id = "urn:li:person:owner"
        raw = {
            "schema_version": 1,
            "polls": {
                "linkedin": {
                    "status": "permission_required",
                    "observed_at": NOW.isoformat(),
                    "required_scope": "r_member_social_feed",
                    "scope_recorded": True,
                    "automatic_retry": False,
                    "next_action": "provider_approval_required_before_reauthorise",
                }
            },
            "inbox": {},
        }
        with patch("ocpf_post.capacity_experiment.ACCOUNTS", {"linkedin": account_id}), \
             patch("ocpf_post.account_profiles.profiles", return_value={}), \
             patch("ocpf_post.registry.load_registry", return_value={"projects": {}}), \
             patch("ocpf_post.engagement.read", return_value=raw), \
             patch("ocpf_post.engagement.report", return_value={"polls": raw["polls"], "counts": {}}):
            value = acceptance_views.inbound_coverage_view(NOW)
        scope = value["scopes"][0]
        self.assertEqual(value["status"], "partial")
        self.assertEqual(scope["status"], "permission_required")
        self.assertEqual(scope["reason"], "r_member_social_feed")
        self.assertEqual(scope["required_scope"], "r_member_social_feed")
        self.assertTrue(scope["scope_recorded"])
        self.assertFalse(scope["automatic_retry"])
        self.assertEqual(scope["next_action"], "provider_approval_required_before_reauthorise")

    def test_pc03_and_pc05_project_linkedin_read_permission_gate(self):
        campaign_preview = {
            "status": "partial",
            "results": [{
                "schedule_id": "sch-li", "campaign": "C-LI", "provider": "linkedin",
                "account_id": "urn:li:person:owner", "post_id": "urn:li:share:1",
                "original_status": "published_unverified",
                "status": "readback_permission_required",
                "required_scope": "r_member_social", "scope_recorded": True,
                "automatic_retry": False,
                "next_action": "provider_approval_required_before_reauthorise",
            }],
        }
        with patch("ocpf_post.publication_reconcile.reconcile", return_value=campaign_preview), \
             patch("ocpf_post.reply_reconcile.reconcile", return_value={"status": "idle", "results": []}), \
             patch("ocpf_post.scheduler.schedule_records", return_value=[]):
            pc3 = acceptance_views.publication_readback_view(NOW)
            pc5 = acceptance_views.effect_reconciliation_view(NOW)
        self.assertEqual(pc3["results"][0]["required_scope"], "r_member_social")
        self.assertTrue(pc3["results"][0]["scope_recorded"])
        self.assertFalse(pc3["results"][0]["automatic_retry"])
        self.assertEqual(pc3["permission_gated_count"], 1)
        self.assertEqual(pc3["manual_review_count"], 0)
        self.assertEqual(pc3["automatic_work_remaining_count"], 0)
        self.assertEqual(pc5["campaign_effects"][0]["status"], "readback_permission_required")
        self.assertEqual(pc5["campaign_effects"][0]["required_scope"], "r_member_social")
        self.assertFalse(pc5["campaign_effects"][0]["automatic_retry"])
        self.assertEqual(pc5["campaign_permission_gated_count"], 1)
        self.assertEqual(pc5["campaign_manual_review_count"], 0)
        self.assertEqual(pc5["campaign_automatic_work_remaining_count"], 0)

    def test_pc01_editorial_supply_projects_durable_requests_without_copy(self):
        continuity = {
            "schema_version": 1, "observed_at": NOW.isoformat(),
            "routes": {
                "route-1": {
                    "request_id": "edr-route-1-1", "project": "post-once",
                    "provider": "x", "account_id": "123", "generation": 1,
                    "status": "open", "first_requested_at": NOW.isoformat(),
                    "last_observed_at": NOW.isoformat(), "runnable": 0, "reserved": 1,
                    "stock_floor": 3, "expiring_within_48h": 0,
                    "surviving_after_48h": 0, "suggested_new_items": 3,
                    "demand_reasons": ["no_unreserved_inventory"],
                    "private_copy": "MUST NOT PROJECT",
                },
                "route-2": {
                    "request_id": "edr-route-2-1", "project": "proof-and-state",
                    "provider": "threads", "account_id": "456", "status": "resolved",
                },
            },
        }
        handoff = {"status": "observed", "observed_at": NOW.isoformat(), "write_performed": True}
        def read(path):
            name = path.name
            return continuity if name == "editorial-continuity.json" else handoff if name == "editorial-handoff-status.json" else {}
        with patch.object(acceptance_views.local_store, "read", side_effect=read):
            value = acceptance_views.editorial_supply_view(NOW)
        encoded = json.dumps(value)
        self.assertEqual(value["acceptance_id"], "PC-01")
        self.assertEqual(value["status"], "open")
        self.assertEqual(value["open_request_count"], 1)
        self.assertEqual(value["resolved_request_count"], 1)
        self.assertNotIn("MUST NOT PROJECT", encoded)
        self.assertNotIn("private_copy", encoded)

    def test_pc02_source_vault_keeps_failed_latest_read_open(self):
        sources = {"projects": {
            "coding-agent-skills": {
                "status": "observed", "observed_at": NOW.isoformat(),
                "source_ok": True, "pending": [],
            }
        }}
        cycle = {"observed_at": NOW.isoformat(), "stages": [
            {"stage": "vault:coding-agent-skills-gtm", "status": "attention"}
        ]}
        policies = {"coding-agent-skills-gtm": {
            "enabled": True, "project": "coding-agent-skills",
        }}
        observations = {"coding-agent-skills-gtm": {
            "observed_at": (NOW - timedelta(hours=2)).isoformat(),
            "valid_until": (NOW - timedelta(minutes=5)).isoformat(),
            "active": {"a": "campaign"}, "skipped": [], "deferred": [],
        }}
        with patch("ocpf_post.source_observations.load", return_value=sources), \
             patch("ocpf_post.vault_sync.policies", return_value=policies), \
             patch("ocpf_post.vault_sync.observations", return_value=observations), \
             patch.object(acceptance_views.local_store, "read", return_value=cycle):
            value = acceptance_views.source_vault_view(NOW)
        self.assertEqual(value["acceptance_id"], "PC-02")
        self.assertEqual(value["status"], "open")
        self.assertEqual(value["open_vault_count"], 1)
        self.assertEqual(value["vaults"][0]["status"], "unavailable")

    def test_pc03_and_pc05_are_preview_only_and_keep_uncertain_history_open(self):
        campaign_preview = {
            "status": "partial",
            "results": [{
                "schedule_id": "sch-1", "campaign": "C-1", "provider": "linkedin",
                "account_id": "urn:li:person:owner", "post_id": "urn:li:share:1",
                "original_status": "ambiguous_effect", "status": "forensic_review_required",
                "previous_status": "forensic_no_match", "automatic_retry": False,
                "next_action": "targeted_manual_review_without_resend", "cached": True,
            }],
        }
        reply_preview = {
            "status": "partial",
            "results": [{
                "inbox_id": "reply-1", "provider": "threads", "account_id": "22",
                "post_id": "33", "original_status": "published_unverified",
                "status": "readback_deferred",
            }],
        }
        failed = [{
            "schedule_id": "sch-failed", "campaign": "C-OLD", "provider": "x",
            "account_id": "11", "status": "failed", "run_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(), "failure_class": "provider_rejected",
        }]
        with patch("ocpf_post.publication_reconcile.reconcile", return_value=campaign_preview) as campaign, \
             patch("ocpf_post.reply_reconcile.reconcile", return_value=reply_preview) as replies, \
             patch("ocpf_post.scheduler.schedule_records", return_value=failed):
            pc3 = acceptance_views.publication_readback_view(NOW)
            pc5 = acceptance_views.effect_reconciliation_view(NOW)
        self.assertEqual(pc3["acceptance_id"], "PC-03")
        self.assertEqual(pc3["status"], "open")
        self.assertEqual(pc3["unresolved_count"], 1)
        self.assertEqual(pc3["permission_gated_count"], 0)
        self.assertEqual(pc3["manual_review_count"], 1)
        self.assertEqual(pc3["automatic_work_remaining_count"], 0)
        self.assertEqual(pc3["results"][0]["status"], "forensic_review_required")
        self.assertFalse(pc3["results"][0]["automatic_retry"])
        self.assertEqual(pc3["results"][0]["next_action"], "targeted_manual_review_without_resend")
        self.assertEqual(pc5["acceptance_id"], "PC-05")
        self.assertEqual(pc5["status"], "open")
        self.assertEqual(pc5["campaign_unresolved_count"], 1)
        self.assertEqual(pc5["campaign_permission_gated_count"], 0)
        self.assertEqual(pc5["campaign_manual_review_count"], 1)
        self.assertEqual(pc5["campaign_automatic_work_remaining_count"], 0)
        self.assertEqual(pc5["campaign_effects"][0]["previous_status"], "forensic_no_match")
        self.assertFalse(pc5["campaign_effects"][0]["automatic_retry"])
        self.assertEqual(pc5["campaign_effects"][0]["next_action"], "targeted_manual_review_without_resend")
        self.assertEqual(pc5["reply_unresolved_count"], 1)
        self.assertEqual(pc5["historical_failed_schedule_count"], 1)
        self.assertTrue(all(call.kwargs.get("apply") is False for call in campaign.call_args_list))
        self.assertTrue(all(call.kwargs.get("apply") is False for call in replies.call_args_list))

    def test_build_has_all_six_pc_sections_and_keeps_open_distinct_from_unavailable(self):
        sections = {
            "pc01_editorial_supply": {"status": "open"},
            "pc02_source_vault": {"status": "observed"},
            "pc03_publication_readback": {"status": "open"},
            "pc04_learning": {"status": "open"},
            "pc05_effect_reconciliation": {"status": "open"},
            "pc06_inbound_coverage": {"status": "partial"},
        }
        with patch.object(acceptance_views, "editorial_supply_view", return_value=sections["pc01_editorial_supply"]), \
             patch.object(acceptance_views, "source_vault_view", return_value=sections["pc02_source_vault"]), \
             patch.object(acceptance_views, "publication_readback_view", return_value=sections["pc03_publication_readback"]), \
             patch.object(acceptance_views, "index4_view", return_value=sections["pc04_learning"]), \
             patch.object(acceptance_views, "effect_reconciliation_view", return_value=sections["pc05_effect_reconciliation"]), \
             patch.object(acceptance_views, "inbound_coverage_view", return_value=sections["pc06_inbound_coverage"]):
            value = acceptance_views.build(now=NOW)
        self.assertEqual(set(value["sections"]), set(sections))
        self.assertEqual(value["status"], "open")

    def test_persist_writes_only_compact_acceptance_state(self):
        value = {
            "schema_version": 1,
            "status": "open",
            "observed_at": NOW.isoformat(),
            "sections": {"index4": {"status": "open"}},
        }
        acceptance_views.persist(value)
        self.assertEqual(local_store.read(acceptance_views.path()), value)


if __name__ == "__main__":
    unittest.main()
