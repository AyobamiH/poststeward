"""Installed customer updates must fetch releases from distribution, not Cloud.

All remote I/O and installer subprocess execution are intercepted. The separate
live acceptance invokes only --dry-run against public release manifests.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from ocpf_post import poststeward_product as product


class InstallerReply:
    def __init__(self, url):
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, size):
        return b"#!/usr/bin/env bash\nset -euo pipefail\n"


class UpdateDistributionOriginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poststeward-update-origin-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "POSTSTEWARD_ORIGIN": "https://app.poststeward.com",
            "POSTSTEWARD_DISTRIBUTION_ORIGIN": "",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.marker = patch.object(product.automation_authority, "read", return_value={"status": "inactive"})
        self.marker.start()
        self.addCleanup(self.marker.stop)
        self.state = patch.object(product, "resolved_paths", return_value={"state": self.root})
        self.state.start()
        self.addCleanup(self.state.stop)
        self.installation = patch("ocpf_post.installed_lifecycle.installation",
                                  return_value=({}, self.root / "installed", self.root / "bin"))
        self.installation.start()
        self.addCleanup(self.installation.stop)

    def run_mock_update(self, *, channel="beta", dry_run=True):
        seen = []
        def fetch(request, timeout):
            seen.append((request.full_url, timeout))
            return InstallerReply(request.full_url)
        with patch.object(product.urllib.request, "urlopen", side_effect=fetch), patch.object(
            product.subprocess, "run", return_value=Mock(returncode=0)
        ) as run:
            result = product.update(channel, dry_run=dry_run)
        return result, seen, run.call_args

    def test_cloud_app_origin_remains_separate_from_public_release_distribution(self):
        value, seen, invocation = self.run_mock_update()
        self.assertEqual(value, 0)
        self.assertEqual(seen, [("https://poststeward.com/install.sh", 30)])
        args, kwargs = invocation
        self.assertEqual(args[0][0], "bash")
        self.assertEqual(args[0][2:6], ["--version", "beta", "--no-onboard", "--prefix"])
        self.assertEqual(kwargs["env"]["POSTSTEWARD_ORIGIN"], "https://app.poststeward.com")
        self.assertEqual(kwargs["env"]["POSTSTEWARD_DISTRIBUTION_ORIGIN"], "https://poststeward.com")
        self.assertIn("--dry-run", args[0])
        self.assertTrue(kwargs.get("check") is False)

    def test_explicit_staging_distribution_is_not_silently_replaced_by_public(self):
        with patch.dict(os.environ, {"POSTSTEWARD_DISTRIBUTION_ORIGIN": "https://staging.example"}):
            value, seen, invocation = self.run_mock_update(channel="stable")
        self.assertEqual(value, 0)
        self.assertEqual(seen[0][0], "https://staging.example/install.sh")
        self.assertEqual(invocation.kwargs["env"]["POSTSTEWARD_DISTRIBUTION_ORIGIN"], "https://staging.example")
        self.assertEqual(invocation.kwargs["env"]["POSTSTEWARD_ORIGIN"], "https://app.poststeward.com")

    def test_untrusted_distribution_origin_is_rejected_before_network(self):
        for invalid in ("http://poststeward.com", "https://user:secret@poststeward.com",
                        "https://poststeward.com/path", "https://poststeward.com/?a=1",
                        "https://poststeward.com/#x", "file:///etc/passwd"):
            with self.subTest(origin=invalid), patch.dict(
                os.environ, {"POSTSTEWARD_DISTRIBUTION_ORIGIN": invalid}
            ), patch.object(product.urllib.request, "urlopen") as fetch:
                with self.assertRaises(product.ProductLifecycleError) as raised:
                    product.update("beta", dry_run=True)
                self.assertEqual(raised.exception.code, "UPDATE_DISTRIBUTION_ORIGIN_INVALID")
                fetch.assert_not_called()

    def test_http_redirect_is_rejected_without_running_an_installer(self):
        with patch.object(product.urllib.request, "urlopen", return_value=InstallerReply(
            "https://different.example/install.sh"
        )), patch.object(product.subprocess, "run") as execute:
            with self.assertRaises(product.ProductLifecycleError) as raised:
                product.update("beta", dry_run=True)
        self.assertEqual(raised.exception.code, "UPDATE_INSTALLER_REDIRECT")
        execute.assert_not_called()

    def test_active_publishing_authority_blocks_before_remote_installer_get(self):
        with patch.object(product.automation_authority, "read", return_value={"status": "active"}), patch.object(
            product.urllib.request, "urlopen"
        ) as fetch:
            with self.assertRaises(product.ProductLifecycleError) as raised:
                product.update("beta", dry_run=True)
        self.assertEqual(raised.exception.code, "UPDATE_ACTIVE_RUNTIME")
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
