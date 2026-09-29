from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.operations_snapshot import coverage_fingerprints


class EditorialSnapshotStabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.config.mkdir()
        self.state.mkdir()
        env = patch.dict(os.environ, {
            "OCPF_POST_CONFIG_DIR": str(self.config),
            "OCPF_POST_STATE_DIR": str(self.state),
        })
        env.start()
        self.addCleanup(env.stop)

    def write(self, path: Path, text: str = "{}"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_unrelated_operational_churn_does_not_invalidate_editorial_snapshot(self):
        self.write(self.state / "publish-receipts.jsonl",
                   '{"campaign":"A","provider":"x","status":"published_verified"}\n')
        before = coverage_fingerprints()
        for name in (
            "alert-delivery.json",
            "acceptance-views.json",
            "reply-worker.json",
            "operating-incidents.json",
            "operations-report.json",
            "editorial-handoff-status.json",
        ):
            self.write(self.state / name, '{"schema_version":1,"observed_at":"later"}')
        self.assertEqual(before, coverage_fingerprints())

    def test_consequence_and_supply_inputs_still_invalidate_editorial_snapshot(self):
        relevant = [
            self.state / "publish-receipts.jsonl",
            self.state / "schedule-events.jsonl",
            self.state / "source-observations.json",
            self.state / "vault-observations.json",
            self.state / "performance-snapshots.jsonl",
            self.state / "performance-feedback.json",
            self.state / "performance-window-state.json",
            self.config / "account-profiles.json",
            self.config / "vaults.json",
            self.config / "runtime-sources.json",
            self.config / "portfolio-policy.json",
            self.config / "linkedin-token.json",
        ]
        for index, path in enumerate(relevant):
            with self.subTest(path=path.name):
                for existing in relevant:
                    existing.unlink(missing_ok=True)
                self.write(path, "{}\n" if path.suffix == ".jsonl" else "{}")
                before = coverage_fingerprints()
                self.write(path, '{"changed":true}\n')
                self.assertNotEqual(before, coverage_fingerprints(), f"{index}:{path}")

    def test_additional_account_and_runtime_campaign_membership_are_part_of_snapshot(self):
        before = coverage_fingerprints()
        self.write(self.config / "accounts" / "linkedin" / "urn-page" / "token.json",
                   '{"credential_source":"provider_default"}')
        after_account = coverage_fingerprints()
        self.assertNotEqual(before, after_account)
        self.write(self.state / "runtime-campaigns" / "CAMPAIGN" / "manifest.json",
                   '{"runtime_imported":true}')
        self.assertNotEqual(after_account, coverage_fingerprints())


if __name__ == "__main__":
    unittest.main()
