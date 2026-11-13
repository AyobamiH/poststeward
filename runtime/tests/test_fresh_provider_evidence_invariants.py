"""Read-only fail-closed provider evidence validation for Fresh and migration verification."""
from __future__ import annotations

import unittest

from ocpf_post.setup_migration import validate_provider_readiness


EXPECTED = [{"provider": "x", "account_id": "acct-42"}]


def evidence(**overrides):
    row = {
        "provider": "x",
        "expected_identity": "acct-42",
        "observed_identity": "acct-42",
        "identity_match": "match",
        "write_scope_state": "granted",
        "ready_for_write_configuration": True,
        "blocking_reasons": [],
    }
    row.update(overrides)
    return row


class FreshProviderEvidenceInvariants(unittest.TestCase):
    def validation(self, *rows):
        return validate_provider_readiness(EXPECTED, list(rows))

    def assert_blocked(self, *rows):
        result = self.validation(*rows)
        self.assertEqual(result["status"], "blocked", result)
        self.assertTrue(result["blockers"], result)

    def test_exact_complete_observation_is_ready(self):
        result = self.validation(evidence())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["expected_count"], 1)
        self.assertEqual(result["observed_count"], 1)

    def test_observed_identity_must_match_even_if_summary_claims_match(self):
        self.assert_blocked(evidence(observed_identity="other-account"))

    def test_claimed_ready_with_nonempty_blockers_must_not_be_accepted(self):
        self.assert_blocked(evidence(blocking_reasons=["provider.oauth.revoked"]))

    def test_unready_with_missing_reasons_must_not_be_accepted(self):
        self.assert_blocked(evidence(ready_for_write_configuration=False, blocking_reasons=[]))

    def test_duplicate_evidence_cannot_shadow_prior_unverified_observation(self):
        self.assert_blocked(
            evidence(observed_identity="other-account", identity_match="mismatch",
                     ready_for_write_configuration=False,
                     blocking_reasons=["provider.identity.mismatch"]),
            evidence(),
        )

    def test_unexpected_provider_is_not_silently_ignored(self):
        self.assert_blocked(evidence(), evidence(provider="threads",
                                                  expected_identity="other",
                                                  observed_identity="other"))

    def test_explicit_missing_write_scope_cannot_claim_ready(self):
        self.assert_blocked(evidence(write_scope_state="missing"))

    def test_legacy_migration_evidence_without_scope_detail_remains_compatible(self):
        row = evidence()
        row.pop("write_scope_state")
        self.assertEqual(self.validation(row)["status"], "ready")

    def test_malformed_duplicate_expected_accounts_cannot_collapse_into_one(self):
        result = validate_provider_readiness(EXPECTED + EXPECTED, [evidence()])
        self.assertEqual(result["status"], "blocked", result)


if __name__ == "__main__":
    unittest.main()
