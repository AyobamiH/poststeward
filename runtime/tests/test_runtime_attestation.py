from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ocpf_post import __version__
from ocpf_post.runtime_attestation import attest

UTC = timezone.utc


class RuntimeAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "runtime"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Attestation Test"], cwd=self.root, check=True)
        # Tests must not inherit an operator's global SSH/GPG commit-signing policy.
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=self.root, check=True)
        (self.root / "app.txt").write_text("campaign copy must never appear in attestation\n")
        subprocess.run(["git", "add", "app.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=self.root, check=True)
        self.sha1 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        self.old = {name: os.environ.get(name) for name in (
            "OCPF_POST_RUNTIME_ROOT", "OCPF_POST_SOURCE_ROOT", "OCPF_POST_STATE_DIR",
            "OCPF_POST_CONFIG_DIR", "X_CLIENT_SECRET",
        )}
        os.environ["OCPF_POST_RUNTIME_ROOT"] = str(self.root)
        os.environ["OCPF_POST_SOURCE_ROOT"] = str(self.root)
        os.environ["OCPF_POST_STATE_DIR"] = str(Path(self.tmp.name) / "state")
        os.environ["OCPF_POST_CONFIG_DIR"] = str(Path(self.tmp.name) / "config")
        os.environ["X_CLIENT_SECRET"] = "credential-sentinel-must-not-leak"

    def tearDown(self) -> None:
        for name, value in self.old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def test_exact_revision_versions_digest_and_non_authorising_boundary(self) -> None:
        digest = "a" * 64
        with patch("ocpf_post.runtime_attestation._repository_revision", return_value=(self.sha1, "remote_observed")), \
             patch("ocpf_post.runtime_attestation.policy_digest", return_value=(digest, "observed")):
            result = attest(now=datetime(2026, 9, 12, 10, 30, tzinfo=UTC))
        self.assertEqual(result["git_commit_sha"], self.sha1)
        self.assertEqual(result["repository_main_sha"], self.sha1)
        self.assertTrue(result["local_matches_repository_main"])
        self.assertFalse(result["repository_has_newer_revision"])
        self.assertEqual(result["cli_version"], __version__)
        self.assertEqual(result["allocator_engine"], "portfolio-queue-v12")
        self.assertEqual(result["policy_config_sha256"], digest)
        self.assertTrue(result["checkout_clean"])
        self.assertFalse(result["publishing_authority"])
        self.assertEqual(result["consequence"], "READ_ONLY")

        encoded = json.dumps(result)
        self.assertNotIn("credential-sentinel-must-not-leak", encoded)
        self.assertNotIn("campaign copy must never appear", encoded)
        self.assertNotIn(socket.gethostname(), encoded)

    def test_dirty_tree_is_visible_without_exposing_dirty_content(self) -> None:
        secret = "untracked-sensitive-content"
        (self.root / "scratch.txt").write_text(secret)
        with patch("ocpf_post.runtime_attestation._repository_revision", return_value=(self.sha1, "remote_observed")), \
             patch("ocpf_post.runtime_attestation.policy_digest", return_value=("b" * 64, "observed")):
            result = attest(now=datetime(2026, 9, 12, 10, 31, tzinfo=UTC))
        self.assertFalse(result["checkout_clean"])
        self.assertNotIn(secret, json.dumps(result))

    def test_repository_newer_revision_is_visible_without_changing_local_checkout(self) -> None:
        (self.root / "app.txt").write_text("new revision\n")
        subprocess.run(["git", "add", "app.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "new revision"], cwd=self.root, check=True)
        sha2 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        subprocess.run(["git", "reset", "--hard", "-q", self.sha1], cwd=self.root, check=True)
        with patch("ocpf_post.runtime_attestation._repository_revision", return_value=(sha2, "remote_observed")), \
             patch("ocpf_post.runtime_attestation.policy_digest", return_value=("c" * 64, "observed")):
            result = attest(now=datetime(2026, 9, 12, 10, 32, tzinfo=UTC))
        self.assertEqual(result["git_commit_sha"], self.sha1)
        self.assertFalse(result["local_matches_repository_main"])
        self.assertTrue(result["repository_has_newer_revision"])
        self.assertFalse(result["publishing_authority"])

    def test_unavailable_policy_attestation_never_becomes_authority(self) -> None:
        with patch("ocpf_post.runtime_attestation._repository_revision", return_value=(None, "repository_unavailable")), \
             patch("ocpf_post.runtime_attestation.policy_digest", return_value=(None, "unavailable")):
            result = attest(now=datetime(2026, 9, 12, 10, 33, tzinfo=UTC))
        self.assertIsNone(result["policy_config_sha256"])
        self.assertEqual(result["policy_digest_status"], "unavailable")
        self.assertIsNone(result["local_matches_repository_main"])
        self.assertIsNone(result["repository_has_newer_revision"])
        self.assertFalse(result["publishing_authority"])


if __name__ == "__main__":
    unittest.main()
