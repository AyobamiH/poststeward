from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpf_post.generated_supply_recovery import (
    _successor_manifest,
    preview,
    recover,
)

NOW = datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)
CAMPAIGN = "OCPF-GEN-2026092400-01-671B1C0"
PROVIDER = "linkedin"
ACCOUNT = "urn:li:person:UBgFeo6HdJ"
TEXT = "A reviewed frozen LinkedIn payload that should be recovered exactly as written."
SHA = hashlib.sha256(TEXT.encode()).hexdigest()


def manifest():
    return {
        "campaign": CAMPAIGN,
        "project": "oneclickpostfactory",
        "title": "Reviewed legacy generated copy",
        "status": "COPY-READY",
        "providers": ["linkedin"],
        "runtime_generated": True,
        "payload_frozen": True,
        "payload_sha256": {"linkedin": SHA},
        "source": {
            "type": "evidence_grounded_generation",
            "source_id": "oneclickpostfactory-GENERATIVE-test",
            "repository": "AyobamiH/oneclickpostfactory",
            "path": "README.md",
            "source_sha": "a" * 40,
            "observed_at": "2026-09-24T00:00:00Z",
        },
        "destinations": {"linkedin": "linkedin-founder"},
        "allocation": {
            "enabled": True,
            "lane": "evergreen",
            "priority": 72,
            "prepared_at": "2026-09-24T00:00:00Z",
            "expires_at": "2026-09-30T00:00:00Z",
        },
        "schedules": {},
        "receipts": {},
    }


