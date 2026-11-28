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

    def test_circuit_opened_during_preflight_defers_before_durable_claim(self) -> None:
        import multiprocessing as mp
        from pathlib import Path
        ctx = mp.get_context("fork")
        first = self._schedule(BoundaryProvider(), campaign="OCPF-002")
        second = self._schedule(BoundaryProvider(), campaign="OCPF-003")
        entered, release = ctx.Event(), ctx.Event()
        results = ctx.Queue()
        calls = Path(self.tmp.name) / "late-circuit-mock-publications"

        class PausedProvider(BoundaryProvider):
            def account(self):
                entered.set()
                if not release.wait(10):
                    raise AssertionError("preflight release timed out")
                return super().account()

            def publish(self, text):
                calls.write_text("unexpected mock publication")
                return super().publish(text)

        def execute_second():
            result = execute_schedule(second["schedule_id"],
                                      provider_factory=lambda _: PausedProvider(), now=self.due_at)
            results.put(result)

        worker = ctx.Process(target=execute_second)
        worker.start()
        try:
            self.assertTrue(entered.wait(8), "second executor did not reach preflight")
            rejecting = BoundaryProvider()
            rejecting.publish_error = ProviderRejected(
                402, '{"type":"https://api.x.com/2/problems/usage-capped","title":"UsageCapExceeded"}',
            )
            failed = execute_schedule(first["schedule_id"],
                                      provider_factory=lambda _: rejecting, now=self.due_at)
            self.assertEqual(failed["failure_class"], "provider_usage_limit")
            circuit = provider_write_circuit("x", rejecting.identity.account_id, now=self.due_at)
            self.assertTrue(circuit["open"])
            release.set()
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
            result = results.get(timeout=2)
            self.assertEqual(result["status"], "scheduled")
            self.assertEqual(result["failure_class"], "provider_circuit_open")
            self.assertEqual(result["circuit_failure_class"], "provider_usage_limit")
            self.assertEqual(result["retry_at"], circuit["retry_at"])
            self.assertTrue(result["automatic_retry"])
            self.assertFalse(calls.exists())
            self.assertEqual(list(iter_receipts()), [])
        finally:
            release.set()
            if worker.is_alive():
                worker.terminate()
                worker.join(3)

    def test_same_second_later_rejection_is_not_reset_by_earlier_success(self) -> None:
        from types import SimpleNamespace

        with patch("ocpf_post.scheduler.uuid.uuid4", return_value=SimpleNamespace(hex="0" * 31 + "1")):
            rejected = self._schedule(BoundaryProvider(), campaign="OCPF-002")
        with patch("ocpf_post.scheduler.uuid.uuid4", return_value=SimpleNamespace(hex="f" * 32)):
            successful = self._schedule(BoundaryProvider(), campaign="OCPF-003")
        provider = BoundaryProvider()
        provider.publish_error = ProviderRejected(402, '{"title":"UsageCapExceeded"}')
        with patch("ocpf_post.scheduler._utc_now_dt", return_value=self.due_at):
            completed = execute_schedule(successful["schedule_id"],
                                         provider_factory=lambda _: BoundaryProvider(), now=self.due_at)
            failed = execute_schedule(rejected["schedule_id"],
                                      provider_factory=lambda _: provider, now=self.due_at)
        self.assertEqual(completed["status"], "published_verified")
        self.assertEqual(failed["failure_class"], "provider_usage_limit")
        self.assertEqual(completed["updated_at"], failed["updated_at"])
        circuit = provider_write_circuit("x", provider.identity.account_id, now=self.due_at)
        self.assertTrue(circuit["open"])
        self.assertEqual(circuit["source_schedule_id"], rejected["schedule_id"])

    def test_same_second_later_success_resets_earlier_rejection(self) -> None:
        from types import SimpleNamespace
        from ocpf_post.scheduler import _append_event

        # These two different campaigns were already claimed before either
        # completed. Use the durable ledger to record their actual outcome order.
        with patch("ocpf_post.scheduler.uuid.uuid4", return_value=SimpleNamespace(hex="f" * 32)):
            rejected = self._schedule(BoundaryProvider(), campaign="OCPF-002")
        with patch("ocpf_post.scheduler.uuid.uuid4", return_value=SimpleNamespace(hex="0" * 31 + "1")):
            successful = self._schedule(BoundaryProvider(), campaign="OCPF-003")
        timestamp = "2026-09-12T10:01:00Z"
        for reservation in (rejected, successful):
            _append_event({"schedule_id": reservation["schedule_id"], "event": "claimed",
                           "status": "executing", "recorded_at": timestamp})
        _append_event({"schedule_id": rejected["schedule_id"], "event": "failed",
                       "status": "failed", "recorded_at": timestamp,
                       "failure_class": "provider_usage_limit",
                       "failure_stage": "provider_consequence", "provider_http_status": 402})
        self.assertTrue(provider_write_circuit("x", BoundaryProvider().identity.account_id,
                                               now=self.due_at)["open"])
        _append_event({"schedule_id": successful["schedule_id"], "event": "completed",
                       "status": "published_verified", "recorded_at": timestamp})
        self.assertFalse(provider_write_circuit("x", BoundaryProvider().identity.account_id,
                                                now=self.due_at)["open"])

    def _clock_rollback_outcomes(self, *, rejection_last: bool):
        from ocpf_post.scheduler import _append_event

        provider = BoundaryProvider()
        successful = self._schedule(provider, campaign="OCPF-002")
        rejected = self._schedule(provider, campaign="OCPF-003")
        for reservation in (successful, rejected):
            _append_event({"schedule_id": reservation["schedule_id"], "event": "claimed",
                           "status": "executing", "recorded_at": "2026-09-12T10:00:00Z"})
        success = {"schedule_id": successful["schedule_id"], "event": "completed",
                   "status": "published_verified"}
        failure = {"schedule_id": rejected["schedule_id"], "event": "failed",
                   "status": "failed", "failure_class": "provider_usage_limit",
                   "failure_stage": "provider_consequence", "provider_http_status": 402}
        first, second = (success, failure) if rejection_last else (failure, success)
        _append_event({**first, "recorded_at": "2026-09-12T10:05:00Z"})
        _append_event({**second, "recorded_at": "2026-09-12T10:01:00Z"})
        return provider.identity.account_id, rejected["schedule_id"]

    def test_clock_rollback_later_rejection_opens_circuit_until_cooldown(self) -> None:
        account_id, rejected_id = self._clock_rollback_outcomes(rejection_last=True)
        now = datetime(2026, 9, 12, 10, 2, tzinfo=UTC)
        circuit = provider_write_circuit("x", account_id, now=now)
        self.assertTrue(circuit["open"])
        self.assertEqual(circuit["source_schedule_id"], rejected_id)
        self.assertEqual(circuit["retry_at"], "2026-09-12T10:16:00Z")
        self.assertFalse(circuit["automatic_retry_authority"])
        expiry = datetime(2026, 9, 12, 10, 16, tzinfo=UTC)
        self.assertFalse(provider_write_circuit("x", account_id, now=expiry)["open"])

    def test_clock_rollback_later_success_clears_circuit(self) -> None:
        account_id, _ = self._clock_rollback_outcomes(rejection_last=False)
        now = datetime(2026, 9, 12, 10, 2, tzinfo=UTC)
        circuit = provider_write_circuit("x", account_id, now=now)
        self.assertFalse(circuit["open"])
        self.assertNotIn("source_schedule_id", circuit)
        self.assertFalse(circuit["automatic_retry_authority"])

    def test_circuit_reads_durable_outcomes_without_changing_public_projection_contract(self) -> None:
        from ocpf_post import account_profiles

        provider = BoundaryProvider()
        schedule = self._schedule(provider)
        provider.publish_error = ProviderRejected(402, '{"title":"UsageCapExceeded"}')
        with patch("ocpf_post.scheduler._utc_now_dt", return_value=self.due_at):
            failed = execute_schedule(schedule["schedule_id"],
                                      provider_factory=lambda _: provider, now=self.due_at)
        self.assertEqual(failed["failure_class"], "provider_usage_limit")
        # Inventory consumers use a no-argument due-order projection. A circuit
        # must read its own durable outcome projection, not that consumer view.
        with patch("ocpf_post.scheduler.schedule_records", side_effect=lambda: []) as public:
            reason = account_profiles.unavailable("x", provider.identity.account_id, now=self.due_at)
            public.assert_not_called()
        self.assertIn("Provider/account write circuit open", reason)

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

        with patch("ocpf_post.scheduler._schedule_records_in_outcome_order", return_value=[row(1), row(2)]):
            closed = provider_write_circuit(
                "x",
                "1480506376447315969",
                now=datetime(2026, 9, 12, 10, 2, 30, tzinfo=UTC),
            )
        self.assertFalse(closed["open"])

        with patch("ocpf_post.scheduler._schedule_records_in_outcome_order", return_value=[row(1), row(2), row(3)]):
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
        with patch("ocpf_post.scheduler._schedule_records_in_outcome_order", return_value=rows):
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

    def test_cancellation_during_circuit_check(self):
        from ocpf_post.scheduler import cancel_schedule
        provider = BoundaryProvider()
        s = self._schedule(provider)
        def circuit(*a, **kw):
            cancel_schedule(s["schedule_id"])
            return {"open": True, "retry_at": "2026-09-12T11:00:00Z", "failure_class": "provider_usage_limit"}
        with patch("ocpf_post.scheduler.provider_write_circuit", side_effect=circuit):
            r = execute_schedule(s["schedule_id"], provider_factory=lambda _: provider, now=self.due_at)
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(get_schedule(s["schedule_id"])["status"], "cancelled")
        self.assertEqual(provider.publish_calls, 0)
        self.assertEqual(run_due(now=datetime(2026,9,12,12,tzinfo=UTC), provider_factory=lambda _: provider), [])
    def test_forced_process_exit_after_claim(self):
        from ocpf_post.scheduler import cancel_schedule, ScheduleError
        provider = BoundaryProvider()
        s = self._schedule(provider)
        class CrashProvider(BoundaryProvider):
            def publish(self, text):
                os._exit(73)
        pid = os.fork()
        if pid == 0:
            try:
                execute_schedule(s["schedule_id"], provider_factory=lambda _: CrashProvider(), now=self.due_at)
            finally:
                os._exit(74)
        _, code = os.waitpid(pid,0)
        self.assertEqual(os.waitstatus_to_exitcode(code),73)
        self.assertEqual(get_schedule(s["schedule_id"])["status"],"executing")
        with self.assertRaises(ScheduleError):
            cancel_schedule(s["schedule_id"])
        self.assertEqual(run_due(now=datetime(2026,9,12,12,tzinfo=UTC), provider_factory=lambda _: provider), [])
        self.assertEqual(provider.publish_calls,0)
        self.assertEqual(list(iter_receipts()),[])
    def test_two_full_executors_only_one_publish(self):
        import multiprocessing as mp
        import pathlib
        provider=BoundaryProvider()
        s=self._schedule(provider)
        ctx=mp.get_context("fork")
        gate=ctx.Barrier(2,timeout=8)
        calls=pathlib.Path(self.tmp.name)/"mock-publish-count"
        class RacingProvider(BoundaryProvider):
            def account(self):
                gate.wait()
                return super().account()
            def publish(self,text):
                with calls.open("a") as f:
                    f.write("called\n")
                return super().publish(text)
        def work():
            execute_schedule(s["schedule_id"],provider_factory=lambda _: RacingProvider(),now=self.due_at)
        workers=[ctx.Process(target=work) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(12)
            if worker.is_alive():
                worker.terminate(); worker.join()
                self.fail("worker timed out")
            self.assertEqual(worker.exitcode,0)
        self.assertEqual(calls.read_text().splitlines(),["called"])
        self.assertEqual(get_schedule(s["schedule_id"])["status"],"published_verified")

    def test_cancel_during_preflight_never_dispatches_or_retries(self):
        from ocpf_post.scheduler import cancel_schedule
        from ocpf_post.providers.base import ProviderUnavailable
        for outcome in ("success", "unavailable", "rejected"):
            with self.subTest(outcome=outcome):
                provider = BoundaryProvider()
                reservation = self._schedule(provider)
                def account():
                    cancel_schedule(reservation["schedule_id"])
                    if outcome == "unavailable":
                        raise ProviderUnavailable("preflight unavailable")
                    if outcome == "rejected":
                        raise ProviderRejected(401, "rejected")
                    return provider.identity
                provider.account = account
                result = execute_schedule(reservation["schedule_id"], provider_factory=lambda _: provider, now=self.due_at)
                self.assertEqual(result["status"], "cancelled")
                self.assertEqual(get_schedule(reservation["schedule_id"])["status"], "cancelled")
                self.assertEqual(provider.publish_calls, 0)
                self.assertEqual(run_due(now=datetime(2026,9,12,12,tzinfo=UTC), provider_factory=lambda _: provider), [])

    def test_segmented_schedule_cancel_is_durable_and_never_dispatches(self):
        from ocpf_post.scheduler import cancel_schedule, schedules_file
        from ocpf_post.ledger import append_jsonl, segment_jsonl, read_jsonl
        provider = BoundaryProvider()
        reservation = self._schedule(provider)
        path = schedules_file()
        for index in range(1001):
            append_jsonl(path, {"schedule_id": "history-"+str(index), "event": "cancelled", "status": "cancelled", "recorded_at": "2026-09-12T08:00:00Z"})
        before = read_jsonl(path)
        preview = segment_jsonl(path, keep_lines=1000)
        segment_jsonl(path, keep_lines=1000, apply=True, expected_sha256=preview["review_sha256"])
        self.assertEqual(read_jsonl(path), before)
        cancelled = cancel_schedule(reservation["schedule_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(execute_schedule(reservation["schedule_id"], provider_factory=lambda _: provider, now=self.due_at)["status"], "cancelled")
        self.assertEqual(provider.publish_calls, 0)
        self.assertEqual(read_jsonl(path)[:-1], before)

    def test_pending_segmentation_blocks_claim_and_cancel(self):
        from ocpf_post.scheduler import cancel_schedule, schedules_file
        from ocpf_post.ledger import LedgerIntegrityError
        provider = BoundaryProvider()
        reservation = self._schedule(provider)
        path = schedules_file()
        marker = path.with_name(path.name+".segments") / "SEGMENTATION.json"
        marker.parent.mkdir()
        marker.write_text("{}")
        original = path.read_bytes()
        with self.assertRaises(LedgerIntegrityError):
            cancel_schedule(reservation["schedule_id"])
        with self.assertRaises(LedgerIntegrityError):
            execute_schedule(reservation["schedule_id"], provider_factory=lambda _: provider, now=self.due_at)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(provider.publish_calls, 0)

    def test_claim_short_write_completes_before_provider_effect(self):
        from ocpf_post import ledger
        from ocpf_post.scheduler import schedules_file
        provider = BoundaryProvider()
        reservation = self._schedule(provider)
        original_write = os.write
        def short_write(fd, data):
            return original_write(fd, data[:max(1,len(data)//2)])
        with patch.object(ledger.os, "write", side_effect=short_write):
            result = execute_schedule(reservation["schedule_id"], provider_factory=lambda _: provider, now=self.due_at)
        self.assertEqual(result["status"], "published_verified")
        self.assertEqual(provider.publish_calls, 1)
        self.assertEqual(get_schedule(reservation["schedule_id"])["status"], "published_verified")
        self.assertTrue(schedules_file().read_bytes().endswith(b"\n"))

    def test_partial_claim_write_refuses_provider_and_replay(self):
        from ocpf_post import ledger
        from ocpf_post.scheduler import schedules_file, cancel_schedule
        from ocpf_post.ledger import LedgerIntegrityError
        provider = BoundaryProvider()
        reservation = self._schedule(provider)
        original_write = os.write
        count = 0
        def partial_failure(fd, data):
            nonlocal count
            count += 1
            if count == 1:
                return original_write(fd, data[:max(1,len(data)//2)])
            raise OSError("injected ledger write failure")
        with patch.object(ledger.os, "write", side_effect=partial_failure):
            with self.assertRaises(OSError):
                execute_schedule(reservation["schedule_id"], provider_factory=lambda _: provider, now=self.due_at)
        self.assertEqual(provider.publish_calls, 0)
        damaged = schedules_file().read_bytes()
        with self.assertRaises(LedgerIntegrityError):
            execute_schedule(reservation["schedule_id"], provider_factory=lambda _: provider, now=self.due_at)
        with self.assertRaises(LedgerIntegrityError):
            cancel_schedule(reservation["schedule_id"])
        self.assertEqual(schedules_file().read_bytes(), damaged)
        self.assertEqual(provider.publish_calls, 0)

    def test_claim_fsync_failure_prevents_effect_and_never_replays(self):
        from ocpf_post import ledger
        provider = BoundaryProvider()
        reservation = self._schedule(provider)
        with patch.object(ledger.os, "fsync", side_effect=OSError("injected fsync failure")):
            with self.assertRaises(OSError):
                execute_schedule(reservation["schedule_id"], provider_factory=lambda _: provider, now=self.due_at)
        self.assertEqual(provider.publish_calls, 0)
        self.assertEqual(get_schedule(reservation["schedule_id"])["status"], "executing")
        self.assertEqual(run_due(now=datetime(2026,9,12,12,tzinfo=UTC), provider_factory=lambda _: provider), [])
        self.assertEqual(provider.publish_calls, 0)


class ConditionalLedgerIntegrityTests(unittest.TestCase):
    def test_conditional_append_does_not_write_on_refusal_or_bad_tail(self):
        from pathlib import Path
        from ocpf_post.ledger import append_jsonl, append_jsonl_if, read_jsonl, LedgerIntegrityError
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"events.jsonl"
            append_jsonl(path, {"id": 1})
            original = path.read_bytes()
            self.assertFalse(append_jsonl_if(path, lambda: None))
            self.assertEqual(path.read_bytes(), original)
            path.write_bytes(original+b'{"id":2}')
            damaged = path.read_bytes()
            with self.assertRaises(LedgerIntegrityError):
                read_jsonl(path)
            with self.assertRaises(LedgerIntegrityError):
                append_jsonl_if(path, lambda: {"id": 3})
            self.assertEqual(path.read_bytes(), damaged)

    def test_stale_segmentation_review_refuses_after_conditional_append(self):
        from pathlib import Path
        from ocpf_post.ledger import append_jsonl, append_jsonl_if, segment_jsonl, read_jsonl
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"events.jsonl"
            path.write_text("".join('{"id":%d}\n'%i for i in range(1002)))
            review = segment_jsonl(path, keep_lines=1000)
            self.assertTrue(append_jsonl_if(path, lambda: {"id": 1002}))
            with self.assertRaises(ValueError):
                segment_jsonl(path, keep_lines=1000, apply=True, expected_sha256=review["review_sha256"])
            self.assertEqual([r["id"] for r in read_jsonl(path)], list(range(1003)))

    def test_recover_interrupted_segmentation_preserves_conditional_append(self):
        from pathlib import Path
        from ocpf_post import ledger
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            original = [{"id": i} for i in range(1002)]
            path.write_text("".join('{"id":%d}\n' % i for i in range(1002)))
            review = ledger.segment_jsonl(path, keep_lines=1000)
            replace = os.replace
            def interrupt(source, destination):
                if str(source).endswith(".pending"):
                    raise OSError("injected failure before segment finalisation")
                return replace(source, destination)
            with patch.object(ledger.os, "replace", side_effect=interrupt):
                with self.assertRaises(OSError):
                    ledger.segment_jsonl(path, keep_lines=1000, apply=True, expected_sha256=review["review_sha256"])
            selector = __import__("unittest.mock", fromlist=["Mock"]).Mock(return_value={"id": 1002})
            with self.assertRaises(ledger.LedgerIntegrityError):
                ledger.append_jsonl_if(path, selector)
            selector.assert_not_called()
            with self.assertRaises(ledger.LedgerIntegrityError):
                ledger.read_jsonl(path)
            self.assertEqual(ledger.recover_segmentation(path)["status"], "recovery_preview")
            self.assertEqual(ledger.recover_segmentation(path, apply=True)["status"], "recovered")
            self.assertEqual(ledger.read_jsonl(path), original)
            self.assertTrue(ledger.append_jsonl_if(path, selector))
            selector.assert_called_once()
            self.assertEqual(ledger.read_jsonl(path), original + [{"id": 1002}])

    def test_selector_failure_and_invalid_unconditional_append_do_not_change_ledger(self):
        from pathlib import Path
        from ocpf_post import ledger
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            ledger.append_jsonl(path, {"id": 1})
            original = path.read_bytes()
            def fail():
                raise RuntimeError("selection failed")
            with self.assertRaises(RuntimeError):
                ledger.append_jsonl_if(path, fail)
            with self.assertRaises(TypeError):
                ledger.append_jsonl(path, None)
            self.assertEqual(path.read_bytes(), original)

    def test_rotation_during_receipt_snapshot_cannot_hide_terminal_effect(self):
        import json
        from pathlib import Path
        from ocpf_post import ledger, state
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "publish-receipts.jsonl"
            terminal = {"campaign": "C", "provider": "x", "account_id": "A",
                        "status": "published_verified"}
            rows = [terminal] + [{"campaign": "other", "provider": "x", "account_id": "A",
                                  "status": "published_verified"} for _ in range(1001)]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            review = ledger.segment_jsonl(path, keep_lines=1000)
            original = ledger._segment_files
            rotated = False
            def rotate_after_listing(selected):
                nonlocal rotated
                result = original(selected)
                if not rotated:
                    rotated = True
                    ledger.segment_jsonl(selected, keep_lines=1000, apply=True,
                                         expected_sha256=review["review_sha256"])
                return result
            with patch.object(state, "receipts_file", return_value=path):
                self.assertEqual(state.terminal_effect_receipt("C", "x", "A"), terminal)
                with patch.object(ledger, "_segment_files", side_effect=rotate_after_listing):
                    with self.assertRaises(ledger.LedgerIntegrityError):
                        state.terminal_effect_receipt("C", "x", "A")
                self.assertEqual(state.terminal_effect_receipt("C", "x", "A"), terminal)
            self.assertEqual(ledger.read_jsonl(path), rows)

    def test_pinned_reader_preserves_prefix_through_later_rotation_and_append(self):
        from pathlib import Path
        from ocpf_post import ledger
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            rows = [{"id": i} for i in range(1002)]
            path.write_text("".join('{"id":%d}\n' % i for i in range(1002)))
            review = ledger.segment_jsonl(path, keep_lines=1000)
            reader = ledger.iter_jsonl(path)
            first = next(reader)
            ledger.segment_jsonl(path, keep_lines=1000, apply=True,
                                 expected_sha256=review["review_sha256"])
            ledger.append_jsonl(path, {"id": 1002})
            self.assertEqual([first, *reader], rows)
            self.assertEqual(ledger.read_jsonl(path), rows + [{"id": 1002}])

    def test_pinned_active_snapshot_excludes_later_appends_and_remains_read_only(self):
        from pathlib import Path
        from ocpf_post import ledger
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            self.assertEqual(ledger.read_jsonl(path), [])
            self.assertEqual(list(Path(tmp).iterdir()), [])
            path.write_text('{"id":0}\n{"id":1}\n')
            reader = ledger.iter_jsonl(path)
            first = next(reader)
            ledger.append_jsonl(path, {"id": 2})
            self.assertEqual([first, *reader], [{"id": 0}, {"id": 1}])
            self.assertEqual(ledger.read_jsonl(path), [{"id": 0}, {"id": 1}, {"id": 2}])



class DistinctReservationRaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"OCPF_POST_STATE_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.created_at = datetime(2026, 9, 12, 8, tzinfo=UTC)
        self.due_at = datetime(2026, 9, 12, 10, 1, tzinfo=UTC)

    def _reserve(self):
        return create_schedule(campaign="OCPF-002", provider="x",
                               at="2026-09-12T10:00:00+00:00",
                               provider_factory=lambda _: BoundaryProvider(), now=self.created_at)

    def _join(self, workers):
        for worker in workers:
            worker.start()
        try:
            for worker in workers:
                worker.join(15)
                self.assertFalse(worker.is_alive(), "worker timed out")
                self.assertEqual(worker.exitcode, 0)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(3)

    def test_concurrent_creation_reserves_only_one_distinct_id(self):
        import multiprocessing as mp
        from pathlib import Path
        from ocpf_post import scheduler
        ctx = mp.get_context("fork")
        gate = ctx.Barrier(2, timeout=8)
        results = Path(self.tmp.name)/"creation-results"
        original = scheduler._append_event
        conditional = scheduler.append_jsonl_if
        def append(event):
            gate.wait()
            return original(event)
        def append_if(path, select):
            gate.wait()
            return conditional(path, select)
        def reserve():
            with patch.object(scheduler, "_append_event", side_effect=append), patch.object(scheduler, "append_jsonl_if", side_effect=append_if):
                try:
                    self._reserve()
                    outcome = "reserved"
                except scheduler.ScheduleError:
                    outcome = "refused"
                with results.open("a") as f:
                    f.write(outcome + "\n")
        self._join([ctx.Process(target=reserve) for _ in range(2)])
        self.assertEqual(sorted(results.read_text().splitlines()), ["refused", "reserved"])
        self.assertEqual(len(schedule_records()), 1)

    def _legacy_duplicate(self):
        from ocpf_post import scheduler
        original = self._reserve()
        duplicate = dict(original, schedule_id="sch_legacy_duplicate")
        scheduler._append_event(duplicate)
        return original["schedule_id"], duplicate["schedule_id"]

    def test_legacy_duplicate_ids_competing_to_dispatch_only_one_publishes(self):
        import multiprocessing as mp
        from pathlib import Path
        from ocpf_post import scheduler
        first, second = self._legacy_duplicate()
        ctx = mp.get_context("fork")
        gate = ctx.Barrier(2, timeout=8)
        calls = Path(self.tmp.name)/"mock-publish-count"
        original = scheduler._transition_scheduled
        class RacingProvider(BoundaryProvider):
            def publish(self, text):
                with calls.open("a") as f:
                    f.write("mock\n")
                return super().publish(text)
        def transition(*args, **kwargs):
            gate.wait()
            return original(*args, **kwargs)
        def work(schedule_id):
            with patch.object(scheduler, "_transition_scheduled", side_effect=transition):
                execute_schedule(schedule_id, provider_factory=lambda _: RacingProvider(), now=self.due_at)
        self._join([ctx.Process(target=work,args=(key,)) for key in (first,second)])
        self.assertEqual(calls.read_text().splitlines(), ["mock"])
        self.assertEqual(sorted(row["status"] for row in schedule_records()), ["duplicate_blocked", "published_verified"])
        self.assertEqual(run_due(now=self.due_at, provider_factory=lambda _: BoundaryProvider()), [])

    def test_legacy_duplicate_cannot_replay_crashed_claim(self):
        first, second = self._legacy_duplicate()
        class CrashProvider(BoundaryProvider):
            def publish(self, text):
                os._exit(73)
        pid = os.fork()
        if pid == 0:
            try:
                execute_schedule(first, provider_factory=lambda _: CrashProvider(), now=self.due_at)
            finally:
                os._exit(74)
        _, status = os.waitpid(pid,0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        self.assertEqual(get_schedule(first)["status"], "executing")
        provider = BoundaryProvider()
        result = execute_schedule(second, provider_factory=lambda _: provider, now=self.due_at)
        self.assertEqual(result["status"], "duplicate_blocked")
        self.assertEqual(provider.publish_calls, 0)
        self.assertEqual(run_due(now=self.due_at, provider_factory=lambda _: provider), [])
        self.assertEqual(list(iter_receipts()), [])



if __name__ == "__main__":
    unittest.main()
