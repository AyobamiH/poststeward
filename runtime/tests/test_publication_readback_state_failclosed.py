"""Corrupt durable provider readback evidence must fail closed, never be replaced.

These regression cases use an isolated state root and make no network calls.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpf_post import local_store, publication_reconcile
from ocpf_post.state import state_dir


class ReadbackDurabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        scope = patch.dict(os.environ, {"OCPF_POST_STATE_DIR": self.temp.name})
        scope.start()
        self.addCleanup(scope.stop)
        self.path = state_dir() / "publication-readbacks.json"

    def test_empty_state_is_initialised_without_external_provider(self) -> None:
        self.assertFalse(publication_reconcile.pending())
        publication_reconcile.reconcile(apply=True)

    def test_malformed_evidence_refuses_automatic_readback_without_erasing_file(self) -> None:
        cases = (
            {"schema_version": 1},
            {"schema_version": 1, "observations": []},
            {"schema_version": 1, "observations": {"broken": None}},
            {"schema_version": 1, "observations": {}, "account_backoff": []},
            {"schema_version": 1, "observations": {}, "account_backoff": {"x:acct": {}}},
            {"schema_version": 1, "observations": {}, "account_backoff": {"x:acct": {"retry_at": "not-a-time"}}},
            {"schema_version": 1, "observations": {}, "account_backoff": {"not_scoped": {"retry_at": "2026-11-13T12:00:00Z"}}},
        )
        for malformed in cases:
            with self.subTest(malformed=malformed):
                local_store.write(self.path, malformed)
                original = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, "durable provider-readback state"):
                    publication_reconcile.pending()
                with self.assertRaisesRegex(ValueError, "durable provider-readback state"):
                    publication_reconcile.reconcile(apply=True)
                self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_json_fails_closed_without_erasing_original_evidence(self) -> None:
        self.path.write_text('{"observations":', encoding="utf-8")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "durable provider-readback state"):
            publication_reconcile.reconcile(apply=True)
        self.assertEqual(self.path.read_bytes(), original)

    def test_null_durable_file_is_not_treated_as_a_fresh_installation(self) -> None:
        self.path.write_text("null", encoding="utf-8")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "durable provider-readback state"):
            publication_reconcile.reconcile(apply=True)
        self.assertEqual(self.path.read_bytes(), original)

    def test_existing_valid_account_backoff_remains_accepted(self) -> None:
        retry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        local_store.write(self.path, {
            "schema_version": 1, "observations": {},
            "account_backoff": {"threads:account-1": {"retry_at": retry, "http_status": 429}},
        })
        self.assertFalse(publication_reconcile.pending())
        publication_reconcile.reconcile(apply=True)
        self.assertEqual(local_store.read(self.path)["account_backoff"]["threads:account-1"]["http_status"], 429)

    def test_repaired_evidence_can_resume_normal_read_only_reconciliation(self) -> None:
        local_store.write(self.path, {"schema_version": 1, "observations": [], "account_backoff": {}})
        with self.assertRaises(ValueError):
            publication_reconcile.reconcile(apply=True)
        local_store.write(self.path, {"schema_version": 1, "observations": {}, "account_backoff": {}})
        self.assertFalse(publication_reconcile.pending())
        publication_reconcile.reconcile(apply=True)


if __name__ == "__main__":
    unittest.main()
