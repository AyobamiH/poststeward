"""Native platform execution, with isolated customer paths and no provider effect.

This proves native filesystem, installation, diagnosis and service fencing, not
real owner approval/provider readback. Those require separately reviewed access.
"""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

repo = Path(__file__).resolve().parent.parent
revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
if sys.platform != 'darwin' and not os.environ.get('WSL_DISTRO_NAME'):
    raise SystemExit('Native acceptance requires macOS or a real WSL distribution; Linux simulation refused.')
with tempfile.TemporaryDirectory(prefix="PostSteward owner's machine ") as temporary:
    root = Path(temporary)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('OCPF_POST_', 'POST_ONCE_', 'POSTSTEWARD_')):
            del env[key]
    env.update({'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_STATE_HOME': str(root / 'state'),
                'XDG_DATA_HOME': str(root / 'data'), 'XDG_CACHE_HOME': str(root / 'cache'),
                'POSTSTEWARD_INSTALL_PREFIX': str(root / 'data' / 'poststeward'),
                'POSTSTEWARD_BIN_DIR': str(root / 'bin'), 'PYTHONDONTWRITEBYTECODE': '1'})
    def run(args, *, allowed=(0,)):
        result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=180)
        if result.returncode not in allowed:
            raise AssertionError({'command': args, 'exit': result.returncode, 'stderr': result.stderr[-3000:]})
        return result
    installer = ['bash', str(repo / 'public' / 'install.sh'), '--version', revision, '--no-onboard']
    for _ in range(2):
        run(installer)
    command = str(root / 'bin' / 'poststeward')
    run([command, '--version'])
    help_value = json.loads(run([command, 'help', '--json']).stdout)
    assert help_value['product'] == 'poststeward'
    diagnosis = json.loads(run([command, 'doctor', '--json'], allowed=(0, 3)).stdout)
    assert diagnosis['status'] != 'READY', 'An unpaired machine must never become ready automatically.'
    runtime = root / 'data' / 'poststeward' / 'releases' / revision
    env.update({'PYTHONPATH': str(runtime / 'src'), 'POSTSTEWARD_RUNTIME_ROOT': str(runtime),
                'POSTSTEWARD_RUNTIME_RELEASE_SHA': revision})
    admission = run([sys.executable, '-m', 'ocpf_post.product_entry', 'setup', 'admission',
                     '--workspace', str(root / 'setup-probe'), '--production', '--json'], allowed=(0, 3))
    if sys.platform == 'darwin':
        sys.path.insert(0, str(runtime / 'src'))
        from ocpf_post.launchd_services import LaunchdServiceController
        # Environment is deliberately installed only for this isolated acceptance.
        os.environ.update(env)
        manager = LaunchdServiceController()
        try:
            manager.stage(runtime_root=runtime, state_root=root / 'state' / 'poststeward' / 'runtime',
                          config_root=root / 'config' / 'poststeward' / 'runtime')
            assert manager.arm()['all_active']
            assert not any(x['active'] for x in manager.disarm()['timers'])
            assert manager.arm()['all_active']
        finally:
            manager.disarm()
            for path in manager.directory.glob('com.poststeward.*.plist'):
                path.unlink()
    print(json.dumps({'platform': sys.platform, 'architecture': platform.machine(), 'revision': revision,
                      'macos_version': platform.mac_ver()[0], 'wsl': bool(os.environ.get('WSL_DISTRO_NAME')),
                      'fresh_install': 'passed', 'repeat_install': 'passed', 'spaces_and_apostrophes': 'passed',
                      'unpaired_fencing': 'passed', 'native_admission': json.loads(admission.stdout),
                      'native_service_load_stop_restart': 'passed' if sys.platform == 'darwin' else 'not_exercised',
                      'real_owner_provider_acceptance': 'unverified', 'provider_effects': 0}))
