from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ocpf_post.campaigns import destination_binding, destination_binding_error
from ocpf_post.providers import for_account


class AccountBindingTests(unittest.TestCase):
    def test_x_destination_is_bound(self) -> None:
        binding = destination_binding("OCPF-001", "x")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["account_id"], "1480506376447315969")

    def test_linkedin_destination_is_bound(self) -> None:
        binding = destination_binding("OCPF-001", "linkedin")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["account_id"], "urn:li:person:UBgFeo6HdJ")

    def test_threads_destination_is_bound(self) -> None:
        binding = destination_binding("OCPF-001", "threads")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["account_id"], "25914281681582868")
        account = SimpleNamespace(
            account_id="25914281681582868",
            display="@tailwaggingwebdesigns",
        )
        self.assertIsNone(destination_binding_error("OCPF-001", "threads", account))

    def test_linkedin_matching_account_is_accepted(self) -> None:
        account = SimpleNamespace(
            account_id="urn:li:person:UBgFeo6HdJ",
            display="Ayobami J Haastrup",
        )
        self.assertIsNone(destination_binding_error("OCPF-001", "linkedin", account))

    def test_founder_identity_uses_provider_default_credential_route(self) -> None:
        provider = SimpleNamespace()
        with patch("ocpf_post.account_profiles.profile", return_value=None), \
             patch("ocpf_post.registry.resolve_provider_default_identity",
                   return_value={"provider": "threads", "account_id": "25914281681582868",
                                 "projects": ["agentproof"], "aliases": ["threads-founder"]}), \
             patch("ocpf_post.providers.get_provider", return_value=provider) as factory:
            routed = for_account("threads", "25914281681582868")
        self.assertIs(routed._provider, provider)
        factory.assert_called_once_with("threads")

    def test_unknown_identity_never_falls_back_to_founder_credential(self) -> None:
        with patch("ocpf_post.account_profiles.profile", return_value=None), \
             patch("ocpf_post.registry.resolve_provider_default_identity",
                   return_value={"provider": "threads", "account_id": "25914281681582868",
                                 "projects": ["agentproof"], "aliases": ["threads-founder"]}), \
             patch("ocpf_post.providers.get_provider") as factory:
            with self.assertRaisesRegex(ValueError, "neither the canonical provider default"):
                for_account("threads", "999999999")
        factory.assert_not_called()

    def test_wrong_bound_account_is_rejected(self) -> None:
        account = SimpleNamespace(account_id="wrong", display="@wrong")
        message = destination_binding_error("OCPF-001", "x", account)
        self.assertIsNotNone(message)
        self.assertIn("destination mismatch", (message or "").lower())


if __name__ == "__main__":
    unittest.main()
