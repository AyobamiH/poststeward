from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ocpf_post.model import AccountIdentity
from ocpf_post.providers.base import AmbiguousProviderEffect, ProviderRejected, ProviderUnavailable
from ocpf_post.scheduler import (
    ScheduleError,
    cancel_schedule,
    create_schedule,
    due_schedules,
    execute_schedule,
    get_schedule,
    parse_schedule_at,
    run_due,
    schedule_records,
)
from ocpf_post.state import iter_receipts

UTC = timezone.utc


class FakeProvider:
    def __init__(self, *, account_id: str = "1480506376447315969", username: str = "JohnWOE15",
                 verify: bool = True, ambiguous: bool = False, account_error: Exception | None = None) -> None:
        self.identity = AccountIdentity(provider="x", account_id=account_id, username=username)
        self.verify = verify
        self.ambiguous = ambiguous
        self.account_error = account_error
        self.account_calls = 0
        self.published: list[str] = []
        self.replies: list[tuple[str, str]] = []

    def account(self):
        self.account_calls += 1
        if self.account_error is not None:
            raise self.account_error
        return self.identity

    def publish(self, text: str):
        if self.ambiguous:
            raise AmbiguousProviderEffect("uncertain write")
        self.published.append(text)
        return {"id": "post-123"}

    def reply(self, text: str, reply_to_id: str):
        self.replies.append((text, reply_to_id))
        return {"id": f"reply-{len(self.replies)}"}

    def verify_post(self, post_id: str, text: str) -> bool:
        return self.verify

    def post_url(self, account, post_id: str) -> str:
        return f"https://example.invalid/{account.account_id}/{post_id}"


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_state = os.environ.get("OCPF_POST_STATE_DIR")
        os.environ["OCPF_POST_STATE_DIR"] = self.tmp.name
        self.base = datetime(2026, 9, 6, 20, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        if self.old_state is None:
            os.environ.pop("OCPF_POST_STATE_DIR", None)
        else:
            os.environ["OCPF_POST_STATE_DIR"] = self.old_state
        self.tmp.cleanup()

    def test_parse_london_time_to_utc(self) -> None:
        value, zone = parse_schedule_at("2026-09-07 10:00 Europe/London")
        self.assertEqual(value, datetime(2026, 9, 7, 9, 0, tzinfo=UTC))
        self.assertEqual(zone, "Europe/London")

    def test_create_schedule_is_consequence_free_and_persists_snapshot(self) -> None:
        fake = FakeProvider()
        schedule = create_schedule(
            campaign="OCPF-002",
            provider="x",
            at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: fake,
            now=self.base,
        )
        self.assertEqual(fake.published, [])
        self.assertEqual(schedule["status"], "scheduled")
        self.assertEqual(schedule["account_id"], "1480506376447315969")
        self.assertEqual(len(schedule["text_sha256"]), 64)
        self.assertEqual(get_schedule(schedule["schedule_id"])["text"], schedule["text"])

    def test_create_schedule_reports_preflight_unavailable_without_persisting(self) -> None:
        unavailable = FakeProvider(account_error=ProviderUnavailable("temporary DNS failure"))
        with self.assertRaisesRegex(ScheduleError, "temporarily unavailable"):
            create_schedule(
                campaign="OCPF-002",
                provider="x",
                at="2026-09-07 10:00 Europe/London",
                provider_factory=lambda _name: unavailable,
                now=self.base,
            )
        self.assertEqual(schedule_records(), [])
        self.assertEqual(unavailable.published, [])

    def test_duplicate_active_schedule_is_rejected(self) -> None:
        fake = FakeProvider()
        kwargs = dict(
            campaign="OCPF-002",
            provider="x",
            at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: fake,
            now=self.base,
        )
        create_schedule(**kwargs)
        with self.assertRaisesRegex(ScheduleError, "active schedule"):
            create_schedule(**kwargs)

    def test_cancel_is_terminal_without_consequence(self) -> None:
        fake = FakeProvider()
        schedule = create_schedule(
            campaign="OCPF-002",
            provider="x",
            at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: fake,
            now=self.base,
        )
        cancelled = cancel_schedule(schedule["schedule_id"], now=self.base)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(fake.published, [])

    def test_run_due_publishes_once_and_records_verified_receipt(self) -> None:
        fake = FakeProvider(verify=True)
        schedule = create_schedule(
            campaign="OCPF-002",
            provider="x",
            at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: fake,
            now=self.base,
        )
        results = run_due(
            now=datetime(2026, 9, 7, 9, 1, tzinfo=UTC),
            provider_factory=lambda _name: fake,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "published_verified")
        self.assertEqual(len(fake.published), 1)
        receipts = list(iter_receipts())
        self.assertEqual(receipts[-1]["status"], "published_verified")
        self.assertEqual(receipts[-1]["campaign"], "OCPF-002")
        self.assertEqual(get_schedule(schedule["schedule_id"])["post_id"], "post-123")

    def test_long_x_payload_is_one_frozen_thread_publication(self) -> None:
        fake = FakeProvider(verify=True)
        long_text = (
            "The root explains why consequential agent work needs evidence rather than confidence. "
            "It should preserve the complete approved thought instead of silently truncating it. "
            "A second section explains how provider ambiguity changes retry safety and why durable IDs matter. "
            "A third section closes with the practical lesson for operators who run several products at once. "
            "Every sentence in this approved payload must survive publication."
        )
        with patch("ocpf_post.scheduler.builtin_text", return_value=long_text):
            schedule = create_schedule(
                campaign="OCPF-002",
                provider="x",
                at="2026-09-07 10:00 Europe/London",
                provider_factory=lambda _name: fake,
                now=self.base,
            )
            self.assertEqual(schedule["publication_type"], "thread")
            self.assertGreater(schedule["part_count"], 1)
            result = execute_schedule(
                schedule["schedule_id"],
                provider_factory=lambda _name: fake,
                now=datetime(2026, 9, 7, 9, 1, tzinfo=UTC),
            )
        self.assertEqual(result["status"], "published_verified")
        self.assertEqual(fake.published, [schedule["publication_parts"][0]["text"]])
        self.assertEqual(len(fake.replies), schedule["part_count"] - 1)
        previous = "post-123"
        for offset, (text, reply_to) in enumerate(fake.replies, start=2):
            self.assertEqual(reply_to, previous)
            self.assertEqual(text, schedule["publication_parts"][offset - 1]["text"])
            previous = f"reply-{offset - 1}"
        receipt = list(iter_receipts())[-1]
        self.assertEqual(receipt["publication_type"], "thread")
        self.assertEqual(receipt["part_count"], schedule["part_count"])
        self.assertEqual(receipt["post_id"], "post-123")
        events = [row for row in schedule_records() if row["schedule_id"] == schedule["schedule_id"]]
        self.assertEqual(events[0]["publication_completed_parts"], schedule["part_count"])
        self.assertEqual(
            events[0]["publication_part_ids"],
            ["post-123", *[f"reply-{index}" for index in range(1, schedule["part_count"])]],
        )

    def test_definite_mid_thread_rejection_is_terminal_partial_effect(self) -> None:
        class RejectingContinuation(FakeProvider):
            def reply(self, text: str, reply_to_id: str):
                raise ProviderRejected(400, "rejected continuation")

        fake = RejectingContinuation(verify=True)
        long_text = " ".join(f"approved-{index}" for index in range(90))
        with patch("ocpf_post.scheduler.builtin_text", return_value=long_text):
            schedule = create_schedule(
                campaign="OCPF-002",
                provider="x",
                at="2026-09-07 10:00 Europe/London",
                provider_factory=lambda _name: fake,
                now=self.base,
            )
            self.assertEqual(schedule["publication_type"], "thread")
            first = execute_schedule(
                schedule["schedule_id"],
                provider_factory=lambda _name: fake,
                now=datetime(2026, 9, 7, 9, 1, tzinfo=UTC),
            )
            second = execute_schedule(
                schedule["schedule_id"],
                provider_factory=lambda _name: fake,
                now=datetime(2026, 9, 7, 9, 2, tzinfo=UTC),
            )
        self.assertEqual(first["status"], "partial_effect")
        self.assertEqual(second["status"], "partial_effect")
        self.assertEqual(len(fake.published), 1)
        self.assertEqual(list(iter_receipts())[-1]["status"], "partial_effect")
        self.assertEqual(first["publication_completed_parts"], 1)
        self.assertEqual(first["publication_failed_part_index"], 2)

    def test_preflight_dns_failure_defers_without_publish_then_recovers_once(self) -> None:
        good = FakeProvider(verify=True)
        schedule = create_schedule(
            campaign="OCPF-002",
            provider="x",
            at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: good,
            now=self.base,
        )
        outage = FakeProvider(account_error=ProviderUnavailable("temporary DNS lookup failure"))
        first_due = datetime(2026, 9, 7, 9, 1, tzinfo=UTC)
        result = run_due(now=first_due, provider_factory=lambda _name: outage)
        self.assertEqual(len(result), 1)
        deferred = result[0]
        self.assertEqual(deferred["status"], "scheduled")
        self.assertEqual(deferred["failure_class"], "provider_unavailable")
        self.assertEqual(deferred["preflight_attempts"], 1)
        self.assertEqual(deferred["retry_at"], "2026-09-07T09:02:00Z")
        self.assertEqual(outage.published, [])
        self.assertEqual(list(iter_receipts()), [])
        self.assertEqual(due_schedules(now=first_due + timedelta(seconds=59)), [])
        self.assertEqual(execute_schedule(schedule["schedule_id"], provider_factory=lambda _name: outage,
                                          now=first_due + timedelta(seconds=59))["preflight_attempts"], 1)
        self.assertEqual(outage.account_calls, 1)

        recovered = run_due(now=first_due + timedelta(minutes=1), provider_factory=lambda _name: good)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["status"], "published_verified")
        self.assertEqual(good.published, [schedule["text"]])
        self.assertIsNone(recovered[0].get("retry_at"))
        self.assertIsNone(recovered[0].get("failure_class"))
        self.assertEqual(run_due(now=first_due + timedelta(minutes=2), provider_factory=lambda _name: good), [])
        self.assertEqual(len(good.published), 1)

    def test_preflight_backoff_is_exponential_and_capped(self) -> None:
        good = FakeProvider()
        schedule = create_schedule(
            campaign="OCPF-002", provider="x", at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: good, now=self.base,
        )
        outage = FakeProvider(account_error=ProviderUnavailable("dns unavailable"))
        moment = datetime(2026, 9, 7, 9, 1, tzinfo=UTC)
        expected_delays = [60, 120, 240, 480, 900, 900]
        for attempt, delay in enumerate(expected_delays, 1):
            result = execute_schedule(schedule["schedule_id"], provider_factory=lambda _name: outage, now=moment)
            self.assertEqual(result["preflight_attempts"], attempt)
            self.assertEqual(result["retry_at"], (moment + timedelta(seconds=delay)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            moment += timedelta(seconds=delay)
        self.assertEqual(outage.published, [])
        self.assertEqual(list(iter_receipts()), [])

    def test_untyped_zero_status_provider_rejection_is_terminal_not_retryable(self) -> None:
        good = FakeProvider()
        first = create_schedule(
            campaign="OCPF-002", provider="x", at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: good, now=self.base,
        )
        # This exact prose used to create retry authority. Provider adapters must
        # now use typed ProviderUnavailable for safe pre-consequence deferral.
        account_network = FakeProvider(account_error=ProviderRejected(0, "X account verification network error: DNS"))
        result = execute_schedule(
            first["schedule_id"], provider_factory=lambda _name: account_network,
            now=datetime(2026, 9, 7, 9, 1, tzinfo=UTC),
        )
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result.get("retry_at"))
        self.assertEqual(result["failure_class"], "provider_rejected")
        self.assertEqual(result["failure_stage"], "identity_preflight")
        self.assertEqual(result["provider_http_status"], 0)
        self.assertFalse(result["automatic_retry"])
        self.assertEqual(account_network.published, [])

        second = create_schedule(
            campaign="OCPF-003", provider="x", at="2026-09-07 11:00 Europe/London",
            provider_factory=lambda _name: good, now=self.base,
        )
        refresh_network = FakeProvider(account_error=ProviderRejected(0, "X refresh network error: timeout"))
        failed = execute_schedule(
            second["schedule_id"], provider_factory=lambda _name: refresh_network,
            now=datetime(2026, 9, 7, 10, 1, tzinfo=UTC),
        )
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("retry_at", failed)
        self.assertEqual(refresh_network.published, [])

    def test_explicit_transient_preflight_defers_but_credential_rejection_is_terminal(self) -> None:
        good = FakeProvider()
        transient = create_schedule(
            campaign="OCPF-002", provider="x", at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: good, now=self.base,
        )
        unavailable = FakeProvider(account_error=ProviderUnavailable("provider read-only preflight returned HTTP 503"))
        deferred = execute_schedule(
            transient["schedule_id"], provider_factory=lambda _name: unavailable,
            now=datetime(2026, 9, 7, 9, 1, tzinfo=UTC),
        )
        self.assertEqual(deferred["status"], "scheduled")
        self.assertEqual(deferred["failure_class"], "provider_unavailable")

        credential = create_schedule(
            campaign="OCPF-003", provider="x", at="2026-09-07 11:00 Europe/London",
            provider_factory=lambda _name: good, now=self.base,
        )
        denied = FakeProvider(account_error=ProviderRejected(401, "invalid token"))
        failed = execute_schedule(
            credential["schedule_id"], provider_factory=lambda _name: denied,
            now=datetime(2026, 9, 7, 10, 1, tzinfo=UTC),
        )
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed.get("retry_at"))
        self.assertEqual(failed["failure_class"], "provider_auth_rejected")
        self.assertEqual(failed["failure_stage"], "identity_preflight")
        self.assertEqual(failed["provider_http_status"], 401)
        self.assertFalse(failed["automatic_retry"])
        self.assertEqual(denied.published, [])

    def test_manual_schedule_rejects_legacy_unattested_generated_campaign(self) -> None:
        account_id = "urn:li:person:test"
        fake = FakeProvider(account_id=account_id)
        first = "Generated LinkedIn copy must keep current authority and editorial checks."
        second = first
        while len(first) + 2 + len(second) < 360:
            second += " " + first
        text = f"{first}\n\n{second}"
        manifest = {
            "campaign": "OCPF-GEN-LEGACY",
            "project": "oneclickpostfactory",
            "providers": ["linkedin"],
            "runtime_generated": True,
            "payload_frozen": True,
            "source": {"type": "evidence_grounded_generation", "source_sha": "a" * 40},
        }
        with patch("ocpf_post.scheduler.builtin_text", return_value=text), \
             patch("ocpf_post.providers.for_campaign", return_value=fake), \
             patch("ocpf_post.scheduler.destination_binding_error", return_value=None), \
             patch("ocpf_post.campaigns.builtin_manifest", return_value=manifest), \
             patch("ocpf_post.vault_sync.guard", return_value=None), \
             patch("ocpf_post.source_guard.guard", return_value=None):
            with self.assertRaisesRegex(ScheduleError, "lacks scoped admission attestation"):
                create_schedule(
                    campaign="OCPF-GEN-LEGACY",
                    provider="linkedin",
                    at="2026-09-07 10:00 Europe/London",
                    provider_factory=lambda _name: fake,
                    now=self.base,
                )
        self.assertEqual(schedule_records(), [])
        self.assertEqual(fake.published, [])

    def test_pending_generated_schedule_revalidates_before_provider_consequence(self) -> None:
        account_id = "urn:li:person:test"
        fake = FakeProvider(account_id=account_id)
        first = "Generated LinkedIn copy must keep current authority and editorial checks."
        second = first
        while len(first) + 2 + len(second) < 360:
            second += " " + first
        text = f"{first}\n\n{second}"
        valid_manifest = {
            "campaign": "OCPF-GEN-ATTESTED",
            "project": "oneclickpostfactory",
            "providers": ["linkedin"],
            "runtime_generated": True,
            "payload_frozen": True,
            "source": {"type": "evidence_grounded_generation", "source_sha": "a" * 40},
            "admission": {
                "schema_version": 1,
                "gate": "scoped_admission",
                "providers": {
                    "linkedin": {
                        "account_id": account_id,
                        "admitted_at": "2026-09-06T19:00:00Z",
                        "scope": f"linkedin:{account_id}",
                    }
                },
            },
        }
        legacy_manifest = {key: value for key, value in valid_manifest.items() if key != "admission"}
        with patch("ocpf_post.scheduler.builtin_text", return_value=text), \
             patch("ocpf_post.providers.for_campaign", return_value=fake), \
             patch("ocpf_post.scheduler.destination_binding_error", return_value=None), \
             patch("ocpf_post.campaigns.builtin_manifest", return_value=valid_manifest), \
             patch("ocpf_post.vault_sync.guard", return_value=None), \
             patch("ocpf_post.source_guard.guard", return_value=None):
            schedule = create_schedule(
                campaign="OCPF-GEN-ATTESTED",
                provider="linkedin",
                at="2026-09-07 10:00 Europe/London",
                provider_factory=lambda _name: fake,
                now=self.base,
            )

        with patch("ocpf_post.scheduler.builtin_text", return_value=text), \
             patch("ocpf_post.providers.for_account", return_value=fake), \
             patch("ocpf_post.scheduler.destination_binding_error", return_value=None), \
             patch("ocpf_post.campaigns.builtin_manifest", return_value=legacy_manifest), \
             patch("ocpf_post.vault_sync.guard", return_value=None), \
             patch("ocpf_post.source_guard.guard", return_value=None):
            result = execute_schedule(
                schedule["schedule_id"],
                provider_factory=lambda _name: fake,
                now=datetime(2026, 9, 7, 9, 1, tzinfo=UTC),
            )
        self.assertEqual(result["status"], "drift_blocked")
        self.assertIn("lacks scoped admission attestation", result["detail"])
        self.assertEqual(fake.published, [])

    def test_campaign_payload_drift_fails_closed_before_publish(self) -> None:
        fake = FakeProvider()
        schedule = create_schedule(
            campaign="OCPF-002",
            provider="x",
            at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: fake,
            now=self.base,
        )
        with patch("ocpf_post.scheduler.builtin_text", return_value="changed after scheduling"):
            result = execute_schedule(schedule["schedule_id"], provider_factory=lambda _name: fake)
        self.assertEqual(result["status"], "drift_blocked")
        self.assertEqual(fake.published, [])

    def test_account_drift_fails_closed(self) -> None:
        good = FakeProvider()
        schedule = create_schedule(
            campaign="OCPF-002",
            provider="x",
            at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: good,
            now=self.base,
        )
        wrong = FakeProvider(account_id="wrong", username="wrong")
        result = execute_schedule(schedule["schedule_id"], provider_factory=lambda _name: wrong)
        self.assertEqual(result["status"], "drift_blocked")
        self.assertEqual(wrong.published, [])

    def test_ambiguous_effect_is_terminal_and_receipted(self) -> None:
        initial = FakeProvider()
        schedule = create_schedule(
            campaign="OCPF-002",
            provider="x",
            at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: initial,
            now=self.base,
        )
        ambiguous = FakeProvider(ambiguous=True)
        result = execute_schedule(schedule["schedule_id"], provider_factory=lambda _name: ambiguous)
        self.assertEqual(result["status"], "ambiguous_effect")
        self.assertEqual(list(iter_receipts())[-1]["status"], "ambiguous_effect")
        second = execute_schedule(schedule["schedule_id"], provider_factory=lambda _name: ambiguous)
        self.assertEqual(second["status"], "ambiguous_effect")

    def test_schedule_records_reconstruct_latest_state(self) -> None:
        fake = FakeProvider()
        schedule = create_schedule(
            campaign="OCPF-002",
            provider="x",
            at="2026-09-07 10:00 Europe/London",
            provider_factory=lambda _name: fake,
            now=self.base,
        )
        cancel_schedule(schedule["schedule_id"], now=self.base)
        records = schedule_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "cancelled")
        self.assertEqual(records[0]["last_event"], "cancelled")


if __name__ == "__main__":
    unittest.main()
