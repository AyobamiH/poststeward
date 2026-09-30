from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ocpf_post.performance import append_snapshot, capture, latest_snapshot, provider_performance
from ocpf_post.providers.base import ProviderUnavailable


class FakeProvider:
    def __init__(self, account_id: str, account_error: Exception | None = None) -> None:
        self._account_id = account_id
        self._account_error = account_error

    def account(self):
        if self._account_error is not None:
            raise self._account_error
        return SimpleNamespace(account_id=self._account_id, display=self._account_id)


class PerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_state = os.environ.get("OCPF_POST_STATE_DIR")
        os.environ["OCPF_POST_STATE_DIR"] = self.tmp.name

    def tearDown(self) -> None:
        if self.old_state is None:
            os.environ.pop("OCPF_POST_STATE_DIR", None)
        else:
            os.environ["OCPF_POST_STATE_DIR"] = self.old_state
        self.tmp.cleanup()

    def test_append_and_latest_are_append_only(self) -> None:
        append_snapshot({"campaign": "OCPF-003", "provider": "x", "captured_at": "a", "metrics": {"likes": 1}})
        append_snapshot({"campaign": "OCPF-003", "provider": "x", "captured_at": "b", "metrics": {"likes": 2}})
        value = latest_snapshot("OCPF-003", "x")
        self.assertEqual(value["captured_at"], "b")
        self.assertEqual(value["metrics"]["likes"], 2)

    def test_capture_preserves_project_source_and_account_alias(self) -> None:
        receipt = {
            "status": "published_verified",
            "campaign": "OCPF-003",
            "provider": "x",
            "account_id": "1480506376447315969",
            "post_id": "2096918050280718633",
            "url": "https://x.com/JohnWOE15/status/2096918050280718633",
        }
        provider = FakeProvider("1480506376447315969")
        with patch("ocpf_post.performance.latest_receipt", return_value=receipt), patch(
            "ocpf_post.performance.get_extended_provider", return_value=provider
        ), patch(
            "ocpf_post.performance._provider_performance",
            return_value={"metrics": {"likes": 7}, "availability": {"status": "available"}},
        ):
            value = capture("OCPF-003", "x")
        self.assertEqual(value["project"], "oneclickpostfactory")
        self.assertEqual(value["source_id"], "OC-01")
        self.assertEqual(value["account_alias"], "x-founder")
        self.assertEqual(value["metrics"]["likes"], 7)

    def test_thread_capture_marks_root_only_metric_scope(self) -> None:
        receipt = {
            "status": "published_verified",
            "campaign": "OCPF-003",
            "provider": "x",
            "account_id": "1480506376447315969",
            "post_id": "2096918050280718633",
            "url": "https://x.com/JohnWOE15/status/2096918050280718633",
            "publication_type": "thread",
            "part_count": 3,
            "publication_part_ids": ["2096918050280718633", "2", "3"],
        }
        provider = FakeProvider("1480506376447315969")
        with patch("ocpf_post.performance.latest_receipt", return_value=receipt), patch(
            "ocpf_post.performance.get_extended_provider", return_value=provider
        ), patch(
            "ocpf_post.performance._provider_performance",
            return_value={"metrics": {"likes": 7}, "availability": {"status": "available"}},
        ):
            value = capture("OCPF-003", "x")
        self.assertEqual(value["publication_type"], "thread")
        self.assertEqual(value["part_count"], 3)
        self.assertEqual(value["metric_scope"], "root_post_only")

    def test_capture_rejects_account_drift(self) -> None:
        receipt = {
            "status": "published_verified",
            "campaign": "OCPF-003",
            "provider": "x",
            "account_id": "1480506376447315969",
            "post_id": "2096918050280718633",
        }
        provider = FakeProvider("wrong-account")
        with patch("ocpf_post.performance.latest_receipt", return_value=receipt), patch(
            "ocpf_post.performance.get_extended_provider", return_value=provider
        ):
            with self.assertRaisesRegex(ValueError, "identity drift"):
                capture("OCPF-003", "x")

    def test_capture_reports_transient_identity_unavailability_as_value_error(self) -> None:
        receipt = {
            "status": "published_verified",
            "campaign": "OCPF-003",
            "provider": "x",
            "account_id": "1480506376447315969",
            "post_id": "2096918050280718633",
        }
        provider = FakeProvider(
            "1480506376447315969",
            account_error=ProviderUnavailable("temporary DNS lookup failure"),
        )
        with patch("ocpf_post.performance.latest_receipt", return_value=receipt), patch(
            "ocpf_post.performance.get_extended_provider", return_value=provider
        ):
            with self.assertRaisesRegex(ValueError, "temporarily unavailable"):
                capture("OCPF-003", "x")

    def test_linkedin_unavailable_is_not_zero(self) -> None:
        value = provider_performance("linkedin", "urn:li:share:test")
        self.assertEqual(value["metrics"], {})
        self.assertEqual(value["availability"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()