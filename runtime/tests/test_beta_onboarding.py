from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from ocpf_post.beta_onboarding import BetaOnboardingError, connect_x, onboard_x
from ocpf_post.model import AccountIdentity
from ocpf_post.product_runtime import PRODUCT_LINEAGE
from ocpf_post.setup_engine import SetupEngine
from ocpf_post.state import write_private_json


class FakeX:
    def __init__(self, token_file: Path, account_id: str = "123456789") -> None:
        self.token_file = token_file
        self.scoped = False
        self.identity = AccountIdentity(
            provider="x",
            account_id=account_id,
            username="beta_user",
            name="Beta User",
        )

    def readonly_account(self):
        return self.identity

    def readonly_usage(self, *, days: int = 7):
        return {
            "project_cap": 100,
            "project_usage": 0,
            "cap_reset_day": None,
            "billing_credit_balance": None,
            "boundary": "fixture",
        }


class FakeAuthorizingX:
    last_secret = None

    def __init__(self, *, client_id=None, client_secret=None, redirect_uri=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        FakeAuthorizingX.last_secret = client_secret
        self.identity = AccountIdentity(
            provider="x",
            account_id="987654321",
            username="connected",
            name="Connected User",
        )

    def authorize(self):
        return self.identity

    def readonly_account(self):
        return self.identity


@contextmanager
def product_environment(root: Path):
    names = {
        "POST_ONCE_PRODUCT_LINEAGE": PRODUCT_LINEAGE,
        "OCPF_POST_CONFIG_DIR": str(root / "config"),
        "OCPF_POST_STATE_DIR": str(root / "state"),
        "OCPF_POST_SETUP_STATE_DIR": str(root / "setup"),
    }
    before = {key: os.environ.get(key) for key in names}
    os.environ.update(names)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class BetaOnboardingTests(unittest.TestCase):
    def test_connect_x_persists_secret_only_after_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with product_environment(root):
                result = connect_x(
                    client_id="client",
                    client_secret="secret-value",
                    provider_factory=FakeAuthorizingX,
                )
                self.assertEqual(result["status"], "connected")
                self.assertEqual(result["account_id"], "987654321")
                self.assertTrue(result["client_secret_persisted"])
                self.assertNotIn("secret-value", json.dumps(result))
                secret = root / "config" / "x-client-secret"
                self.assertEqual(secret.read_text(encoding="utf-8").strip(), "secret-value")
                self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_connect_x_repairs_existing_secret_permissions_after_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with product_environment(root):
                secret = root / "config" / "x-client-secret"
                secret.parent.mkdir(parents=True)
                secret.write_text("stale\n", encoding="utf-8")
                secret.chmod(0o644)
                connect_x(
                    client_id="client",
                    client_secret="replacement",
                    provider_factory=FakeAuthorizingX,
                )
                self.assertEqual(secret.read_text(encoding="utf-8").strip(), "replacement")
                self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_onboard_x_preview_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with product_environment(root):
                engine = SetupEngine(root / "setup")
                engine.start(
                    "fresh",
                    operator_label="Beta Example",
                    timezone="UTC",
                    pace="occasional",
                )
                token = root / "config" / "x-token.json"
                write_private_json(
                    token,
                    {
                        "access_token": "fixture",
                        "scope": "tweet.read tweet.write users.read offline.access",
                        "expires_at": int(time.time()) + 3600,
                    },
                )
                provider = FakeX(token)
                result = onboard_x(
                    project_id="beta-example",
                    project_label="Beta Example",
                    campaign_text="Reviewed beta onboarding copy.",
                    provider=provider,
                )
                self.assertEqual(result["status"], "preview")
                self.assertFalse(result["campaign_allocation_enabled"])
                self.assertFalse((root / "config" / "runtime-projects.json").exists())
                self.assertFalse((root / "state" / "runtime-campaigns").exists())
                status = engine.status()
                self.assertEqual(status["session"]["stage"], "configuration_ready")

    def test_onboard_x_apply_reaches_verification_ready_without_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with product_environment(root):
                engine = SetupEngine(root / "setup")
                engine.start(
                    "fresh",
                    operator_label="Beta Example",
                    timezone="UTC",
                    pace="occasional",
                )
                token = root / "config" / "x-token.json"
                write_private_json(
                    token,
                    {
                        "access_token": "fixture",
                        "scope": "tweet.read tweet.write users.read offline.access",
                        "expires_at": int(time.time()) + 3600,
                    },
                )
                provider = FakeX(token)
                result = onboard_x(
                    project_id="beta-example",
                    project_label="Beta Example",
                    campaign_text="Reviewed beta onboarding copy.",
                    provider=provider,
                    apply=True,
                )
                self.assertEqual(result["status"], "verification_ready")
                self.assertEqual(result["campaign"]["campaign"], "BETAEXAMPLE-001")
                self.assertFalse(result["campaign_allocation_enabled"])
                self.assertEqual(result["project_import"]["result"], "imported")
                self.assertEqual(result["campaign_import"]["result"], "imported")
                status = engine.status()
                self.assertEqual(status["session"]["stage"], "verification_ready")
                self.assertFalse(status["publishing_authority"])
                self.assertFalse(status["automation_enabled"])

    def test_onboard_x_refuses_wrong_setup_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with product_environment(root):
                engine = SetupEngine(root / "setup")
                engine.start(
                    "explore",
                    operator_label=None,
                    timezone="UTC",
                    pace="occasional",
                )
                token = root / "config" / "x-token.json"
                write_private_json(
                    token,
                    {
                        "access_token": "fixture",
                        "scope": "tweet.read tweet.write users.read offline.access",
                        "expires_at": int(time.time()) + 3600,
                    },
                )
                with self.assertRaises(BetaOnboardingError):
                    onboard_x(
                        project_id="beta-example",
                        project_label="Beta Example",
                        campaign_text="Reviewed beta onboarding copy.",
                        provider=FakeX(token),
                        apply=True,
                    )


    def test_onboard_x_rejects_invalid_campaign_before_project_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with product_environment(root):
                engine = SetupEngine(root / "setup")
                engine.start(
                    "fresh",
                    operator_label="Beta Example",
                    timezone="UTC",
                    pace="occasional",
                )
                token = root / "config" / "x-token.json"
                write_private_json(
                    token,
                    {
                        "access_token": "fixture",
                        "scope": "tweet.read tweet.write users.read offline.access",
                        "expires_at": int(time.time()) + 3600,
                    },
                )
                with self.assertRaises(BetaOnboardingError):
                    onboard_x(
                        project_id="beta-example",
                        project_label="Beta Example",
                        campaign_id="BETAEXAMPLE/001",
                        campaign_text="Reviewed beta onboarding copy.",
                        provider=FakeX(token),
                        apply=True,
                    )
                self.assertFalse((root / "config" / "runtime-projects.json").exists())


if __name__ == "__main__":
    unittest.main()
