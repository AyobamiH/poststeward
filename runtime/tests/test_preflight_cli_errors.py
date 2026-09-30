from __future__ import annotations

import io
import unittest
from argparse import Namespace
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

from ocpf_post.cli import cmd_x_status
from ocpf_post.provider_runner import cmd_status as cmd_provider_status
from ocpf_post.providers.base import ProviderUnavailable


class PreflightCliErrorTests(unittest.TestCase):
    @staticmethod
    def unavailable_provider():
        provider = Mock()
        provider.account.side_effect = ProviderUnavailable("temporary DNS lookup failure")
        return provider

    def test_x_status_reports_unavailability_without_traceback(self) -> None:
        stderr = io.StringIO()
        with patch("ocpf_post.cli._provider", return_value=self.unavailable_provider()), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                cmd_x_status(Namespace())
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("temporary DNS lookup failure", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_threads_status_reports_unavailability_without_traceback(self) -> None:
        stderr = io.StringIO()
        args = Namespace(provider="threads")
        with patch("ocpf_post.provider_runner._provider", return_value=self.unavailable_provider()), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                cmd_provider_status(args)
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("temporary DNS lookup failure", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