class GeneratedSupplyRecoveryTests(unittest.TestCase):
    def review_file(self, text=TEXT):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump([{"campaign": CAMPAIGN, "text": text}], tmp)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink, missing_ok=True)
        return tmp.name

    def preview_context(self, *, receipts=None):
        return (
            patch("ocpf_post.generated_supply_recovery.builtin_manifest", return_value=manifest()),
            patch("ocpf_post.generated_supply_recovery.builtin_text", return_value=TEXT),
            patch("ocpf_post.generated_supply_recovery.destination_binding", return_value={"account_id": ACCOUNT}),
            patch("ocpf_post.generated_supply_recovery.schedule_records", return_value=[]),
            patch("ocpf_post.generated_supply_recovery.iter_receipts", return_value=receipts or []),
            patch("ocpf_post.generated_supply_recovery.occupied_copies", return_value={}),
            patch("ocpf_post.generated_supply_recovery.campaign_copy_key", return_value=("linkedin", ACCOUNT, SHA)),
            patch("ocpf_post.portfolio._eligible_manifest", return_value=(True, "eligible")),
            patch("ocpf_post.vault_sync.guard", return_value=None),
            patch("ocpf_post.account_profiles.unavailable", return_value=None),
        )

    def test_preview_identifies_reviewed_legacy_route_as_recoverable(self):
        path = self.review_file()
        contexts = self.preview_context()
        for ctx in contexts:
            ctx.start()
            self.addCleanup(ctx.stop)
        result = preview(path, provider=PROVIDER, now=NOW)
        self.assertEqual(result["recoverable_count"], 1)
        self.assertEqual(result["states"], {"recoverable_pending_fresh_admission": 1})
        row = result["recoverable"][0]
        self.assertEqual(row["campaign"], CAMPAIGN)
        self.assertEqual(row["account_id"], ACCOUNT)
        self.assertEqual(row["text_sha256"], SHA)
        self.assertTrue(row["successor_campaign"].startswith("OCPF-REC-LI-"))
        self.assertTrue(result["read_only"])

    def test_preview_never_recovers_terminal_effect(self):
        path = self.review_file()
        receipt = {
            "campaign": CAMPAIGN,
            "provider": PROVIDER,
            "account_id": ACCOUNT,
            "status": "published_unverified",
            "post_id": "urn:li:share:123",
        }
        contexts = self.preview_context(receipts=[receipt])
        for ctx in contexts:
            ctx.start()
            self.addCleanup(ctx.stop)
        result = preview(path, provider=PROVIDER, now=NOW)
        self.assertEqual(result["recoverable_count"], 0)
        self.assertEqual(result["states"], {"terminal_effect_do_not_recover": 1})

    def test_preview_requires_exact_reviewed_text_match(self):
        path = self.review_file("Edited review text")
        contexts = self.preview_context()
        for ctx in contexts:
            ctx.start()
            self.addCleanup(ctx.stop)
        result = preview(path, provider=PROVIDER, now=NOW)
        self.assertEqual(result["recoverable_count"], 0)
        self.assertEqual(result["states"], {"reviewed_text_does_not_match_frozen_payload": 1})

    def test_successor_manifest_preserves_lineage_and_does_not_claim_historical_rewrite(self):
        gate = {
            "admitted": True,
            "scope": f"linkedin:{ACCOUNT}",
            "reasons": ["within_destination_budget"],
            "protected": False,
            "learning_protected": False,
        }
        successor = "OCPF-REC-LI-ABCDEF123456"
        value = _successor_manifest(
            manifest(),
            source_campaign=CAMPAIGN,
            successor_campaign=successor,
            provider=PROVIDER,
            account_id=ACCOUNT,
            text_sha256=SHA,
            gate=gate,
            now=NOW,
            review_file_sha256="b" * 64,
            review_sha256="c" * 64,
        )
        self.assertEqual(value["campaign"], successor)
        self.assertEqual(value["providers"], [PROVIDER])
        self.assertEqual(value["payload_sha256"], {PROVIDER: SHA})
        self.assertEqual(value["admission"]["gate"], "scoped_admission")
        self.assertEqual(value["admission"]["providers"][PROVIDER]["account_id"], ACCOUNT)
        self.assertEqual(value["recovery"]["source_campaign"], CAMPAIGN)
        self.assertFalse(value["recovery"]["historical_manifest_rewritten"])
        self.assertFalse(value["recovery"]["provider_consequence_attempted"])
        self.assertEqual(value["schedules"], {})
        self.assertEqual(value["receipts"], {})
        self.assertEqual(value["allocation"]["expires_at"], "2026-09-30T00:00:00Z")

    def test_apply_creates_successor_inventory_only(self):
        path = self.review_file()
        successor = "OCPF-REC-LI-ABCDEF123456"
        preview_value = {
            "schema_version": 1,
            "read_only": True,
            "provider": PROVIDER,
            "review_file_sha256": "b" * 64,
            "reviewed_generated_campaigns": 1,
            "recoverable_count": 1,
            "states": {"recoverable_pending_fresh_admission": 1},
            "review_sha256": "c" * 64,
            "recoverable": [{
                "campaign": CAMPAIGN,
                "provider": PROVIDER,
                "project": "oneclickpostfactory",
                "account_id": ACCOUNT,
                "text_sha256": SHA,
                "source_sha": "a" * 40,
                "source_id": "oneclickpostfactory-GENERATIVE-test",
                "expires_at": "2026-09-30T00:00:00Z",
                "successor_campaign": successor,
            }],
            "details": [],
        }

        class AllowBudget:
            def __init__(self, now, apply):
                self.now = now
                self.apply = apply

            def admit(self, project, provider, account, *, expires_at=None):
                return {
                    "admitted": True,
                    "scope": f"{provider}:{account}",
                    "reasons": ["within_destination_budget"],
                    "protected": False,
                    "learning_protected": False,
                }

        with patch("ocpf_post.generated_supply_recovery.preview", return_value=preview_value), \
             patch("ocpf_post.generated_supply_recovery.local_store.locked", return_value=nullcontext()), \
             patch("ocpf_post.runtime_sources.source_lock", return_value=nullcontext()), \
             patch("ocpf_post.generated_supply_recovery.LearningBudget", AllowBudget), \
             patch("ocpf_post.generated_supply_recovery.campaign_ids", return_value=[]), \
             patch("ocpf_post.generated_supply_recovery.builtin_manifest", return_value=manifest()), \
             patch("ocpf_post.generated_supply_recovery.builtin_text", return_value=TEXT), \
             patch("ocpf_post.replenisher._write_runtime_campaign") as writer:
            result = recover(
                path,
                provider=PROVIDER,
                expected_sha256="c" * 64,
                now=NOW,
            )

        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["blocked_count"], 0)
        writer.assert_called_once()
        args = writer.call_args.args
        self.assertEqual(args[0], successor)
        self.assertEqual(args[2], {PROVIDER: TEXT})
        self.assertEqual(args[1]["recovery"]["source_campaign"], CAMPAIGN)
        self.assertFalse(args[1]["recovery"]["provider_consequence_attempted"])


if __name__ == "__main__":
    unittest.main()
