"""User launch agents behind the unchanged A–K activation/cloud fences.

No sudo, global daemon, shell interpolation, profile edits or automatic login
policy changes. Loaded interval jobs count as active even while awaiting a tick.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

JOBS = {
    'run-due': ('com.poststeward.run-due', 60),
    'refill': ('com.poststeward.portfolio-refill', 900),
    'collect': ('com.poststeward.collection', 900),
    'respond': ('com.poststeward.replies', 600),
}


class LaunchdServiceController:
    def __init__(self) -> None:
        self.domain = f'gui/{os.getuid()}'
        self.directory = Path.home() / 'Library' / 'LaunchAgents'

    @staticmethod
    def _run(args: list[str], **kwargs):
        return subprocess.run(args, text=True, capture_output=True, check=False, **kwargs)

    def inspect(self) -> dict[str, Any]:
        disabled = self._run(['launchctl', 'print-disabled', self.domain], timeout=10)
        rows = []
        for label, _ in JOBS.values():
            result = self._run(['launchctl', 'print', f'{self.domain}/{label}'], timeout=10)
            blocked = bool(re.search(r'"' + re.escape(label) + r'"\s*=>\s*true', disabled.stdout))
            rows.append({'unit': label, 'enabled': result.returncode == 0 and not blocked,
                         'active': result.returncode == 0})
        return {'schema_version': 1, 'manager': 'launchd', 'timers': rows,
                'all_enabled': all(x['enabled'] for x in rows),
                'all_active': all(x['active'] for x in rows),
                'persistence': 'Runs while this user is logged in and the Mac is awake; no wake-from-sleep guarantee.'}

    def stage(self, *, runtime_root: Path, state_root: Path, config_root: Path) -> dict[str, Any]:
        from ocpf_post.setup_activation import ActivationError
        from ocpf_post.product_runtime import resolved_paths
        if self._run(['launchctl', 'print', self.domain], timeout=10).returncode:
            raise ActivationError('activation.launchd.unavailable', 'Sign in to a macOS desktop user session before activation.')
        self.directory.mkdir(parents=True, exist_ok=True)
        env = {name: value for name, value in os.environ.items() if name.startswith('XDG_')}
        env.update({
            'PATH': str(Path(sys.executable).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
            'PYTHONPATH': str(runtime_root / 'src'),
            'PYTHONDONTWRITEBYTECODE': '1',
            'POSTSTEWARD_RUNTIME_ROOT': str(runtime_root),
            'POSTSTEWARD_RUNTIME_CONFIG_DIR': str(config_root),
            'POSTSTEWARD_RUNTIME_STATE_DIR': str(state_root),
            'POSTSTEWARD_SETUP_STATE_DIR': str(resolved_paths()['setup']),
            'POSTSTEWARD_REQUIRE_CLOUD_FENCE': '1',
        })
        for name in ('POSTSTEWARD_ORIGIN', 'POSTSTEWARD_RUNTIME_RELEASE_SHA'):
            if os.environ.get(name):
                env[name] = os.environ[name]
        for action, (label, interval) in JOBS.items():
            target = self.directory / f'{label}.plist'
            if target.is_symlink():
                raise ActivationError('activation.launchd.unmanaged', 'Refusing a symlinked launch agent.')
            if target.exists():
                old = plistlib.loads(target.read_bytes())
                if old.get('PostStewardManaged') != 1 or old.get('Label') != label:
                    raise ActivationError('activation.launchd.unmanaged', 'Refusing to replace an unrelated launch agent.')
            value = {'Label': label, 'PostStewardManaged': 1,
                     'ProgramArguments': [sys.executable, '-m', 'ocpf_post.service_runner', action],
                     'WorkingDirectory': str(runtime_root), 'EnvironmentVariables': env,
                     'StartInterval': interval, 'RunAtLoad': True, 'Umask': 0o077,
                     'ProcessType': 'Background'}
            fd, temporary = tempfile.mkstemp(prefix='.poststeward-', dir=self.directory)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    plistlib.dump(value, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return {'status': 'staged', 'manager': 'launchd', 'provider_consequence': False}

    def preflight(self, **kwargs):
        from ocpf_post.setup_activation import SystemdServiceController
        return SystemdServiceController.preflight(self, **kwargs)

    def arm(self) -> dict[str, Any]:
        from ocpf_post.setup_activation import ActivationError
        for label, _ in JOBS.values():
            self._run(['launchctl', 'enable', f'{self.domain}/{label}'], timeout=20)
            result = self._run(['launchctl', 'bootstrap', self.domain, str(self.directory / f'{label}.plist')], timeout=30)
            if result.returncode:
                raise ActivationError('activation.launchd.arm_failed', 'Could not load all PostSteward launch agents; activation remains fenced.')
        observed = self.inspect()
        if not observed['all_enabled'] or not observed['all_active']:
            raise ActivationError('activation.launchd.attestation_failed', 'Launch agent readback did not match activation.')
        return observed

    def disarm(self) -> dict[str, Any]:
        from ocpf_post.setup_activation import ActivationError
        for label, _ in JOBS.values():
            self._run(['launchctl', 'bootout', f'{self.domain}/{label}'], timeout=30)
            self._run(['launchctl', 'disable', f'{self.domain}/{label}'], timeout=20)
        deadline=time.monotonic()+10
        observed = self.inspect()
        while any(row['active'] for row in observed['timers']) and time.monotonic()<deadline:
            time.sleep(0.2)
            observed=self.inspect()
        if any(row['active'] or row['enabled'] for row in observed['timers']):
            raise ActivationError('activation.launchd.disarm_failed', 'A PostSteward launch agent is still loaded.')
        return observed
