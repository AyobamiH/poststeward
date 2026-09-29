from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.provider_import import import_threads
from ocpf_post.providers.base import ProviderRejected, ProviderUnavailable
from ocpf_post.providers.http import TransportError
from ocpf_post.providers.threads import ThreadsProvider


class ProviderPreflightAvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cfg = os.environ.get("OCPF_POST_CONFIG_DIR")
        os.environ["OCPF_POST_CONFIG_DIR"] = self.tmp.name
        import_threads("tok")

    def tearDown(self) -> None:
        if self.old_cfg is None:
            os.environ.pop("OCPF_POST_CONFIG_DIR", None)
        else:
            os.environ["OCPF_POST_CONFIG_DIR"] = self.old_cfg
        self.tmp.cleanup()

    def test_threads_account_dns_failure_is_provider_unavailable(self) -> None:
        with patch(
            "ocpf_post.providers.threads.request_json",
            side_effect=TransportError("<urlopen error [Errno -3] Temporary failure in name resolution>"),
        ):
            with self.assertRaises(ProviderUnavailable) as error:
                ThreadsProvider().account()
        self.assertIn("account verification network error", str(error.exception))

    def test_threads_account_transient_http_is_provider_unavailable(self) -> None:
        for status in (408, 425, 429, 500, 503):
            with self.subTest(status=status), patch(
                "ocpf_post.providers.threads.request_json",
                return_value=(status, {}, {"error": {"message": "temporary"}}),
            ):
                with self.assertRaises(ProviderUnavailable):
                    ThreadsProvider().account()

    def test_threads_account_credential_rejection_is_not_auto_retryable(self) -> None:
        with patch(
            "ocpf_post.providers.threads.request_json",
            return_value=(401, {}, {"error": {"message": "invalid token"}}),
        ):
            with self.assertRaises(ProviderRejected) as error:
                ThreadsProvider().account()
        self.assertEqual(error.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
