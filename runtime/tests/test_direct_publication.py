from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpf_post.direct_publication import DirectPublicationStopped, publish
from ocpf_post.model import AccountIdentity
from ocpf_post.providers.base import ProviderRejected
from ocpf_post.state import iter_receipts


class FakeProvider:
    def __init__(self, *, reject_reply=False, verify=True):
        self.reject_reply = reject_reply
        self.verify = verify
        self.posts = []
        self.replies = []

    def publish(self, text):
        self.posts.append(text)
        return {"id": "100"}

    def reply(self, text, reply_to_id):
        if self.reject_reply:
            raise ProviderRejected(400, "continuation rejected")
        self.replies.append((text, reply_to_id))
        return {"id": str(100 + len(self.replies))}

    def verify_post(self, post_id, expected_text):
        return self.verify

    def post_url(self, account, post_id):
        return f"https://example.invalid/{account.account_id}/{post_id}"


class DirectPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict("os.environ", {"OCPF_POST_STATE_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.account = AccountIdentity(provider="x", account_id="123", username="owner")
        self.clock = lambda: "2026-09-18T22:30:00Z"

    def test_long_x_direct_publish_chains_every_part_and_finishes_one_logical_publication(self):
        provider = FakeProvider()
        text = " ".join(f"approved-{i}" for i in range(100))
        result = publish(
            provider=provider,
            account=self.account,
            campaign="TEST-001",
            provider_name="x",
            text=text,
            now=self.clock,
        )
        self.assertEqual(result["status"], "published_verified")
        self.assertEqual(result["publication"]["publication_type"], "thread")
        self.assertEqual(len(provider.posts), 1)
        self.assertEqual(len(provider.replies), result["publication"]["part_count"] - 1)
        previous = "100"
        for _, reply_to in provider.replies:
            self.assertEqual(reply_to, previous)
            previous = str(int(previous) + 1)
        final = list(iter_receipts())[-1]
        self.assertEqual(final["status"], "published_verified")
        self.assertEqual(final["publication_type"], "thread")
        self.assertEqual(len(final["publication_part_ids"]), final["part_count"])

    def test_mid_thread_rejection_records_terminal_partial_effect(self):
        provider = FakeProvider(reject_reply=True)
        text = " ".join(f"approved-{i}" for i in range(100))
        with self.assertRaises(DirectPublicationStopped) as error:
            publish(
                provider=provider,
                account=self.account,
                campaign="TEST-002",
                provider_name="x",
                text=text,
                now=self.clock,
            )
        self.assertEqual(error.exception.status, "partial_effect")
        final = list(iter_receipts())[-1]
        self.assertEqual(final["status"], "partial_effect")
        self.assertEqual(final["completed_part_count"], 1)
        self.assertEqual(final["failed_part_index"], 2)
        self.assertEqual(final["publication_part_ids"], ["100"])

    def test_threads_long_text_uses_same_reply_chain_contract(self):
        provider = FakeProvider()
        account = AccountIdentity(provider="threads", account_id="456", username="owner")
        text = " ".join(f"thread-{i}" for i in range(180))
        result = publish(
            provider=provider,
            account=account,
            campaign="TEST-003",
            provider_name="threads",
            text=text,
            now=self.clock,
        )
        self.assertEqual(result["publication"]["publication_type"], "thread")
        self.assertGreater(result["publication"]["part_count"], 1)
        self.assertEqual(len(provider.replies), result["publication"]["part_count"] - 1)


if __name__ == "__main__":
    unittest.main()
