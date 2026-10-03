from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import urllib.error

from ocpf_post import workers_ai
from ocpf_post.state import write_private_json


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode()


class Opener:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if self.error:
            raise self.error
        return Response(self.payload)


class WorkersAITransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "OCPF_POST_CONFIG_DIR": self.tmp.name,
            "CLOUDFLARE_ACCOUNT_ID": "0123456789abcdef0123456789abcdef",
            "CLOUDFLARE_WORKERS_AI_TOKEN": "workers-ai-model-token-1234567890",
            "OCPF_POST_WORKERS_AI_MODEL": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_configuration_requires_dedicated_workers_ai_token(self):
        with patch.dict(os.environ, {
            "CLOUDFLARE_WORKERS_AI_TOKEN": "",
            "CLOUDFLARE_API_TOKEN": "broad-deployment-token-must-not-be-used",
        }, clear=False):
            cfg = workers_ai.configuration()
        self.assertFalse(cfg["available"])
        self.assertEqual(cfg["api_token"], "")

    def test_saved_private_configuration_is_used_without_environment_token(self):
        write_private_json(
            workers_ai.config_dir() / "workers-ai.json",
            {
                "schema_version": 1,
                "account_id": "abcdef0123456789abcdef0123456789",
                "api_token": "saved-workers-ai-token-1234567890",
                "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "fallback_enabled": True,
                "producer_mode": "work-workers-ai",
            },
        )
        with patch.dict(os.environ, {
            "CLOUDFLARE_ACCOUNT_ID": "",
            "CLOUDFLARE_WORKERS_AI_TOKEN": "",
            "OCPF_POST_WORKERS_AI_MODEL": "",
        }, clear=False):
            cfg = workers_ai.configuration()
        self.assertTrue(cfg["available"])
        self.assertEqual(cfg["account_id"], "abcdef0123456789abcdef0123456789")
        self.assertEqual(cfg["api_token"], "saved-workers-ai-token-1234567890")

        policy = workers_ai.editorial_policy()
        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["mode"], "work-workers-ai")
        self.assertEqual(policy["daily_limit"], workers_ai.DEFAULT_DAILY_LIMIT)
        value = workers_ai.status()
        self.assertTrue(value["fallback_enabled"])
        self.assertEqual(value["producer_mode"], "work-workers-ai")
        self.assertEqual(value["daily_limit"], workers_ai.DEFAULT_DAILY_LIMIT)

    def test_workers_ai_daily_limit_is_independent_and_bounded(self):
        write_private_json(
            workers_ai.config_dir() / "workers-ai.json",
            {
                "schema_version": 1,
                "account_id": "abcdef0123456789abcdef0123456789",
                "api_token": "saved-workers-ai-token-1234567890",
                "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "fallback_enabled": True,
                "producer_mode": "work-workers-ai",
                "daily_limit": 24,
            },
        )
        with patch.dict(os.environ, {
            "OCPF_POST_GENERATIVE_DAILY_LIMIT": "3",
            "OCPF_POST_WORKERS_AI_DAILY_LIMIT": "",
        }, clear=False):
            # Empty explicit value fails safe to the dedicated Workers AI default;
            # it never inherits the legacy paid-OpenAI authoring limit.
            policy = workers_ai.editorial_policy()
        self.assertEqual(policy["daily_limit"], workers_ai.DEFAULT_DAILY_LIMIT)

        with patch.dict(os.environ, {
            "OCPF_POST_WORKERS_AI_DAILY_LIMIT": "40",
        }, clear=False):
            self.assertEqual(workers_ai.editorial_policy()["daily_limit"], 40)

        with patch.dict(os.environ, {
            "OCPF_POST_WORKERS_AI_DAILY_LIMIT": "9999",
        }, clear=False):
            self.assertEqual(
                workers_ai.editorial_policy()["daily_limit"],
                workers_ai.MAX_DAILY_LIMIT,
            )

    def test_explicit_environment_policy_overrides_persistent_fallback(self):
        write_private_json(
            workers_ai.config_dir() / "workers-ai.json",
            {
                "schema_version": 1,
                "account_id": "abcdef0123456789abcdef0123456789",
                "api_token": "saved-workers-ai-token-1234567890",
                "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "fallback_enabled": True,
                "producer_mode": "work-workers-ai",
            },
        )
        with patch.dict(os.environ, {
            "OCPF_POST_GENERATIVE_SUPPLY_ENABLED": "1",
            "OCPF_POST_GENERATIVE_SUPPLY_MODE": "work",
        }, clear=False):
            policy = workers_ai.editorial_policy()
        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["mode"], "work")

    def test_json_request_uses_cloudflare_openai_compatibility_and_reject_busy(self):
        payload = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"answer": "ok"})},
            }]
        }
        opener = Opener(payload=payload)
        value, model = workers_ai.chat_json(
            messages=[{"role": "user", "content": "bounded"}],
            schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
            max_tokens=100,
            temperature=0,
            opener=opener,
        )
        self.assertEqual(value, {"answer": "ok"})
        self.assertEqual(model, "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
        self.assertIn(
            "/accounts/0123456789abcdef0123456789abcdef/ai/v1/chat/completions",
            opener.request.full_url,
        )
        request = json.loads(opener.request.data)
        self.assertEqual(request["model"], model)
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertEqual(request["options"], {"rejectIfBusy": True})
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer workers-ai-model-token-1234567890")

    def test_rate_limit_is_fail_closed_and_never_retried_here(self):
        error = urllib.error.HTTPError(
            "https://api.cloudflare.com/test", 429, "busy", {}, None
        )
        opener = Opener(error=error)
        with self.assertRaisesRegex(ValueError, "workers_ai_busy_or_rate_limited"):
            workers_ai.chat_json(
                messages=[{"role": "user", "content": "bounded"}],
                schema={"type": "object"},
                max_tokens=100,
                temperature=0,
                opener=opener,
            )

    def test_probe_status_requires_fresh_evidence_for_current_model(self):
        from ocpf_post.state import state_dir

        now = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
        write_private_json(
            state_dir() / "workers-ai-probe.json",
            {
                "schema_version": 1,
                "status": "accepted",
                "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "observed_at": (now - timedelta(hours=1)).isoformat(),
            },
        )
        value = workers_ai.probe_status(now=now)
        self.assertEqual(value["status"], "accepted")
        self.assertTrue(value["fresh"])
        self.assertTrue(value["model_match"])

        with patch.dict(os.environ, {
            "OCPF_POST_WORKERS_AI_MODEL": "@cf/zai-org/glm-5.3",
        }, clear=False):
            changed = workers_ai.probe_status(now=now)
        self.assertEqual(changed["status"], "unobserved")
        self.assertFalse(changed["model_match"])

    def test_stale_probe_never_counts_as_live_acceptance(self):
        from ocpf_post.state import state_dir

        now = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
        write_private_json(
            state_dir() / "workers-ai-probe.json",
            {
                "schema_version": 1,
                "status": "accepted",
                "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "observed_at": (now - timedelta(hours=25)).isoformat(),
            },
        )
        value = workers_ai.probe_status(now=now)
        self.assertEqual(value["status"], "unobserved")
        self.assertFalse(value["fresh"])

    def test_status_never_returns_token(self):
        value = workers_ai.status()
        self.assertEqual(value["status"], "configured")
        self.assertTrue(value["credential_present"])
        self.assertNotIn("api_token", value)
        self.assertNotIn("workers-ai-model-token", json.dumps(value))


if __name__ == "__main__":
    unittest.main()

