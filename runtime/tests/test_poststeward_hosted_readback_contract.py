"""Adversarial hosted identity/capability readback: never trust a stale binding alone."""
import unittest
from unittest.mock import patch

from ocpf_post.poststeward_onboarding import _cloud_readiness


def binding():
    return {
        "provider": "threads",
        "alias": "approved-threads",
        "version": 4,
        "identity": {"id": "123"},
        "active": True,
        "capabilities": {"oauth": True, "refresh": True, "readback": True},
    }


def response(*, provider="threads", oauth=True, binding_version=4, schema=1, with_capabilities=True):
    result = {
        "schemaVersion": schema,
        "provider": provider,
        "account": {
            "identity": {"id": "123"},
            "alias": "approved-threads",
            "binding": binding_version,
        },
    }
    if with_capabilities:
        result["capabilities"] = {"oauth": oauth, "refresh": True, "readback": True}
    return result


class HostedIdentityReadbackContractTests(unittest.TestCase):
    def projection(self, payload):
        with patch("ocpf_post.poststeward_onboarding.relay", return_value=payload) as relay:
            result = _cloud_readiness(binding())
        self.assertEqual(relay.call_count, 1)
        self.assertEqual(relay.call_args.args[0]["action"], "account")
        return result

    def test_exact_live_identity_and_capabilities_remain_usable(self):
        value = self.projection(response())
        self.assertEqual(value["identity_match"], "match")
        self.assertEqual(value["blocking_reasons"], [])
        self.assertTrue(value["ready_for_write_configuration"])

    def test_stale_oauth_flag_cannot_override_relay_capability_revocation(self):
        value = self.projection(response(oauth=False))
        self.assertFalse(value["ready_for_write_configuration"])
        self.assertIn("provider.oauth.hosted_connection_required", value["blocking_reasons"])

    def test_missing_current_cloud_capabilities_are_unverified(self):
        value = self.projection(response(with_capabilities=False))
        self.assertFalse(value["ready_for_write_configuration"])
        self.assertIn("provider.oauth.hosted_connection_required", value["blocking_reasons"])

    def test_matching_numeric_id_on_wrong_provider_cannot_prove_identity(self):
        value = self.projection(response(provider="x"))
        self.assertFalse(value["ready_for_write_configuration"])
        self.assertIn("provider.binding.provider_mismatch", value["blocking_reasons"])

    def test_unversioned_or_wrong_schema_is_never_accepted(self):
        value = self.projection(response(schema=99))
        self.assertFalse(value["ready_for_write_configuration"])
        self.assertIn("provider.readback.schema_invalid", value["blocking_reasons"])

    def test_non_object_relay_response_is_blocked_without_exception(self):
        value = self.projection(None)
        self.assertFalse(value["ready_for_write_configuration"])
        self.assertIn("provider.readback.schema_invalid", value["blocking_reasons"])
        self.assertIn("provider.identity.mismatch", value["blocking_reasons"])

    def test_boolean_binding_version_cannot_match_numeric_one(self):
        row = binding()
        row["version"] = 1
        with patch("ocpf_post.poststeward_onboarding.relay", return_value=response(binding_version=True)):
            value = _cloud_readiness(row)
        self.assertFalse(value["ready_for_write_configuration"])
        self.assertIn("provider.binding.mismatch", value["blocking_reasons"])


if __name__ == "__main__":
    unittest.main()
