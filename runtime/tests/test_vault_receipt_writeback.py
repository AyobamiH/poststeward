from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from ocpf_post.google_vault import status_change_requests
from ocpf_post.vault_sync import digest, receipt_updates, reconcile_receipts


def approved_document(entry):
    body = {"schema_version": 1, "entries": [entry]}
    return (
        "POST-ONCE APPROVED ENTRIES BEGIN\n"
        + json.dumps(body, indent=2)
        + "\nPOST-ONCE APPROVED ENTRIES END\n"
    )


def approved_entry():
    value = {
        "campaign": "EXAMPLE-VAULT-01",
        "provider": "x",
        "title": "Example",
        "text": "Exact approved copy",
        "allocation": {
            "lane": "evergreen",
            "priority": 70,
            "prepared_at": "2026-09-20T00:00:00Z",
            "expires_at": "2026-09-27T00:00:00Z",
        },
        "status": "APPROVED",
    }
    value["approval_sha256"] = digest(value)
    return value


class VaultReceiptWritebackTests(unittest.TestCase):
    def test_status_change_requests_target_only_designated_entry(self):
        document = {
            "revision_id": "rev-1",
            "paragraphs": [
                {"text": "Narrative says APPROVED\n", "start_index": 1, "end_index": 25, "tab_id": "t.0"},
                {"text": "POST-ONCE X APPROVED ENTRIES BEGIN\n", "start_index": 30, "end_index": 70, "tab_id": "t.0"},
                {"text": '      "campaign": "EXAMPLE-VAULT-01",\n', "start_index": 80, "end_index": 125, "tab_id": "t.0"},
                {"text": '      "provider": "x",\n', "start_index": 130, "end_index": 155, "tab_id": "t.0"},
                {"text": '      "status": "APPROVED",\n', "start_index": 160, "end_index": 190, "tab_id": "t.0"},
                {"text": "POST-ONCE X APPROVED ENTRIES END\n", "start_index": 200, "end_index": 238, "tab_id": "t.0"},
            ],
        }
        requests, updated, already = status_change_requests(document, [{
            "base_campaign": "EXAMPLE-VAULT-01",
            "provider": "x",
        }])
        self.assertEqual(already, [])
        self.assertEqual(len(updated), 1)
        self.assertEqual(len(requests), 2)
        start = 160 + '      "status": "APPROVED",\n'.index("APPROVED")
        self.assertEqual(requests[0]["deleteContentRange"]["range"], {
            "startIndex": start,
            "endIndex": start + len("APPROVED"),
            "tabId": "t.0",
        })
        self.assertEqual(requests[1]["insertText"]["text"], "PUBLISHED")

    def test_exact_published_receipt_projects_one_vault_status_update(self):
        approved = approved_entry()
        revision = digest({k: v for k, v in approved.items() if k != "approval_sha256"})
        manifest = {
            "campaign": "EXAMPLE-VAULT-01-V" + revision[:12].upper() + "-X",
            "providers": ["x"],
            "payload_sha256": {"x": "a" * 64},
            "vault": {
                "id": "example-vault",
                "key": "EXAMPLE-VAULT-01:x",
                "base_campaign": "EXAMPLE-VAULT-01",
                "revision": revision,
            },
        }
        receipt = {
            "campaign": manifest["campaign"],
            "provider": "x",
            "account_id": "123",
            "text_sha256": "a" * 64,
            "status": "published_verified",
            "schedule_id": "sch-1",
            "post_id": "999",
        }
        policy = {
            "id": "example-vault",
            "document_id": "doc-1",
            "destinations": {"x": "x-brand"},
        }
        document = {"text": approved_document(approved)}
        with patch("ocpf_post.vault_sync.destination_binding", return_value={"account_id": "123"}):
            rows = receipt_updates(
                policy,
                document,
                manifests={manifest["campaign"]: manifest},
                receipts=[receipt],
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["base_campaign"], "EXAMPLE-VAULT-01")
        self.assertEqual(rows[0]["receipt_status"], "published_verified")
        self.assertEqual(rows[0]["post_id"], "999")

    def test_ambiguous_or_mismatched_effect_never_terminalises_document(self):
        approved = approved_entry()
        revision = digest({k: v for k, v in approved.items() if k != "approval_sha256"})
        campaign = "EXAMPLE-VAULT-01-V" + revision[:12].upper() + "-X"
        manifest = {
            "campaign": campaign,
            "providers": ["x"],
            "payload_sha256": {"x": "a" * 64},
            "vault": {
                "id": "example-vault",
                "key": "EXAMPLE-VAULT-01:x",
                "base_campaign": "EXAMPLE-VAULT-01",
                "revision": revision,
            },
        }
        policy = {"id": "example-vault", "document_id": "doc-1", "destinations": {"x": "x-brand"}}
        document = {"text": approved_document(approved)}
        receipts = [
            {
                "campaign": campaign,
                "provider": "x",
                "account_id": "123",
                "text_sha256": "a" * 64,
                "status": "ambiguous_effect",
                "post_id": None,
            },
            {
                "campaign": campaign,
                "provider": "x",
                "account_id": "123",
                "text_sha256": "b" * 64,
                "status": "published_verified",
                "post_id": "wrong-copy",
            },
        ]
        with patch("ocpf_post.vault_sync.destination_binding", return_value={"account_id": "123"}):
            rows = receipt_updates(policy, document, manifests={campaign: manifest}, receipts=receipts)
        self.assertEqual(rows, [])

    def test_writeback_permission_failure_does_not_change_receipt_truth(self):
        candidate = {
            "base_campaign": "EXAMPLE-VAULT-01",
            "provider": "x",
            "campaign": "EXAMPLE-VAULT-01-V1-X",
            "receipt_status": "published_verified",
            "schedule_id": "sch-1",
            "post_id": "999",
        }
        with patch("ocpf_post.vault_sync.receipt_updates", return_value=[candidate]), \
             patch("ocpf_post.vault_sync.credential_capabilities", return_value={
                 "credential_present": True,
                 "read": True,
                 "document_write": False,
                 "writeback_enabled": True,
             }):
            result = reconcile_receipts(
                {"id": "example-vault"},
                {"document_id": "doc-1"},
                apply=True,
                writer=lambda *_: (_ for _ in ()).throw(AssertionError("writer should not run")),
            )
        self.assertEqual(result["status"], "permission_required")
        self.assertEqual(result["candidate_count"], 1)
        self.assertIn("never retries", result["boundary"])


if __name__ == "__main__":
    unittest.main()
