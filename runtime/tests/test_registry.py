from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ocpf_post.campaigns import campaign_project, destination_binding, destination_binding_error
from ocpf_post.registry import (
    RegistryError,
    infer_project,
    resolve_account,
    resolve_default_account,
    resolve_provider_default_identity,
)


class RegistryTests(unittest.TestCase):
    def test_existing_ocpf_campaigns_resolve_to_same_accounts(self) -> None:
        expected = {
            "x": "1480506376447315969",
            "threads": "25914281681582868",
            "linkedin": "urn:li:person:UBgFeo6HdJ",
        }
        for campaign in ("OCPF-001", "OCPF-002", "OCPF-003", "OCPF-009", "OCPF-010"):
            self.assertEqual(campaign_project(campaign), "oneclickpostfactory")
            for provider, account_id in expected.items():
                binding = destination_binding(campaign, provider)
                self.assertIsNotNone(binding)
                self.assertEqual(binding["account_id"], account_id)

    def test_global_provider_defaults_are_the_founder_identities(self) -> None:
        expected = {
            "x": "1480506376447315969",
            "threads": "25914281681582868",
            "linkedin": "urn:li:person:UBgFeo6HdJ",
        }
        for provider, account_id in expected.items():
            with self.subTest(provider=provider):
                resolved = resolve_provider_default_identity(provider)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved["account_id"], account_id)
                self.assertIn("agentproof", resolved["projects"])

    def test_agentproof_uses_founder_defaults_for_all_declared_providers(self) -> None:
        expected = {
            "x": ("x-founder", "1480506376447315969"),
            "threads": ("threads-founder", "25914281681582868"),
            "linkedin": ("linkedin-founder", "urn:li:person:UBgFeo6HdJ"),
        }
        for provider, (alias, account_id) in expected.items():
            with self.subTest(provider=provider):
                binding = destination_binding("AGENTPROOF-001", provider)
                self.assertIsNotNone(binding)
                self.assertEqual(binding["alias"], alias)
                self.assertEqual(binding["account_id"], account_id)

    def test_project_default_account_is_explicit_and_provider_scoped(self) -> None:
        binding = resolve_default_account("oneclickpostfactory", "x")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["alias"], "x-founder")
        self.assertEqual(binding["account_id"], "1480506376447315969")

        proof_state = resolve_default_account("proof-and-state", "x")
        self.assertIsNotNone(proof_state)
        self.assertEqual(proof_state["alias"], "x-founder")
        self.assertEqual(proof_state["account_id"], "1480506376447315969")
        self.assertIsNone(resolve_default_account("proof-and-state", "threads"))

    def test_campaign_uses_project_default_when_provider_is_declared(self) -> None:
        fake_registry = {
            "schema_version": 1,
            "projects": {
                "alpha": {
                    "campaign_prefixes": ["ALPHA-"],
                    "default_accounts": {"x": "x-founder"},
                    "accounts": {
                        "x-founder": {"provider": "x", "account_id": "111"},
                        "x-brand": {"provider": "x", "account_id": "222"},
                    },
                }
            },
        }
        manifest = {"campaign": "ALPHA-001", "project": "alpha", "providers": ["x"]}
        with patch("ocpf_post.registry.load_registry", return_value=fake_registry), patch(
            "ocpf_post.campaigns.builtin_manifest", return_value=manifest
        ):
            binding = destination_binding("ALPHA-001", "x")
            self.assertIsNotNone(binding)
            self.assertEqual(binding["alias"], "x-founder")
            self.assertEqual(binding["account_id"], "111")

    def test_campaign_specific_account_overrides_project_default(self) -> None:
        fake_registry = {
            "schema_version": 1,
            "projects": {
                "alpha": {
                    "campaign_prefixes": ["ALPHA-"],
                    "default_accounts": {"x": "x-founder"},
                    "accounts": {
                        "x-founder": {"provider": "x", "account_id": "111"},
                        "x-brand": {"provider": "x", "account_id": "222"},
                    },
                }
            },
        }
        manifest = {
            "campaign": "ALPHA-002",
            "project": "alpha",
            "providers": ["x"],
            "destinations": {"x": "x-brand"},
        }
        with patch("ocpf_post.registry.load_registry", return_value=fake_registry), patch(
            "ocpf_post.campaigns.builtin_manifest", return_value=manifest
        ):
            binding = destination_binding("ALPHA-002", "x")
            self.assertIsNotNone(binding)
            self.assertEqual(binding["alias"], "x-brand")
            self.assertEqual(binding["account_id"], "222")

    def test_default_does_not_authorise_an_undeclared_provider(self) -> None:
        fake_registry = {
            "schema_version": 1,
            "projects": {
                "alpha": {
                    "campaign_prefixes": ["ALPHA-"],
                    "default_accounts": {"x": "x-founder"},
                    "accounts": {"x-founder": {"provider": "x", "account_id": "111"}},
                }
            },
        }
        manifest = {"campaign": "ALPHA-003", "project": "alpha", "providers": ["linkedin"]}
        with patch("ocpf_post.registry.load_registry", return_value=fake_registry), patch(
            "ocpf_post.campaigns.builtin_manifest", return_value=manifest
        ):
            self.assertIsNone(destination_binding("ALPHA-003", "x"))

    def test_proof_and_state_is_a_distinct_project_with_real_campaign_binding(self) -> None:
        self.assertEqual(infer_project("PAS-001"), "proof-and-state")
        self.assertEqual(campaign_project("PAS-001"), "proof-and-state")
        binding = destination_binding("PAS-001", "linkedin")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["alias"], "linkedin-founder")
        self.assertEqual(binding["account_id"], "urn:li:person:UBgFeo6HdJ")

    def test_real_next_project_campaigns_use_the_founder_x_default(self) -> None:
        expected_projects = {
            "PAS-002": "proof-and-state",
            "DONESTATE-001": "donestate",
            "OPSTRUTH-001": "opstruth",
            "PB-001": "parcelbasis",
        }
        for campaign, project_id in expected_projects.items():
            self.assertEqual(campaign_project(campaign), project_id)
            binding = destination_binding(campaign, "x")
            self.assertIsNotNone(binding)
            self.assertEqual(binding["alias"], "x-founder")
            self.assertEqual(binding["account_id"], "1480506376447315969")

    def test_same_external_account_can_be_explicitly_governed_by_several_projects(self) -> None:
        project_ids = ("oneclickpostfactory", "proof-and-state", "donestate", "opstruth", "parcelbasis")
        bindings = [resolve_account(project_id, "x-founder", expected_provider="x") for project_id in project_ids]
        self.assertEqual({binding["account_id"] for binding in bindings}, {"1480506376447315969"})
        self.assertEqual(len({binding.get("role") for binding in bindings}), len(project_ids))

    def test_proof_and_state_does_not_inherit_unregistered_threads_account(self) -> None:
        with self.assertRaises(RegistryError):
            resolve_account("proof-and-state", "threads-founder", expected_provider="threads")

    def test_provider_alias_cannot_cross_provider(self) -> None:
        with self.assertRaises(RegistryError):
            resolve_account("oneclickpostfactory", "x-founder", expected_provider="threads")

    def test_wrong_account_still_fails_closed(self) -> None:
        account = SimpleNamespace(account_id="wrong", display="wrong")
        message = destination_binding_error("OCPF-010", "x", account)
        self.assertIsNotNone(message)
        self.assertIn("destination mismatch", message.lower())

    def test_campaign_prefix_infers_project(self) -> None:
        self.assertEqual(infer_project("OCPF-999"), "oneclickpostfactory")

    def test_ambiguous_prefix_is_rejected(self) -> None:
        fake = {
            "schema_version": 1,
            "projects": {
                "a": {"campaign_prefixes": ["OCPF-"], "accounts": {}},
                "b": {"campaign_prefixes": ["OCPF-"], "accounts": {}},
            },
        }
        with patch("ocpf_post.registry.load_registry", return_value=fake):
            with self.assertRaises(RegistryError):
                infer_project("OCPF-123")

    def test_multiple_accounts_for_same_provider_resolve_by_alias(self) -> None:
        fake = {
            "schema_version": 1,
            "projects": {
                "alpha": {
                    "campaign_prefixes": ["ALPHA-"],
                    "accounts": {
                        "x-founder": {"provider": "x", "account_id": "111"},
                        "x-brand": {"provider": "x", "account_id": "222"},
                    },
                }
            },
        }
        with patch("ocpf_post.registry.load_registry", return_value=fake):
            self.assertEqual(resolve_account("alpha", "x-founder", expected_provider="x")["account_id"], "111")
            self.assertEqual(resolve_account("alpha", "x-brand", expected_provider="x")["account_id"], "222")

    def test_account_alias_is_project_scoped(self) -> None:
        fake = {
            "schema_version": 1,
            "projects": {
                "alpha": {
                    "campaign_prefixes": ["ALPHA-"],
                    "accounts": {"x-main": {"provider": "x", "account_id": "111"}},
                },
                "beta": {
                    "campaign_prefixes": ["BETA-"],
                    "accounts": {"x-main": {"provider": "x", "account_id": "999"}},
                },
            },
        }
        with patch("ocpf_post.registry.load_registry", return_value=fake):
            self.assertEqual(resolve_account("alpha", "x-main")["account_id"], "111")
            self.assertEqual(resolve_account("beta", "x-main")["account_id"], "999")


if __name__ == "__main__":
    unittest.main()
