from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import credential_keyring, state_registry
from ocpf_post.cli_catalog import COMMANDS
from ocpf_post.cli_surface import command_surface
from ocpf_post.completion_gen import bash_completion, fish_completion, zsh_completion
from ocpf_post.ledger import LedgerIntegrityError
from ocpf_post.state import (
    append_receipt,
    config_dir,
    delete_private_json,
    iter_receipts,
    provider_token_file,
    read_json,
    write_private_json,
)


class FakeKeyringModule:
    class Backend:
        priority = 1

    def __init__(self):
        self.values = {}
        self.backend = self.Backend()

    def get_keyring(self):
        return self.backend

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[(service, username)] = value

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


class FinalPlatformHardeningTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {
            "OCPF_POST_CONFIG_DIR": str(self.root / "config"),
            "OCPF_POST_STATE_DIR": str(self.root / "state"),
        })
        env.start()
        self.addCleanup(env.stop)

    def _receipt(self, index):
        return {
            "campaign": f"TEST-{index:04d}",
            "provider": "x",
            "account_id": "123",
            "status": "published_verified",
            "recorded_at": "2026-09-18T00:00:00Z",
            "post_id": str(index + 1),
        }

    def test_critical_ledger_segmentation_preserves_order_and_future_appends(self):
        path = self.root / "state" / "publish-receipts.jsonl"
        path.parent.mkdir(parents=True)
        original = [self._receipt(index) for index in range(1005)]
        path.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in original),
            encoding="utf-8",
        )
        preview = state_registry.segment("receipts", keep_lines=1000)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["moved_lines"], 5)
        applied = state_registry.segment(
            "receipts",
            keep_lines=1000,
            apply=True,
            expected_sha256=preview["review_sha256"],
        )
        self.assertEqual(applied["status"], "segmented")
        self.assertEqual(list(iter_receipts()), original)
        segment_dir = path.with_name(path.name + ".segments")
        self.assertEqual([item.name for item in segment_dir.glob("*.jsonl")], ["00000001.jsonl"])
        append_receipt(self._receipt(1005))
        rows = list(iter_receipts())
        self.assertEqual(len(rows), 1006)
        self.assertEqual(rows[-1]["campaign"], "TEST-1005")
        verified = state_registry.verify()
        self.assertEqual(verified["critical_ledger_status"], "observed")
        self.assertEqual(verified["ledger_segmentation"]["publish-receipts.jsonl"]["segment_count"], 1)

    def test_interrupted_segmentation_fails_closed_then_rolls_back_pending_copy(self):
        path = self.root / "state" / "publish-receipts.jsonl"
        path.parent.mkdir(parents=True)
        row = self._receipt(1)
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        root = path.with_name(path.name + ".segments")
        root.mkdir()
        pending = root / "00000001.jsonl.pending"
        pending.write_text(json.dumps(row) + "\n", encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (root / "SEGMENTATION.json").write_text(json.dumps({
            "schema_version": 1,
            "old_live_sha256": digest,
            "new_live_sha256": hashlib.sha256(b"").hexdigest(),
            "pending_sha256": hashlib.sha256(pending.read_bytes()).hexdigest(),
            "pending_name": pending.name,
            "final_name": "00000001.jsonl",
            "review_sha256": "a" * 64,
        }), encoding="utf-8")
        with self.assertRaises(LedgerIntegrityError):
            list(iter_receipts())
        preview = state_registry.segment_recover("receipts")
        self.assertEqual(preview["status"], "recovery_preview")
        self.assertEqual(preview["action"], "rollback_pending_segment")
        applied = state_registry.segment_recover("receipts", apply=True)
        self.assertEqual(applied["status"], "recovered")
        self.assertEqual(list(iter_receipts()), [row])
        self.assertFalse((root / "SEGMENTATION.json").exists())
        self.assertFalse(pending.exists())

    def test_inventory_never_decodes_credential_shaped_config_json(self):
        path = self.root / "config" / "threads-token.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not-valid-json", encoding="utf-8")
        result = state_registry.inventory()
        row = next(item for item in result["files"] if item["scope"] == "config" and item["path"] == "threads-token.json")
        self.assertEqual(row["format"], "credential_opaque")
        self.assertEqual(row["status"], "opaque")
        self.assertEqual(result["invalid_count"], 0)

    def test_keyring_migration_refuses_permissive_provider_file(self):
        if os.name != "posix":
            self.skipTest("POSIX mode check")
        fake = FakeKeyringModule()
        path = provider_token_file("x")
        path.parent.mkdir(parents=True)
        path.write_text('{"access_token":"secret"}', encoding="utf-8")
        path.chmod(0o644)
        with patch.object(credential_keyring, "_module", return_value=fake):
            result = credential_keyring.migrate_provider("x", path, config_dir())
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "provider_token_file_not_private")
        self.assertTrue(path.exists())
        self.assertEqual(fake.values, {})

    def test_provider_keyring_migration_is_reversible_and_secret_free(self):
        fake = FakeKeyringModule()
        token = {
            "access_token": "secret-access-token",
            "refresh_token": "secret-refresh-token",
            "scope": "write",
        }
        path = provider_token_file("x")
        with patch.object(credential_keyring, "_module", return_value=fake):
            write_private_json(path, token)
            preview = credential_keyring.migrate_provider("x", path, config_dir())
            self.assertEqual(preview["status"], "preview")
            migrated = credential_keyring.migrate_provider(
                "x", path, config_dir(), apply=True, expected_sha256=preview["review_sha256"]
            )
            self.assertEqual(migrated["status"], "migrated")
            self.assertFalse(path.exists())
            self.assertEqual(read_json(path), token)
            status = credential_keyring.status(config_dir())
            self.assertTrue(status["providers"][0]["managed"])
            self.assertTrue(status["providers"][0]["secret_present"])
            self.assertNotIn("secret-access-token", json.dumps(status))
            updated = {**token, "access_token": "rotated-token"}
            write_private_json(path, updated)
            self.assertEqual(read_json(path), updated)
            restore_preview = credential_keyring.restore_provider("x", path, config_dir())
            restored = credential_keyring.restore_provider(
                "x", path, config_dir(), apply=True,
                expected_sha256=restore_preview["review_sha256"],
            )
            self.assertEqual(restored["status"], "restored_to_private_file")
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(read_json(path), updated)
            self.assertFalse(credential_keyring.is_managed("x", config_dir()))

    def test_keyring_aware_delete_removes_authority_and_index(self):
        fake = FakeKeyringModule()
        path = provider_token_file("threads")
        token = {"access_token": "thread-secret"}
        with patch.object(credential_keyring, "_module", return_value=fake):
            write_private_json(path, token)
            preview = credential_keyring.migrate_provider("threads", path, config_dir())
            credential_keyring.migrate_provider(
                "threads", path, config_dir(), apply=True,
                expected_sha256=preview["review_sha256"],
            )
            self.assertTrue(credential_keyring.is_managed("threads", config_dir()))
            delete_private_json(path)
            self.assertFalse(credential_keyring.is_managed("threads", config_dir()))
            self.assertIsNone(fake.get_password(
                credential_keyring.SERVICE,
                credential_keyring.username("threads"),
            ))
            self.assertEqual(read_json(path), {})

    def test_completions_are_command_option_aware_from_parser_metadata(self):
        surface = command_surface()
        bash = bash_completion(COMMANDS, surface)
        zsh = zsh_completion(COMMANDS, surface)
        fish = fish_completion(COMMANDS, surface)
        for rendered in (bash, zsh, fish):
            self.assertIn("receipts schedules performance", rendered)
            self.assertIn("x threads linkedin", rendered)
        self.assertIn("--ledger", bash)
        self.assertIn("--expected-sha256", bash)
        self.assertIn("--ledger", zsh)
        self.assertIn("-l ledger", fish)
        self.assertIn("-l expected-sha256", fish)


if __name__ == "__main__":
    unittest.main()
