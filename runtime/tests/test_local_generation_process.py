"""Exercise local authority replacement across real process boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from ocpf_post import automation_authority as authority


class LocalGenerationProcessTests(unittest.TestCase):
    def test_running_child_refuses_replaced_local_authority(self):
        for changed in ("authority_generation", "installation_id", "operation_id"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = os.environ.copy()
                env.update(OCPF_POST_STATE_DIR=directory,
                           POSTSTEWARD_RUNTIME_ROOT=str(Path(__file__).resolve().parents[1]),
                           POSTSTEWARD_RUNTIME_RELEASE_SHA="a" * 40)
                env.pop(authority.UNATTENDED_FENCE_ENV, None)
                values = dict(root=root, operation_id=str(uuid.uuid4()),
                              installation_id=str(uuid.uuid4()), authority_generation=1,
                              review_sha256="b" * 64, runtime_revision="a" * 40,
                              cloud_executor=dict(workspace="isolated-fixture",
                                  installation_id=str(uuid.uuid4()), authority_generation=7))
                authority.activate(**values)
                capture = subprocess.run([sys.executable, "-c",
                    "import json; from ocpf_post.automation_authority import unattended_context; print(json.dumps(unattended_context()))"],
                    env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(capture.returncode, 0, capture.stderr)
                env[authority.UNATTENDED_FENCE_ENV] = capture.stdout.strip()
                child = """import sys
from pathlib import Path
from ocpf_post.automation_authority import unattended_cloud_fence
unattended_cloud_fence()
Path(sys.argv[1]).write_text("effect allowed")
"""
                effect = root / "effect"
                accepted = subprocess.run([sys.executable, "-c", child, str(effect)],
                    env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(accepted.returncode, 0, accepted.stderr)
                effect.unlink()
                values[changed] = 2 if changed == "authority_generation" else str(uuid.uuid4())
                authority.activate(**values)
                refused = subprocess.run([sys.executable, "-c", child, str(effect)],
                    env=env, capture_output=True, text=True, timeout=20)
                self.assertNotEqual(refused.returncode, 0, "Stale child retained authority: " + changed)
                self.assertFalse(effect.exists())
                self.assertIn("activation changed", refused.stderr)
