import os
from pathlib import Path
import plistlib
import shlex
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

    def test_systemd_binds_exact_custom_paths_inside_private_tmp(self):
        from ocpf_post import systemd_services

        for label in ("custom-runtime", "owner's runtime 100% $special"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / label
                runtime = root / "release files"
                state = root / "state files"
                config = root / "config files"
                client = root / "paired client"
                for directory in (runtime, state, config):
                    directory.mkdir(parents=True)
                environment = {
                    "XDG_CONFIG_HOME": str(root / "unit config"),
                    "POSTSTEWARD_SETUP_STATE_DIR": str(root / "setup"),
                }
                with patch.dict(os.environ, environment), \
                        patch("ocpf_post.poststeward_cloud.client_path", return_value=client / "client.json"), \
                        patch("ocpf_post.systemd_services.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
                    systemd_services.stage(runtime_root=runtime, state_root=state, config_root=config)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0].args[0][:3], ["systemctl", "--user", "link"])
                self.assertEqual(run.call_args_list[1].args[0], ["systemctl", "--user", "daemon-reload"])
                writable = [str(path).replace("%", "%%") for path in (state, config, client)]
                for stem in systemd_services.JOBS:
                    unit = root / "unit config" / "systemd" / "user" / (stem + ".service")
                    directives = dict(line.split("=", 1) for line in unit.read_text().splitlines() if "=" in line)
                    self.assertEqual(shlex.split(directives["BindPaths"]), writable)
                    self.assertEqual(shlex.split(directives["ReadWritePaths"]), writable)
                    self.assertEqual(shlex.split(directives["BindReadOnlyPaths"]), [str(runtime).replace("%", "%%")])
                    for key, value in {
                        "PrivateTmp": "true", "ProtectSystem": "strict", "ProtectHome": "read-only",
                        "NoNewPrivileges": "true", "UMask": "0077", "RestrictSUIDSGID": "true",
                        "LockPersonality": "true", "ProtectKernelTunables": "true",
                        "ProtectKernelModules": "true", "ProtectControlGroups": "true",
                    }.items():
                        self.assertEqual(directives[key], value)

    def test_background_execution_without_review_is_fenced(self):
        from ocpf_post.service_runner import main
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            'POSTSTEWARD_RUNTIME_ROOT': temp, 'POSTSTEWARD_RUNTIME_STATE_DIR': temp + '/state',
            'POSTSTEWARD_RUNTIME_CONFIG_DIR': temp + '/config', 'POSTSTEWARD_SETUP_STATE_DIR': temp + '/setup',
        }), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 3)) as run:
            self.assertEqual(main(['run-due']), 3)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
