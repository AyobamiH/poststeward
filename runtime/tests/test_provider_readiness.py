from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from ocpf_post.provider_readiness import observe_linkedin, observe_threads, observe_x
from ocpf_post.providers.linkedin import LinkedInProvider
from ocpf_post.providers.threads import ThreadsProvider
from ocpf_post.providers.x import XProvider
from ocpf_post.state import write_private_json

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schemas" / "setup-recovery" / "v1" / "provider-readiness.schema.json").read_text(
        encoding="utf-8"
    )
)


class ProviderReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_cfg = os.environ.get("OCPF_POST_CONFIG_DIR")
        os.environ["OCPF_POST_CONFIG_DIR"] = str(self.root / "global-config")

    def tearDown(self) -> None:
        if self.old_cfg is None:
            os.environ.pop("OCPF_POST_CONFIG_DIR", None)
        else:
            os.environ["OCPF_POST_CONFIG_DIR"] = self.old_cfg
        self.tmp.cleanup()

    def assert_schema(self, value: dict) -> None:
        Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(value)
        rendered = json.dumps(value, sort_keys=True)
        self.assertNotIn("secret-access", rendered)
        self.assertNotIn("secret-refresh", rendered)
        self.assertNotIn("secret-client", rendered)

    def credential_dir(self, provider: str) -> Path:
        path = self.root / provider
        path.mkdir(parents=True, exist_ok=True)
        return path

    def test_x_projection_is_no_refresh_and_bounded(self) -> None:
        directory = self.credential_dir("x")
        write_private_json(
            directory / "token.json",
            {
                "access_token": "secret-access",
                "refresh_token": "secret-refresh",
                "scope": "tweet.read tweet.write users.read offline.access",
                "expires_at": int(time.time()) + 3600,
            },
        )
        write_private_json(directory / "settings.json", {"client_id": "client"})
        provider = XProvider(credential_dir=directory)

        def response(url: str, **_kwargs):
            if "/users/me" in url:
                return 200, {"data": {"id": "42", "username": "postonce"}}
            if "/usage/tweets" in url:
                return 200, {
                    "data": {
                        "project_cap": "10000",
                        "project_usage": "15",
                        "cap_reset_day": 1,
                    }
                }
            if "/users/42/tweets" in url:
                return 200, {
                    "data": [{"id": "99", "text": "hello", "created_at": "2026-09-25T10:00:00Z"}],
                    "meta": {},
                }
            raise AssertionError(url)

        with patch.object(provider, "refresh", side_effect=AssertionError("readiness must not refresh")), patch(
            "ocpf_post.providers.x._request_json", side_effect=response
        ) as request:
            value = observe_x(
                provider,
                expected_identity="42",
                forensic_window=("2026-09-25T09:00:00Z", "2026-09-25T11:00:00Z"),
            )

        self.assertEqual(value["identity_match"], "match")
        self.assertEqual(value["write_scope_state"], "granted")
        self.assertEqual(value["readback_scope_state"], "granted")
        self.assertEqual(value["quota_observation"]["project_usage"], "15")
        self.assertIsNone(value["quota_observation"]["billing_credit_balance"])
        self.assertEqual(value["forensic_recent_listing"]["posts_observed"], 1)
        self.assertEqual(value["recovery_fencing_strength"], "assisted")
        self.assertNotIn("provider.identity.mismatch", value["blocking_reasons"])
        self.assertTrue(value["ready_for_write_configuration"])
        self.assertEqual(request.call_count, 3)
        self.assert_schema(value)

    def test_x_immutable_identity_mismatch_blocks(self) -> None:
        directory = self.credential_dir("x")
        write_private_json(
            directory / "token.json",
            {
                "access_token": "secret-access",
                "scope": "tweet.read tweet.write users.read offline.access",
            },
        )
        write_private_json(directory / "settings.json", {"client_id": "client"})
        provider = XProvider(credential_dir=directory)
        responses = [
            (200, {"data": {"id": "999", "username": "same-handle"}}),
            (200, {"data": {"project_cap": "100", "project_usage": "1"}}),
        ]
        with patch("ocpf_post.providers.x._request_json", side_effect=responses):
            value = observe_x(provider, expected_identity="42")
        self.assertEqual(value["identity_match"], "mismatch")
        self.assertIn("provider.identity.mismatch", value["blocking_reasons"])
        self.assertFalse(value["ready_for_write_configuration"])

    def test_threads_projection_uses_existing_readonly_primitives_and_quota(self) -> None:
        directory = self.credential_dir("threads")
        write_private_json(
            directory / "token.json",
            {
                "access_token": "secret-access",
                "scope": "threads_basic threads_content_publish",
                "expires_at": int(time.time()) + 30 * 24 * 60 * 60,
            },
        )
        provider = ThreadsProvider(credential_dir=directory)

        def response(url: str, **_kwargs):
            if url.endswith("/me"):
                return 200, {}, {"id": "77", "username": "factory"}
            if "threads_publishing_limit" in url:
                return 200, {}, {
                    "data": [{
                        "quota_usage": 3,
                        "config": {"quota_total": 250, "quota_duration": 86400},
                        "reply_quota_usage": 0,
                        "reply_config": {"quota_total": 1000, "quota_duration": 86400},
                    }]
                }
            if url.endswith("/me/threads"):
                return 200, {}, {
                    "data": [{"id": "88", "text": "hello"}],
                    "paging": {"cursors": {}},
                }
            raise AssertionError(url)

        with patch.object(provider, "refresh", side_effect=AssertionError("readiness must not refresh")), patch(
            "ocpf_post.providers.threads.request_json", side_effect=response
        ):
            value = observe_threads(
                provider,
                expected_identity="77",
                forensic_window=(1789980000, 1789983600),
            )

        self.assertEqual(value["identity_match"], "match")
        self.assertEqual(value["write_scope_state"], "granted")
        self.assertEqual(value["quota_observation"]["quota_total"], 250)
        self.assertEqual(value["forensic_recent_listing"]["posts_observed"], 1)
        self.assertEqual(value["recovery_fencing_strength"], "manual_only")
        self.assertTrue(value["ready_for_write_configuration"])
        self.assert_schema(value)

    def test_threads_unrecorded_write_scope_fails_closed(self) -> None:
        directory = self.credential_dir("threads")
        write_private_json(directory / "token.json", {"access_token": "secret-access"})
        provider = ThreadsProvider(credential_dir=directory)
        responses = [
            (200, {}, {"id": "77", "username": "factory"}),
            (200, {}, {"data": [{"quota_usage": 0, "config": {"quota_total": 250}}]}),
        ]
        with patch("ocpf_post.providers.threads.request_json", side_effect=responses):
            value = observe_threads(provider, expected_identity="77")
        self.assertIn("provider.write_scope.unproven", value["blocking_reasons"])
        self.assertFalse(value["ready_for_write_configuration"])

    def test_linkedin_member_requires_live_userinfo_identity_and_introspection_scope(self) -> None:
        directory = self.credential_dir("linkedin")
        write_private_json(
            directory / "token.json",
            {
                "access_token": "secret-access",
                "refresh_token": "secret-refresh",
                "client_id": "client",
                "client_secret": "secret-client",
                "scope": "openid profile w_member_social",
                "expires_at": int(time.time()) + 3600,
            },
        )
        provider = LinkedInProvider(credential_dir=directory)

        def response(url: str, **_kwargs):
            if "introspectToken" in url:
                return 200, {}, {
                    "active": True,
                    "status": "active",
                    "client_id": "client",
                    "scope": "openid,profile,w_member_social",
                    "expires_at": int(time.time()) + 3600,
                    "auth_type": "3L",
                }
            if url.endswith("/v2/userinfo"):
                return 200, {}, {"sub": "abc123", "name": "John"}
            raise AssertionError(url)

        with patch.object(provider, "refresh", side_effect=AssertionError("readiness must not refresh")), patch(
            "ocpf_post.providers.linkedin.request_json", side_effect=response
        ):
            value = observe_linkedin(
                provider,
                expected_identity="urn:li:person:abc123",
            )

        self.assertEqual(value["observed_identity"], "urn:li:person:abc123")
        self.assertEqual(value["identity_match"], "match")
        self.assertEqual(value["write_scope_state"], "granted")
        self.assertEqual(value["readback_scope_state"], "missing")
        self.assertIn("provider.readback.optional_missing", value["optional_gaps"])
        self.assertIn("provider.api_version.attention", value["optional_gaps"])
        self.assertTrue(value["ready_for_write_configuration"])
        self.assert_schema(value)

    def test_linkedin_stored_urn_cannot_replace_live_userinfo_for_migration(self) -> None:
        directory = self.credential_dir("linkedin")
        write_private_json(
            directory / "token.json",
            {
                "access_token": "secret-access",
                "client_id": "client",
                "client_secret": "secret-client",
                "person_urn": "urn:li:person:abc123",
                "scope": "w_member_social",
            },
        )
        provider = LinkedInProvider(credential_dir=directory)
        responses = [
            (200, {}, {"active": True, "scope": "w_member_social"}),
            (403, {}, {"message": "missing openid"}),
        ]
        with patch("ocpf_post.providers.linkedin.request_json", side_effect=responses):
            value = observe_linkedin(provider, expected_identity="urn:li:person:abc123")
        self.assertIn("provider.identity.unverified", value["blocking_reasons"])
        self.assertFalse(value["ready_for_write_configuration"])

    def test_linkedin_org_readback_becomes_recovery_blocker_when_required(self) -> None:
        directory = self.credential_dir("linkedin")
        write_private_json(
            directory / "token.json",
            {
                "access_token": "secret-access",
                "client_id": "client",
                "client_secret": "secret-client",
                "scope": "openid profile w_organization_social",
            },
        )
        provider = LinkedInProvider(
            credential_dir=directory,
            actor_urn="urn:li:organization:146607525",
        )
        responses = [
            (200, {}, {
                "active": True,
                "scope": "openid,profile,w_organization_social",
            }),
            (200, {}, {"sub": "abc123", "name": "John"}),
        ]
        with patch("ocpf_post.providers.linkedin.request_json", side_effect=responses):
            value = observe_linkedin(
                provider,
                expected_identity="urn:li:organization:146607525",
                require_recovery_readback=True,
            )
        self.assertEqual(value["identity_match"], "match")
        self.assertEqual(value["write_scope_state"], "granted")
        self.assertIn("provider.readback.recovery_required", value["blocking_reasons"])
        self.assertFalse(value["ready_for_write_configuration"])
        self.assert_schema(value)


if __name__ == "__main__":
    unittest.main()
