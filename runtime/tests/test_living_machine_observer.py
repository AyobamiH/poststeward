from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ocpf_post import observer


class LivingMachineProjectionTests(unittest.TestCase):
    def test_engagement_projection_keeps_lifecycle_but_redacts_copy(self):
        raw = {
            "schema_version": 1,
            "counts": {"pending": 1},
            "waiting_over_24h": 0,
            "polls": {},
            "nested_pending": 1,
            "items": [{
                "id": "inbox-1",
                "provider": "threads",
                "account_id": "acct-1",
                "campaign": "TEST-1",
                "status": "drafted",
                "first_seen_at": "2026-09-17T12:00:00Z",
                "last_seen_at": "2026-09-17T12:01:00Z",
                "conversation_depth": 2,
                "context": {"text": "untrusted inbound copy", "author": "someone"},
                "draft": {"text": "private reviewed reply"},
                "dismissal_reason": "private operator prose",
            }],
        }
        with patch("ocpf_post.engagement.report", return_value=raw):
            value = observer._engagement()
        encoded = json.dumps(value)
        self.assertEqual(value["recent_items"][0]["id"], "inbox-1")
        self.assertEqual(value["recent_items"][0]["conversation_depth"], 2)
        self.assertFalse(value["copy_exposed"])
        self.assertNotIn("untrusted inbound copy", encoded)
        self.assertNotIn("private reviewed reply", encoded)
        self.assertNotIn("private operator prose", encoded)
        self.assertNotIn("context", value["recent_items"][0])
        self.assertNotIn("draft", value["recent_items"][0])

    def test_feedback_projection_reads_persisted_state_without_recomputation(self):
        persisted = {
            "status": "signals_available",
            "observed_at": "2026-09-17T12:30:00Z",
            "enabled": True,
            "selection_adjustment_available": True,
            "signals": {
                "scope-secretish-digest": {
                    "provider": "x", "account_id": "123", "lane": "evergreen",
                    "variant": "practical", "target_age_hours": 24,
                    "supporting_target_ages": [24, 72], "boost": 3,
                    "expires_at": "2026-09-18T12:30:00Z",
                    "evidence_post_ids": ["1", "2", "3"],
                }
            },
            "cohorts": [{"status": "bounded_preference_available"}],
            "observations": [{}, {}],
            "age_conflicts": [],
        }
        with patch("ocpf_post.local_store.read", return_value=persisted), \
             patch("ocpf_post.performance_feedback.build", side_effect=AssertionError("recompute forbidden")):
            value = observer._feedback()
        self.assertEqual(value["signal_count"], 1)
        self.assertEqual(value["cohort_count"], 1)
        self.assertEqual(value["observation_count"], 2)
        self.assertTrue(value["selection_adjustment_available"])
        self.assertFalse(value["recomputed"])
        self.assertEqual(value["signals"][0]["evidence_count"], 3)
        self.assertNotIn("evidence_post_ids", value["signals"][0])

    def test_machine_flows_are_emitted_only_from_supplied_evidence(self):
        payload = {
            "portfolio": {"plan": {"plan": [{"campaign": "TEST-1"}]}},
            "replenishment": {"observed_repositories": 2, "runtime_campaign_count": 4},
            "activity": {
                "active_schedules": [{"schedule_id": "sch-1", "status": "executing"}],
                "recent_receipts": [{
                    "campaign": "TEST-1", "provider": "x", "schedule_id": "sch-1",
                    "post_id": "99", "status": "published_verified",
                    "recorded_at": "2026-09-17T12:00:04Z", "readback_verified": True,
                }],
                "activity": [
                    {"kind": "schedule", "event": "scheduled", "status": "scheduled", "schedule_id": "sch-1", "campaign": "TEST-1", "provider": "x", "at": "2026-09-17T12:00:00Z"},
                    {"kind": "schedule", "event": "claimed", "status": "executing", "schedule_id": "sch-1", "campaign": "TEST-1", "provider": "x", "at": "2026-09-17T12:00:01Z"},
                    {"kind": "schedule", "event": "completed", "status": "published_verified", "schedule_id": "sch-1", "campaign": "TEST-1", "provider": "x", "post_id": "99", "at": "2026-09-17T12:00:03Z"},
                    {"kind": "receipt", "status": "published_verified", "schedule_id": "sch-1", "campaign": "TEST-1", "provider": "x", "post_id": "99", "at": "2026-09-17T12:00:04Z"},
                ],
            },
            "engagement": {
                "counts": {"pending": 1, "sending": 0},
                "recent_items": [{
                    "id": "reply-1", "provider": "x", "campaign": "TEST-1", "status": "published_verified",
                    "first_seen_at": "2026-09-17T12:02:00Z", "attempted_at": "2026-09-17T12:03:00Z",
                    "published_at": "2026-09-17T12:03:02Z", "post_id": "100", "readback_verified": True,
                }],
            },
            "performance": {
                "snapshot_count": 1,
                "recent": [{
                    "campaign": "TEST-1", "provider": "x", "post_id": "99",
                    "captured_at": "2026-09-17T13:00:00Z", "availability": {"status": "available"},
                }],
            },
            "feedback": {
                "status": "signals_available", "observed_at": "2026-09-17T13:01:00Z",
                "signal_count": 1, "selection_adjustment_available": True,
            },
        }
        value = observer._machine_projection(payload)
        routes = {(row["source"], row["target"], row["kind"]) for row in value["flows"]}
        expected = {
            ("campaigns", "scheduler", "schedule_persisted"),
            ("scheduler", "run_due", "consequence_claimed"),
            ("run_due", "x", "publication_completed"),
            ("x", "readback", "publication_receipt"),
            ("x", "engagement", "reply_observed"),
            ("engagement", "reply_worker", "reply_send_claimed"),
            ("reply_worker", "x", "reply_published"),
            ("x", "readback", "reply_readback"),
            ("readback", "performance", "metrics_stored"),
            ("performance", "learning", "feedback_evaluated"),
            ("learning", "planner", "feedback_available"),
        }
        self.assertTrue(expected.issubset(routes))
        self.assertTrue(value["evidence_only"])
        self.assertEqual(value["animation_authority"], "browser-only")
        self.assertFalse(value["runtime_paused_by_browser"])
        self.assertFalse(any(row["source"] == "sources" for row in value["flows"]))

    def test_empty_snapshot_has_topology_without_manufactured_motion(self):
        value = observer._machine_projection({})
        self.assertGreater(len(value["nodes"]), 8)
        self.assertGreater(len(value["edges"]), 8)
        self.assertEqual(value["flows"], [])


class LivingMachineUiTests(unittest.TestCase):
    def test_ui_keeps_controls_visual_only_and_sse_one_way(self):
        body = (Path(__file__).resolve().parents[1] / "src" / "ocpf_post" / "ui" / "index.html").read_text(encoding="utf-8")
        lower = body.lower()
        self.assertIn('id="living-machine"', body)
        self.assertIn("Pause visual", body)
        self.assertIn("Replay recent", body)
        self.assertIn("evidence trace", lower)
        self.assertIn("new EventSource('/api/stream')", body)
        self.assertNotIn("<form", lower)
        self.assertNotIn('method="post"', lower)
        self.assertNotIn("/api/publish", body)
        self.assertNotIn("/api/retry", body)


if __name__ == "__main__":
    unittest.main()
