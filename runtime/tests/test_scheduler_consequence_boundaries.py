from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from ocpf_post.model import AccountIdentity
from ocpf_post.providers.base import ProviderRejected
from ocpf_post.scheduler import (
    create_schedule,
    execute_schedule,
    get_schedule,
    provider_failure_metadata,
    provider_write_circuit,
    run_due,
    schedule_records,
)
from ocpf_post.state import iter_receipts

UTC = timezone.utc


class BoundaryProvider:
    def __init__(self) -> None:
        self.identity = AccountIdentity(provider="x", account_id="1480506376447315969", username="JohnWOE15")
        self.account_error = None
        self.publish_error = None
        self.verify_error = None
        self.publish_calls = 0

    def account(self):
        if self.account_error:
            raise self.account_error
        return self.identity

    def publish(self, text: str):
        self.publish_calls += 1
        if self.publish_error:
            raise self.publish_error
        return {"id": "provider-post-1"}

    def verify_post(self, post_id: str, expected_text: str) -> bool:
        if self.verify_error:
            raise self.verify_error
        return True

    def post_url(self, account, post_id: str) -> str:
        return f"https://example.invalid/{post_id}"


class SchedulerConsequenceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_state = os.environ.get("OCPF_POST_STATE_DIR")
        os.environ["OCPF_POST_STATE_DIR"] = self.tmp.name
        self.created_at = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)
        self.due_at = datetime(2026, 9, 12, 10, 1, tzinfo=UTC)

    def tearDown(self) -> None:
        if self.old_state is None:
            os.environ.pop("OCPF_POST_STATE_DIR", None)
        else:
            os.environ["OCPF_POST_STATE_DIR"] = self.old_state
        self.tmp.cleanup()

    def _schedule(self, provider: BoundaryProvider, campaign: str = "OCPF-002"):
        return create_schedule(
            campaign=campaign,
            provider="x",
            at="2026-09-12T10:00:00+00:00",
            provider_factory=lambda _name: provider,
            now=self.created_at,
        )

    def test_lost_or_unknown_publish_response_becomes_ambiguous_and_never_retries(self) -> None:
        provider = BoundaryProvider()
        schedule = self._schedule(provider)
        provider.publish_error = RuntimeError("transport vanished after request began")
        result = execute_schedule(schedule["schedule_id"], provider_factory=lambda _name: provider, now=self.due_at)
        self.assertEqual(result["status"], "ambiguous_effect")
        self.assertEqual(provider.publish_calls, 1)
        receipts = list(iter_receipts())
        self.assertEqual(receipts[-1]["status"], "ambiguous_effect")
        self.assertNotIn("transport vanished", receipts[-1].get("detail", ""))

        again = run_due(now=datetime(2026, 9, 12, 10, 5, tzinfo=UTC), provider_factory=lambda _name: provider)
        self.assertEqual(again, [])
        self.assertEqual(provider.publish_calls, 1)

    def test_unexpected_readback_exception_is_published_unverified_not_retryable(self) -> None:
        provider = BoundaryProvider()
        schedule = self._schedule(provider)
        # A programming/runtime exception after a durable provider id is still a
        # readback failure, never permission to resend the already-created post.
        provider.verify_error = RuntimeError("unexpected readback parser failure")
        result = execute_schedule(schedule["schedule_id"], provider_factory=lambda _name: provider, now=self.due_at)
        self.assertEqual(result["status"], "published_unverified")
        self.assertEqual(result["post_id"], "provider-post-1")
        self.assertEqual(provider.publish_calls, 1)
        receipts = list(iter_receipts())
        self.assertEqual(receipts[-1]["status"], "published_unverified")
        self.assertEqual(receipts[-1]["post_id"], "provider-post-1")

        again = run_due(now=datetime(2026, 9, 12, 10, 5, tzinfo=UTC), provider_factory=lambda _name: provider)
        self.assertEqual(again, [])
        self.assertEqual(provider.publish_calls, 1)

    def test_pre_consequence_rejection_remains_historical_and_never_replays(self) -> None:
        provider = BoundaryProvider()
        schedule = self._schedule(provider)
        provider.account_error = ProviderRejected(401, "invalid token")
        failed = execute_schedule(schedule["schedule_id"], provider_factory=lambda _name: provider, now=self.due_at)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failure_class"], "provider_auth_rejected")
        self.assertEqual(failed["failure_stage"], "identity_preflight")
        self.assertEqual(failed["provider_http_status"], 401)
        self.assertFalse(failed["automatic_retry"])
        self.assertEqual(provider.publish_calls, 0)
        self.assertEqual(list(iter_receipts()), [])

        provider.account_error = None
        later = run_due(now=datetime(2026, 9, 12, 11, 0, tzinfo=UTC), provider_factory=lambda _name: provider)
        self.assertEqual(later, [])
        self.assertEqual(provider.publish_calls, 0)
        self.assertEqual(get_schedule(schedule["schedule_id"])["status"], "failed")

    def test_definite_post_rejection_is_classified_without_retry_authority(self) -> None:
        provider = BoundaryProvider()
        schedule = self._schedule(provider)
        provider.publish_error = ProviderRejected(429, '{"title":"Too Many Requests"}')

        failed = execute_schedule(
            schedule["schedule_id"],
            provider_factory=lambda _name: provider,
            now=self.due_at,
        )

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failure_class"], "provider_rate_limited")
        self.assertEqual(failed["failure_stage"], "provider_consequence")
        self.assertEqual(failed["provider_http_status"], 429)
        self.assertFalse(failed["automatic_retry"])
        self.assertEqual(provider.publish_calls, 1)
        self.assertEqual(list(iter_receipts()), [])

        later = run_due(
            now=datetime(2026, 9, 12, 11, 0, tzinfo=UTC),
            provider_factory=lambda _name: provider,
        )
        self.assertEqual(later, [])
        self.assertEqual(provider.publish_calls, 1)

    def test_historical_safe_detail_can_be_classified_without_rewriting_ledger(self) -> None:
        value = provider_failure_metadata({
            "status": "failed",
            "detail": (
                'Provider rejected the consequence: provider rejected request '
                '(HTTP 402): {"title":"UsageCapExceeded"}. This schedule will not auto-retry.'
            ),
        })
        self.assertEqual(value["failure_class"], "provider_usage_limit")
        self.assertEqual(value["failure_stage"], "provider_consequence")
        self.assertEqual(value["provider_http_status"], 402)
        self.assertFalse(value["automatic_retry"])
        self.assertEqual(value["classification_basis"], "historical_safe_detail")

    def test_semantic_x_problem_codes_distinguish_forbidden_failures(self) -> None:
        automated = provider_failure_metadata(
            ProviderRejected(403, '{"errors":[{"code":226,"message":"automated"}]}')
        )
        duplicate = provider_failure_metadata(
            ProviderRejected(403, '{"errors":[{"code":187,"message":"duplicate"}]}')
        )
        client = provider_failure_metadata(
            ProviderRejected(
                403,
                '{"type":"https://api.x.com/2/problems/client-forbidden",'
                '"reason":"client-not-enrolled","title":"Client Forbidden"}',
            )
        )
        self.assertEqual(automated["failure_class"], "provider_automation_restricted")
        self.assertEqual(automated["provider_error_code"], 226)
        self.assertEqual(duplicate["failure_class"], "provider_duplicate_content")
        self.assertEqual(duplicate["provider_error_code"], 187)
        self.assertEqual(client["failure_class"], "provider_client_forbidden")
        self.assertEqual(client["provider_reason"], "client-not-enrolled")

    def test_usage_limit_opens_account_circuit_and_defers_later_post_before_consequence(self) -> None:
        provider = BoundaryProvider()
        first = self._schedule(provider, campaign="OCPF-002")
        second = create_schedule(
            campaign="OCPF-003",
            provider="x",
            at="2026-09-12T10:10:00+00:00",
            provider_factory=lambda _name: provider,
            now=self.created_at,
        )

        provider.publish_error = ProviderRejected(
            402,
            '{"type":"https://api.x.com/2/problems/usage-capped",'
            '"title":"UsageCapExceeded"}',
        )
        failed = execute_schedule(
            first["schedule_id"],
            provider_factory=lambda _name: provider,
            now=self.due_at,
        )
        self.assertEqual(failed["failure_class"], "provider_usage_limit")
        self.assertEqual(provider.publish_calls, 1)

        circuit = provider_write_circuit(
            "x",
            provider.identity.account_id,
            now=datetime(2026, 9, 12, 10, 11, tzinfo=UTC),
        )
        self.assertTrue(circuit["open"])
        self.assertEqual(circuit["failure_class"], "provider_usage_limit")

        deferred = execute_schedule(
            second["schedule_id"],
            provider_factory=lambda _name: provider,
            now=datetime(2026, 9, 12, 10, 11, tzinfo=UTC),
        )
        self.assertEqual(deferred["status"], "scheduled")
        self.assertEqual(deferred["failure_class"], "provider_circuit_open")
        self.assertEqual(deferred["circuit_failure_class"], "provider_usage_limit")
        self.assertTrue(deferred["automatic_retry"])
        self.assertEqual(provider.publish_calls, 1)

    def test_candidate_specific_duplicate_does_not_open_account_circuit(self) -> None:
        provider = BoundaryProvider()
        schedule = self._schedule(provider)
        provider.publish_error = ProviderRejected(
            403,
            '{"errors":[{"code":187,"message":"Status is a duplicate."}]}',
        )
        failed = execute_schedule(
            schedule["schedule_id"],
            provider_factory=lambda _name: provider,
            now=self.due_at,
        )
        self.assertEqual(failed["failure_class"], "provider_duplicate_content")
        circuit = provider_write_circuit(
            "x",
            provider.identity.account_id,
            now=datetime(2026, 9, 12, 10, 2, tzinfo=UTC),
        )
        self.assertFalse(circuit["open"])

    def test_three_consecutive_generic_forbidden_failures_open_circuit(self) -> None:
        def row(index: int) -> dict:
            return {
                "schedule_id": f"sch-generic-{index}",
                "provider": "x",
                "account_id": "1480506376447315969",
                "status": "failed",
                "updated_at": f"2026-09-12T10:0{index}:00Z",
                "detail": (
                    "Provider rejected the consequence: provider rejected request "
                    '(HTTP 403): {"type":"about:blank","title":"Forbidden",'
                    '"detail":"You are not permitted to perform this action.",'
                    '"status":403}. This schedule will not auto-retry.'
                ),
            }

        with patch("ocpf_post.scheduler.schedule_records", return_value=[row(1), row(2)]):
            closed = provider_write_circuit(
                "x",
                "1480506376447315969",
                now=datetime(2026, 9, 12, 10, 2, 30, tzinfo=UTC),
            )
        self.assertFalse(closed["open"])

        with patch("ocpf_post.scheduler.schedule_records", return_value=[row(1), row(2), row(3)]):
            opened = provider_write_circuit(
                "x",
                "1480506376447315969",
                now=datetime(2026, 9, 12, 10, 3, 30, tzinfo=UTC),
            )
        self.assertTrue(opened["open"])
        self.assertEqual(opened["failure_class"], "provider_forbidden")
        self.assertEqual(opened["trigger"], "repeated_generic_forbidden")
        self.assertEqual(opened["consecutive_failures"], 3)
        self.assertEqual(opened["generic_forbidden_threshold"], 3)
        self.assertFalse(opened["automatic_retry_authority"])

    def test_success_resets_generic_forbidden_streak(self) -> None:
        def failed(index: int) -> dict:
            return {
                "schedule_id": f"sch-generic-{index}",
                "provider": "x",
                "account_id": "1480506376447315969",
                "status": "failed",
                "updated_at": f"2026-09-12T10:0{index}:00Z",
                "detail": (
                    "Provider rejected the consequence: provider rejected request "
                    '(HTTP 403): {"type":"about:blank","title":"Forbidden",'
                    '"detail":"You are not permitted to perform this action.",'
                    '"status":403}. This schedule will not auto-retry.'
                ),
            }

        rows = [
            failed(1),
            failed(2),
            failed(3),
            {
                "schedule_id": "sch-success",
                "provider": "x",
                "account_id": "1480506376447315969",
                "status": "published_verified",
                "updated_at": "2026-09-12T10:04:00Z",
            },
            failed(5),
            failed(6),
        ]
        with patch("ocpf_post.scheduler.schedule_records", return_value=rows):
            circuit = provider_write_circuit(
                "x",
                "1480506376447315969",
                now=datetime(2026, 9, 12, 10, 7, tzinfo=UTC),
            )
        self.assertFalse(circuit["open"])

    def test_read_only_schedule_inspection_has_no_external_consequence(self) -> None:
        provider = BoundaryProvider()
        schedule = self._schedule(provider)
        before = provider.publish_calls
        rows = schedule_records()
        self.assertEqual(rows[0]["schedule_id"], schedule["schedule_id"])
        self.assertEqual(provider.publish_calls, before)
        self.assertEqual(list(iter_receipts()), [])


if __name__ == "__main__":
    unittest.main()
