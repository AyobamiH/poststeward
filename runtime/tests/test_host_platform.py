import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.host_platform import host, service_controller
from ocpf_post.launchd_services import JOBS, LaunchdServiceController


class HostPlatformTests(unittest.TestCase):
    def test_mac_selects_launchd_without_importing_systemctl(self):
        with patch('sys.platform', 'darwin'):
            self.assertIsInstance(service_controller(), LaunchdServiceController)
            self.assertEqual(host()['service_manager'], 'launchd')

    def test_wsl_reports_shutdown_limit(self):
        with patch('sys.platform', 'linux'), patch.dict(os.environ, {'WSL_DISTRO_NAME': 'Ubuntu-24.04'}):
            value = host()
            self.assertTrue(value['wsl'])
            self.assertIn('Windows host', value['persistence'])

    def test_plist_preserves_spaces_and_apostrophes_and_no_secrets(self):
        with tempfile.TemporaryDirectory(prefix="poststeward owner's ") as temp:
            root = Path(temp)
            manager = LaunchdServiceController()
            manager.directory = root / 'agents'
            with patch.object(manager, '_run', return_value=subprocess.CompletedProcess([], 0, '', '')):
                manager.stage(runtime_root=root, state_root=root / 'state', config_root=root / 'config')
            for label, _ in JOBS.values():
                value = plistlib.loads((manager.directory / f'{label}.plist').read_bytes())
                self.assertEqual(value['WorkingDirectory'], str(root))
                self.assertEqual(value['ProgramArguments'][1:3], ['-m', 'ocpf_post.service_runner'])
                self.assertEqual(value['Umask'], 0o077)
                self.assertNotIn('POSTSTEWARD_RUNTIME_TOKEN', value['EnvironmentVariables'])

    def test_stage_refuses_unmanaged_or_symlinked_agent(self):
        from ocpf_post.setup_activation import ActivationError
        with tempfile.TemporaryDirectory() as temp:
            manager = LaunchdServiceController()
            manager.directory = Path(temp)
            label = next(iter(JOBS.values()))[0]
            target = manager.directory / f'{label}.plist'
            target.write_bytes(plistlib.dumps({'Label': label}))
            with patch.object(manager, '_run', return_value=subprocess.CompletedProcess([], 0, '', '')):
                with self.assertRaises(ActivationError):
                    manager.stage(runtime_root=Path(temp), state_root=Path(temp), config_root=Path(temp))

    def test_unavailable_desktop_blocks_stage(self):
        from ocpf_post.setup_activation import ActivationError
        manager = LaunchdServiceController()
        with patch.object(manager, '_run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            with self.assertRaises(ActivationError):
                manager.stage(runtime_root=Path('/tmp'), state_root=Path('/tmp'), config_root=Path('/tmp'))

    def test_background_execution_without_review_is_fenced(self):
        from ocpf_post.service_runner import main
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            'POSTSTEWARD_RUNTIME_ROOT': temp, 'POSTSTEWARD_RUNTIME_STATE_DIR': temp + '/state',
            'POSTSTEWARD_RUNTIME_CONFIG_DIR': temp + '/config', 'POSTSTEWARD_SETUP_STATE_DIR': temp + '/setup',
        }), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 3)) as run:
            self.assertEqual(main(['run-due']), 3)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][-2:], ['ocpf_post.automation_authority', 'check'])


if __name__ == '__main__':
    unittest.main()
