from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ocpf_post.replenisher_reconcile import reconcile_runtime_sources
from ocpf_post.state import write_private_json
from ocpf_post.replenisher import source_state_file


class ReplenisherReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_state = os.environ.get("OCPF_POST_STATE_DIR")
        self.old_config = os.environ.get("OCPF_POST_CONFIG_DIR")
        os.environ["OCPF_POST_STATE_DIR"] = self.tmp.name
        os.environ["OCPF_POST_CONFIG_DIR"] = self.tmp.name

    def tearDown(self) -> None:
        if self.old_state is None:
            os.environ.pop("OCPF_POST_STATE_DIR", None)
        else:
            os.environ["OCPF_POST_STATE_DIR"] = self.old_state
        if self.old_config is None:
            os.environ.pop("OCPF_POST_CONFIG_DIR", None)
        else:
            os.environ["OCPF_POST_CONFIG_DIR"] = self.old_config
        self.tmp.cleanup()

    def test_old_readme_snapshot_is_disabled(self) -> None:
        root = Path(self.tmp.name) / "runtime-campaigns" / "OCPF-AUTO-01I-OLD"
        root.mkdir(parents=True)
        manifest = {
            "campaign": "OCPF-AUTO-01I-OLD",
            "runtime_generated": True,
            "source": {
                "type": "repository_product_truth",
                "repository": "AyobamiH/oneclickpostfactory",
                "source_sha": "old-readme",
            },
            "allocation": {"enabled": True, "lane": "commercial", "priority": 90},
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        write_private_json(source_state_file(), {
            "schema_version": 1,
            "repositories": {
                "AyobamiH/oneclickpostfactory": {"readme_sha": "new-readme"}
            },
        })

        result = reconcile_runtime_sources()
        self.assertEqual(len(result), 1)
        updated = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(updated["allocation"]["enabled"])
        self.assertEqual(updated["allocation"]["superseded_by"], "README:new-readme")

    def test_old_readme_snapshot_disables_evidence_grounded_generation(self) -> None:
        root = Path(self.tmp.name) / "runtime-campaigns" / "OCPF-GEN-OLD"
        root.mkdir(parents=True)
        manifest = {
            "campaign": "OCPF-GEN-OLD",
            "runtime_generated": True,
            "payload_frozen": True,
            "source": {
                "type": "evidence_grounded_generation",
                "repository": "AyobamiH/oneclickpostfactory",
                "source_sha": "old-readme",
                "path": "README.md",
            },
            "allocation": {"enabled": True, "lane": "evergreen", "priority": 72},
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        write_private_json(source_state_file(), {
            "schema_version": 1,
            "repositories": {
                "AyobamiH/oneclickpostfactory": {"readme_sha": "new-readme"}
            },
        })

        result = reconcile_runtime_sources()
        self.assertEqual(len(result), 1)
        updated = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(updated["allocation"]["enabled"])
        self.assertEqual(updated["allocation"]["superseded_by"], "README:new-readme")

    def test_current_snapshot_remains_enabled(self) -> None:
        root = Path(self.tmp.name) / "runtime-campaigns" / "OCPF-AUTO-01I-CURRENT"
        root.mkdir(parents=True)
        manifest = {
            "campaign": "OCPF-AUTO-01I-CURRENT",
            "runtime_generated": True,
            "source": {
                "type": "repository_product_truth",
                "repository": "AyobamiH/oneclickpostfactory",
                "source_sha": "same-readme",
            },
            "allocation": {"enabled": True, "lane": "commercial", "priority": 90},
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        write_private_json(source_state_file(), {
            "schema_version": 1,
            "repositories": {
                "AyobamiH/oneclickpostfactory": {"readme_sha": "same-readme"}
            },
        })
        self.assertEqual(reconcile_runtime_sources(), [])
        updated = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(updated["allocation"]["enabled"])


if __name__ == "__main__":
    unittest.main()
