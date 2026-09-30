from __future__ import annotations

import hashlib
import unittest
from importlib.resources import files

from ocpf_post.campaigns import builtin_manifest, builtin_text, destination_binding


class CampaignPackageTests(unittest.TestCase):
    def test_hash_manifest_packages_have_resolvable_payloads(self) -> None:
        checked = 0
        for root in (files("ocpf_post") / "campaigns").iterdir():
            if not root.is_dir() or not (root / "manifest.json").is_file():
                continue
            manifest = builtin_manifest(root.name)
            hashes = manifest.get("payload_sha256")
            if hashes is None:
                continue  # Older publication evidence predates package hashes.
            checked += 1
            with self.subTest(campaign=root.name):
                self.assertEqual(manifest["campaign"], root.name)
                self.assertTrue(manifest.get("project"))
                self.assertTrue(manifest.get("source", {}).get("source_id"))
                providers = manifest["providers"]
                self.assertEqual(len(providers), len(set(providers)))
                self.assertEqual(set(hashes), set(providers))
                for provider in providers:
                    with self.subTest(provider=provider):
                        text = builtin_text(root.name, provider)
                        self.assertTrue(text)
                        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), hashes[provider])
                        binding = destination_binding(root.name, provider)
                        self.assertIsNotNone(binding)
                        self.assertEqual(binding["provider"], provider)
                        self.assertTrue(binding["account_id"])
                        if provider == "x":
                            self.assertLessEqual(len(text), 280)
                        elif provider == "threads":
                            self.assertLessEqual(len(text), 500)
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
