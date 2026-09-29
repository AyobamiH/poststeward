from __future__ import annotations

import unittest
from unittest.mock import patch

from ocpf_post.scheduler_cli import _inspection_payload, _recovery_action


class SchedulerInspectionTests(unittest.TestCase):
    def test_inspection_redacts_scheduled_copy_and_preserves_failure_detail(self) -> None:
        record = {
            "schedule_id": "sch_test",
            "status": "failed",
            "campaign": "GBF-AUTO-01I-79EB3E2",
            "provider": "threads",
            "account_id": "25914281681582868",
            "account_label": "threads-founder",
            "run_at": "2026-09-11T13:39:26Z",
            "display_timezone": "Europe/London",
            "text": "private scheduled copy must not appear in inspection JSON",
            "text_sha256": "a" * 64,
            "detail": "Provider rejected the consequence: example safe provider error.",
            "failure_class": "provider_forbidden",
            "failure_stage": "provider_consequence",
            "provider_http_status": 403,
            "automatic_retry": False,
        }
        events = [
            {
                "event": "scheduled",
                "status": "scheduled",
                "schedule_id": "sch_test",
                "recorded_at": "2026-09-11T12:00:00Z",
                "text": "private scheduled copy must not appear in event history",
            },
            {
                "event": "failed",
                "status": "failed",
                "schedule_id": "sch_test",
                "recorded_at": "2026-09-11T13:39:26Z",
                "detail": "Provider rejected the consequence: example safe provider error.",
                "failure_class": "provider_forbidden",
                "failure_stage": "provider_consequence",
                "provider_http_status": 403,
                "automatic_retry": False,
            },
        ]

        with patch("ocpf_post.scheduler_cli.iter_schedule_events", return_value=events):
            payload = _inspection_payload(record)

        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["provider_consequence_attempted"])
        self.assertFalse(payload["inspection_consequence_attempted"])
        self.assertTrue(payload["historical_provider_consequence_attempted"])
        self.assertNotIn("text", payload["schedule"])
        self.assertTrue(all("text" not in event for event in payload["events"]))
        self.assertEqual(payload["schedule"]["detail"], record["detail"])
        self.assertEqual(payload["schedule"]["failure_class"], "provider_forbidden")
        self.assertEqual(payload["schedule"]["failure_stage"], "provider_consequence")
        self.assertEqual(payload["schedule"]["provider_http_status"], 403)
        self.assertFalse(payload["schedule"]["automatic_retry"])
        self.assertEqual(payload["events"][1]["failure_class"], "provider_forbidden")
        self.assertIn("Do not rerun", payload["recovery_action"])

    def test_deferred_preflight_is_visible_without_exposing_copy(self) -> None:
        record = {
            "schedule_id": "sch_deferred",
            "status": "scheduled",
            "campaign": "DONESTATE-AUTO-03Q-9B792BA",
            "provider": "x",
            "account_id": "1480506376447315969",
            "run_at": "2026-09-11T13:39:36Z",
            "text": "private copy",
            "text_sha256": "b" * 64,
            "retry_at": "2026-09-11T13:41:00Z",
            "preflight_attempts": 2,
            "failure_class": "provider_unavailable",
            "detail": "Provider preflight was unavailable before consequence; no publish request was attempted.",
        }
        events = [{
            "event": "preflight_deferred",
            "status": "scheduled",
            "schedule_id": "sch_deferred",
            "recorded_at": "2026-09-11T13:39:00Z",
            "retry_at": "2026-09-11T13:41:00Z",
            "preflight_attempts": 2,
            "failure_class": "provider_unavailable",
            "detail": record["detail"],
            "text": "must stay redacted",
        }]
        with patch("ocpf_post.scheduler_cli.iter_schedule_events", return_value=events):
            payload = _inspection_payload(record)
        self.assertNotIn("text", payload["schedule"])
        self.assertNotIn("text", payload["events"][0])
        self.assertEqual(payload["schedule"]["retry_at"], record["retry_at"])
        self.assertEqual(payload["schedule"]["preflight_attempts"], 2)
        self.assertEqual(payload["events"][0]["failure_class"], "provider_unavailable")
        self.assertIn("before any publish request began", payload["recovery_action"])
        self.assertIn(record["retry_at"], payload["recovery_action"])
        self.assertIn("do not create a duplicate replacement", payload["recovery_action"])

    def test_ambiguous_effect_guidance_blocks_retry(self) -> None:
        action = _recovery_action({"status": "ambiguous_effect"})
        self.assertIn("Do not retry", action)
        self.assertIn("provider/readback evidence", action)

    def test_published_unverified_guidance_does_not_republish(self) -> None:
        action = _recovery_action({"status": "published_unverified"})
        self.assertIn("exact provider text was not proven", action)
        self.assertIn("do not republish", action)

    def test_published_unverified_inspection_exposes_frozen_payload_boundary(self) -> None:
        text = "First paragraph with the intended provider payload.\n\nSecond paragraph keeps the exact body reviewable."
        import hashlib
        record = {
            "schedule_id": "sch_linkedin",
            "status": "published_unverified",
            "campaign": "OCPF-GEN-TEST",
            "provider": "linkedin",
            "account_id": "urn:li:person:test",
            "run_at": "2026-09-24T12:08:34Z",
            "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "post_id": "urn:li:share:123",
            "readback_verified": False,
        }
        events = [
            {"event": "scheduled", "status": "scheduled", "schedule_id": "sch_linkedin"},
            {"event": "claimed", "status": "executing", "schedule_id": "sch_linkedin"},
            {"event": "completed", "status": "published_unverified", "schedule_id": "sch_linkedin"},
        ]
        with patch("ocpf_post.scheduler_cli.iter_schedule_events", return_value=events), \
             patch("ocpf_post.providers.linkedin.recorded_read_permission", return_value={
                 "status": "missing", "purpose": "posts", "required_scope": "r_member_social",
             }):
            payload = _inspection_payload(record)
        integrity = payload["payload_integrity"]
        self.assertTrue(payload["historical_provider_consequence_attempted"])
        self.assertEqual(integrity["expected_text_characters"], len(text))
        self.assertEqual(integrity["expected_paragraphs"], 2)
        self.assertTrue(integrity["local_payload_hash_matches"])
        self.assertFalse(integrity["provider_text_match_proven"])
        self.assertEqual(integrity["verification_state"], "durable_provider_id_only")
        self.assertEqual(integrity["read_authority"]["required_scope"], "r_member_social")


if __name__ == "__main__":
    unittest.main()
