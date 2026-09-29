from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import product_runtime, runtime_release
from ocpf_post.setup_activation import SERVICES, TIMERS


ROOT = Path(__file__).resolve().parents[1]


class StandaloneProductIdentityTests(unittest.TestCase):
    def test_legacy_path_environment_cannot_redirect_standalone_product(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy_config = root / "legacy-config"
            legacy_state = root / "legacy-state"
            env = {
                "HOME": str(root / "home"),
                "XDG_CONFIG_HOME": str(root / "xdg-config"),
                "XDG_STATE_HOME": str(root / "xdg-state"),
                "XDG_DATA_HOME": str(root / "xdg-data"),
                "OCPF_POST_CONFIG_DIR": str(legacy_config),
                "OCPF_POST_STATE_DIR": str(legacy_state),
                "OCPF_POST_RELEASES_DIR": str(root / "legacy-releases"),
                "OCPF_POST_SETUP_STATE_DIR": str(root / "legacy-setup"),
            }
            with patch.dict(os.environ, env, clear=False):
                value = product_runtime.apply_environment()
                self.assertEqual(
                    Path(os.environ["OCPF_POST_CONFIG_DIR"]),
                    root / "xdg-config" / "poststeward" / "runtime",
                )
                self.assertEqual(
                    Path(os.environ["OCPF_POST_STATE_DIR"]),
                    root / "xdg-state" / "poststeward" / "runtime",
                )
                self.assertEqual(
                    Path(os.environ["OCPF_POST_RELEASES_DIR"]),
                    root / "xdg-data" / "poststeward" / "releases",
                )
                self.assertEqual(
                    Path(os.environ["OCPF_POST_SETUP_STATE_DIR"]),
                    root / "xdg-state" / "poststeward" / "setup",
                )
                self.assertNotEqual(Path(value["state_dir"]), legacy_state)
                self.assertFalse(value["original_post_once_mutation_allowed"])

    def test_public_product_overrides_map_to_internal_compatibility_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(
                os.environ,
                {
                    "POSTSTEWARD_RUNTIME_CONFIG_DIR": str(root / "config"),
                    "POSTSTEWARD_RUNTIME_STATE_DIR": str(root / "state"),
                    "POSTSTEWARD_RELEASES_DIR": str(root / "releases"),
                    "POSTSTEWARD_SETUP_STATE_DIR": str(root / "setup"),
                },
                clear=False,
            ):
                product_runtime.apply_environment()
                self.assertEqual(os.environ["OCPF_POST_CONFIG_DIR"], str(root / "config"))
                self.assertEqual(os.environ["OCPF_POST_STATE_DIR"], str(root / "state"))
                self.assertEqual(os.environ["OCPF_POST_RELEASES_DIR"], str(root / "releases"))
                self.assertEqual(os.environ["OCPF_POST_SETUP_STATE_DIR"], str(root / "setup"))

    def test_canonical_and_compatibility_entrypoints_use_product_boundary(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('poststeward = "ocpf_post.product_entry:main"', pyproject)
        self.assertIn('post-once = "ocpf_post.product_entry:main"', pyproject)
        self.assertIn('ocpf-post = "ocpf_post.product_entry:main"', pyproject)
        for name in ("poststeward", "post-once", "ocpf-post"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("ocpf_post.product_entry", text)

    def test_managed_unit_namespace_cannot_collide_with_original_installation(self) -> None:
        self.assertTrue(all(unit.startswith("poststeward-") for unit in TIMERS))
        self.assertTrue(all(unit.startswith("poststeward-") for unit in SERVICES))
        self.assertTrue(all(unit.startswith("poststeward-") for unit in runtime_release.RUNTIME_UNIT_FILES))
        self.assertFalse(any(unit.startswith("post-once-") or unit.startswith("ocpf-post-") for unit in TIMERS + SERVICES))

        critical = (
            "scripts/install-user-scheduler-timer",
            "scripts/install-user-portfolio-timer",
            "scripts/uninstall-user-scheduler-timer",
            "scripts/uninstall-user-portfolio-timer",
            "scripts/install-user-console",
            "scripts/restore-local-runtime.py",
            "scripts/safe-runtime-upgrade.py",
            "scripts/run-unattended",
        )
        for relative in critical:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("oneclickpostfactory/post-once", text, relative)
            self.assertNotIn("ocpf-post-run-due", text, relative)
            self.assertNotIn("ocpf-post-portfolio-refill", text, relative)
            self.assertNotIn("ocpf-post-collection", text, relative)
            self.assertNotIn("ocpf-post-replies", text, relative)
            self.assertNotIn("ocpf-post-console", text, relative)

    def test_standalone_product_has_no_packaged_owner_defaults(self) -> None:
        from ocpf_post import campaigns
        from ocpf_post.portfolio_source_loader import packaged_source_profiles
        from ocpf_post.registry import load_registry

        packaged_ids = campaigns.campaign_ids()
        self.assertTrue(packaged_ids)
        sample = packaged_ids[0]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(
                os.environ,
                {
                    "POSTSTEWARD_RUNTIME_LINEAGE": product_runtime.PRODUCT_LINEAGE,
                    "POSTSTEWARD_RUNTIME_CONFIG_DIR": str(root / "config"),
                    "POSTSTEWARD_RUNTIME_STATE_DIR": str(root / "state"),
                },
                clear=False,
            ):
                self.assertEqual(load_registry()["projects"], {})
                self.assertEqual(packaged_source_profiles()["projects"], {})
                self.assertEqual(campaigns.campaign_ids(), [])
                self.assertEqual(campaigns.builtin_manifest(sample), {})
                self.assertIsNone(campaigns.builtin_text(sample, "x"))

    def test_console_uses_separate_default_port_and_product_command(self) -> None:
        text = (ROOT / "scripts" / "install-user-console").read_text(encoding="utf-8")
        self.assertIn('POSTSTEWARD_CONSOLE_PORT:-8877', text)
        self.assertIn("poststeward-console.service", text)
        self.assertIn("ExecStart=/bin/sh $ROOT/poststeward console --port $PORT", text)


if __name__ == "__main__":
    unittest.main()
