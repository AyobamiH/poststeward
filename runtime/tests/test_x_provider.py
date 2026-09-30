from __future__ import annotations

import unittest
from unittest.mock import patch

from ocpf_post.providers.base import ProviderRejected, ProviderUnavailable
from ocpf_post.providers.x import DEFAULT_SCOPES, TransportError, XProvider, _make_pkce, _safe_payload


class XProviderTests(unittest.TestCase):
    def test_pkce_uses_s256_material(self) -> None:
        verifier, challenge = _make_pkce()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertGreaterEqual(len(challenge), 43)
        self.assertNotEqual(verifier, challenge)

    def test_required_scopes_are_minimal_for_text_publish_and_refresh(self) -> None:
        self.assertEqual(DEFAULT_SCOPES, ("tweet.read", "tweet.write", "users.read", "offline.access"))

    def test_safe_payload_scrubs_tokens(self) -> None:
        rendered = _safe_payload({"access_token": "secret", "refresh_token": "secret2", "error": "bad"})
        self.assertNotIn("secret", rendered)
        self.assertIn("bad", rendered)

    def test_reply_uses_native_in_reply_to_tweet_id(self) -> None:
        provider = XProvider(client_id="client")
        with patch.object(provider, "_bearer", return_value=(201, {"data": {"id": "456"}})) as bearer:
            result = provider.reply("continuation", "123")
        self.assertEqual(result["id"], "456")
        self.assertEqual(
            bearer.call_args.kwargs["body"],
            {"text": "continuation", "reply": {"in_reply_to_tweet_id": "123"}},
        )

    def test_account_transport_failure_is_typed_preflight_unavailability(self) -> None:
        provider = XProvider(client_id="client")
        with patch.object(provider, "_bearer", side_effect=TransportError("temporary DNS lookup failure")):
            with self.assertRaises(ProviderUnavailable) as error:
                provider.account()
        self.assertIn("account verification network error", str(error.exception))

    def test_account_transient_http_is_typed_preflight_unavailability(self) -> None:
        provider = XProvider(client_id="client")
        for status in (408, 425, 429, 500, 503):
            with self.subTest(status=status), patch.object(
                provider,
                "_bearer",
                return_value=(status, {"title": "temporary"}),
            ):
                with self.assertRaises(ProviderUnavailable):
                    provider.account()

    def test_account_credential_rejection_is_not_retryable(self) -> None:
        provider = XProvider(client_id="client")
        with patch.object(provider, "_bearer", return_value=(401, {"title": "unauthorised"})):
            with self.assertRaises(ProviderRejected) as error:
                provider.account()
        self.assertEqual(error.exception.status, 401)

    def test_refresh_failure_propagating_through_account_is_not_reclassified(self) -> None:
        provider = XProvider(client_id="client")
        refresh_failure = ProviderRejected(503, "X refresh failed")
        with patch.object(provider, "_bearer", side_effect=refresh_failure):
            with self.assertRaises(ProviderRejected) as error:
                provider.account()
        self.assertIs(error.exception, refresh_failure)


if __name__ == "__main__":
    unittest.main()
