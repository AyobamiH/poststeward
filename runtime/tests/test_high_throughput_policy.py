from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import admission, portfolio
from ocpf_post.scoped_admission import Budget, INVENTORY_SAFETY_DAYS
from ocpf_post.source_routes import routes as source_routes

UTC = timezone.utc
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


class DeepInventoryAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "OCPF_POST_CONFIG_DIR": str(self.root / "config"),
            "OCPF_POST_STATE_DIR": str(self.root / "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        (self.root / "config").mkdir()
        (self.root / "state").mkdir()

        self.saved = deepcopy(portfolio.DEFAULT_POLICY)
        self.saved["providers"]["linkedin"]["daily_target"] = 20
        self.saved["providers"]["linkedin"]["flow_mode"] = "fixed"
        self.saved["providers"]["linkedin"]["hard_daily_ceiling"] = 100
        self.saved["release_pacing"] = {
            "schema_version": 1,
            "mode": "steady_originals",
            "unit": "logical_publications_per_account_per_local_day",
        }

    def budget(self, rows, *, unavailable=None):
        stack = [
            patch("ocpf_post.portfolio.delivery_candidates", return_value=deepcopy(rows)),
            patch("ocpf_post.portfolio.load_policy", return_value=deepcopy(self.saved)),
            patch("ocpf_post.account_profiles.profile", return_value=None),
            patch("ocpf_post.account_profiles.unavailable", return_value=unavailable),
            patch("ocpf_post.scheduler.schedule_records", return_value=[]),
            patch.object(Budget, "_reserve_stock", return_value=len(rows)),
            patch("ocpf_post.registry.load_registry", return_value={
                "projects": {
                    "project-new": {
                        "accounts": {
                            "linkedin": {
                                "provider": "linkedin",
                                "account_id": "urn:li:person:member",
                            }
                        }
                    }
                }
            }),
            patch.object(admission, "load_policy", return_value=deepcopy(admission.DEFAULT_POLICY)),
        ]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)
        return Budget(NOW, False)

    def test_legacy_provider_high_water_is_diagnostic_not_a_gate(self):
        rows = [
            {
                "campaign": f"LI-{i}",
                "project": f"project-{i % 10}",
                "provider": "linkedin",
                "account_id": "urn:li:person:member",
            }
            for i in range(82)
        ]
        result = self.budget(rows).admit(
            "project-new", "linkedin", "urn:li:person:member",
        )
        self.assertTrue(result["admitted"])
        self.assertIn("legacy_provider_high_water", result["pressure_reasons"])
        self.assertEqual(result["reserve_limit"], 20 * INVENTORY_SAFETY_DAYS)
        self.assertEqual(result["reasons"], ["within_deep_inventory_safety_ceiling"])

    def test_hard_inventory_safety_ceiling_still_bounds_one_account(self):
        rows = [
            {
                "campaign": f"LI-{i}",
                "project": f"project-{i % 10}",
                "provider": "linkedin",
                "account_id": "urn:li:person:member",
            }
            for i in range(20 * INVENTORY_SAFETY_DAYS)
        ]
        result = self.budget(rows).admit(
            "project-new", "linkedin", "urn:li:person:member",
        )
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reasons"], ["account_inventory_safety_ceiling"])

    def test_unavailable_destination_still_fails_closed(self):
        result = self.budget([], unavailable="inactive").admit(
            "project", "linkedin", "urn:li:organization:123",
        )
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reasons"], ["account_unavailable"])


class LinkedInRouteTests(unittest.TestCase):
    def test_page_bound_project_has_independent_member_and_page_routes(self):
        registry = {
            "projects": {
                "opstruth": {
                    "default_accounts": {
                        "x": "x-founder",
                        "linkedin": "linkedin-founder",
                    },
                    "accounts": {
                        "x-founder": {
                            "provider": "x",
                            "account_id": "1",
                        },
                        "linkedin-founder": {
                            "provider": "linkedin",
                            "account_id": "urn:li:person:member",
                        },
                        "linkedin-page": {
                            "provider": "linkedin",
                            "account_id": "urn:li:organization:999",
                        },
                    },
                }
            }
        }
        profile = {
            "project": "opstruth",
            "providers": ["x"],
            "destinations": {"x": "x-founder"},
        }

        def resolve(project, alias, expected_provider=None):
            row = registry["projects"][project]["accounts"][alias]
            self.assertEqual(row["provider"], expected_provider)
            return {"alias": alias, **row}

        with patch("ocpf_post.registry.load_registry", return_value=registry), \
             patch("ocpf_post.registry.resolve_account", side_effect=resolve):
            rows = source_routes("opstruth", profile)

        self.assertEqual(
            [(r["provider"], r["account_id"], r["default"]) for r in rows],
            [
                ("x", "1", True),
                ("linkedin", "urn:li:person:member", True),
                ("linkedin", "urn:li:organization:999", False),
            ],
        )


class SteadyTargetActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.config.mkdir()
        self.state.mkdir()
        self.home.mkdir()
        value = deepcopy(portfolio.DEFAULT_POLICY)
        for provider, target in {"x": 20, "threads": 20, "linkedin": 15}.items():
            value["providers"][provider]["daily_target"] = target
            value["providers"][provider]["flow_mode"] = "fixed"
            value["providers"][provider]["hard_daily_ceiling"] = 100
        value["release_pacing"] = {
            "schema_version": 1,
            "mode": "steady_originals",
            "unit": "logical_publications_per_account_per_local_day",
        }
        self.policy = self.config / "portfolio-policy.json"
        self.policy.write_text(json.dumps(value), encoding="utf-8")
        self.before = json.loads(self.policy.read_text())
        self.env = patch.dict(os.environ, {
            "HOME": str(self.home),
            "OCPF_POST_CONFIG_DIR": str(self.config),
            "OCPF_POST_STATE_DIR": str(self.state),
            "OCPF_POST_PORTFOLIO_POLICY": str(self.policy),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_owner_approved_24_24_20_changes_only_release_targets(self):
        script = ROOT / "scripts" / "steady-targets-24-24-20.py"
        preview = subprocess.run(
            [sys.executable, "-B", str(script)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertEqual(json.loads(preview.stdout)["after_targets"], {
            "x": 24, "threads": 24, "linkedin": 20,
        })
        applied = subprocess.run(
            [sys.executable, "-B", str(script), "--apply", "--approve-24-24-20"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        after = json.loads(self.policy.read_text())
        self.assertEqual(
            [after["providers"][p]["daily_target"] for p in ("x", "threads", "linkedin")],
            [24, 24, 20],
        )
        for provider in ("x", "threads", "linkedin"):
            self.assertEqual(after["providers"][provider]["hard_daily_ceiling"], 100)
            self.assertEqual(
                after["providers"][provider]["window_start"],
                self.before["providers"][provider]["window_start"],
            )


class ConversationSetupTests(unittest.TestCase):
    def test_x_automatic_requires_explicit_x_approval_reference(self):
        script = ROOT / "scripts" / "enable-conversation-loop.py"
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--x-automatic"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--x-automatic requires --x-approval-reference", result.stderr)


if __name__ == "__main__":
    unittest.main()
