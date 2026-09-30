from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ocpf_post.campaigns import builtin_text, normalize_campaign_id
from ocpf_post.state import append_receipt, latest_receipt, terminal_effect_receipt


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OCPF_POST_CONFIG_DIR"] = str(Path(self.tmp.name) / "config")
        os.environ["OCPF_POST_STATE_DIR"] = str(Path(self.tmp.name) / "state")

    def tearDown(self) -> None:
        os.environ.pop("OCPF_POST_CONFIG_DIR", None)
        os.environ.pop("OCPF_POST_STATE_DIR", None)
        self.tmp.cleanup()

    def test_builtin_campaign(self) -> None:
        text = builtin_text("OCPF-001", "x")
        self.assertIsNotNone(text)
        self.assertIn("Reddit answer", text or "")

    def test_campaign_normalization(self) -> None:
        self.assertEqual(normalize_campaign_id(" ocpf-001 "), "OCPF-001")
        with self.assertRaises(ValueError):
            normalize_campaign_id("bad campaign!")

    def test_terminal_receipt_is_account_specific(self) -> None:
        base = {
            "campaign": "OCPF-001",
            "provider": "x",
            "status": "published_verified",
            "text_sha256": "abc",
            "recorded_at": "2026-09-06T00:00:00Z",
            "username": "a",
            "post_id": "1",
            "url": "https://x.com/a/status/1",
            "readback_verified": True,
            "detail": None,
        }
        append_receipt({**base, "account_id": "A"})
        self.assertIsNotNone(terminal_effect_receipt("OCPF-001", "x", "A"))
        self.assertIsNone(terminal_effect_receipt("OCPF-001", "x", "B"))

    def test_latest_receipt_wins(self) -> None:
        first = {"campaign": "OCPF-001", "provider": "x", "account_id": "A", "status": "published_unverified"}
        second = {"campaign": "OCPF-001", "provider": "x", "account_id": "A", "status": "published_verified"}
        append_receipt(first)
        append_receipt(second)
        self.assertEqual(latest_receipt("OCPF-001", "x", "A")["status"], "published_verified")


if __name__ == "__main__":
    unittest.main()
