from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpf_post.portal_import import import_x_portal_credentials


class PortalImportTests(unittest.TestCase):
    def test_import_persists_rotating_tokens_and_confidential_secret_privately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OCPF_POST_CONFIG_DIR": tmp}, clear=False):
                result = import_x_portal_credentials(
                    access_token="access-value",
                    refresh_token="refresh-value",
                    client_id="client-id",
                    client_secret="client-secret",
                    access_expires_in=7200,
                )

                token_path = Path(result["token_file"])
                secret_path = Path(result["secret_file"])
                payload = json.loads(token_path.read_text(encoding="utf-8"))

                self.assertEqual(payload["access_token"], "access-value")
                self.assertEqual(payload["refresh_token"], "refresh-value")
                self.assertEqual(payload["client_id"], "client-id")
                self.assertEqual(payload["source"], "x_developer_portal")
                self.assertEqual(secret_path.read_text(encoding="utf-8"), "client-secret")

                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(token_path.stat().st_mode), 0o600)
                    self.assertEqual(stat.S_IMODE(secret_path.stat().st_mode), 0o600)
                    self.assertEqual(stat.S_IMODE(Path(tmp).stat().st_mode), 0o700)

    def test_import_rejects_missing_refresh_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OCPF_POST_CONFIG_DIR": tmp}, clear=False):
                with self.assertRaises(ValueError):
                    import_x_portal_credentials(
                        access_token="access-value",
                        refresh_token="",
                        client_id="client-id",
                    )


if __name__ == "__main__":
    unittest.main()
