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
import time
import re
import socket
import threading
import urllib.request
import http.cookiejar

repo = Path(__file__).resolve().parent.parent
revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
if sys.platform != 'darwin' and not os.environ.get('WSL_DISTRO_NAME'):
    raise SystemExit('Native acceptance requires macOS or a real WSL distribution; Linux simulation refused.')
if sys.platform != 'darwin' and 'microsoft-standard' not in platform.release().lower():
    raise SystemExit('WSL 2 kernel required; WSL 1 is not supported for unattended operation.')
with tempfile.TemporaryDirectory(prefix="PostSteward owner's machine ", dir=Path.home()) as temporary:
    root = Path(temporary).resolve()
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
            raise AssertionError({'command': args, 'exit': result.returncode, 'stderr': result.stderr[-3000:], 'stdout':result.stdout[-3000:]})
        return result
    previous='06fb738b71db4782f4b2f7066814b2aa7ea8cb7e'
    def install(sha):return ['bash', str(repo/'public'/'install.sh'),'--version',sha,'--no-onboard']
    for _ in range(2):run(install(previous))
    command=str(root/'bin'/'poststeward')
    receipt=root/'state'/'poststeward'/'install.json'
    prefix=root/'data'/'poststeward'
    data=root/'state'/'poststeward'/'runtime';data.mkdir(parents=True,exist_ok=True)
    sentinel=data/'native-evidence.json';sentinel.write_text('{"durable":"retain across software changes"}')
    before=receipt.read_bytes()
    failed=run(install('0'*40),allowed=tuple(range(1,256)))
    assert receipt.read_bytes()==before and (prefix/'current').resolve().name==previous
    # Inject a terminated/truncated downloader at the actual native installer boundary.
    # This is fault injection, not a claim that GitHub suffered an outage.
    fault_bin=root/'fault-bin';fault_bin.mkdir()
    downloader=fault_bin/'curl';downloader.write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = "-o" ]; then shift; printf partial-download > "$1"; exit 18; fi; shift; done\nexit 18\n');downloader.chmod(0o755)
    real_path=env['PATH'];env['PATH']=str(fault_bin)+os.pathsep+real_path
    run(install(revision),allowed=tuple(range(1,256)));env['PATH']=real_path
    assert receipt.read_bytes()==before and (prefix/'current').resolve().name==previous
    run(install(revision))
    assert json.loads(receipt.read_text())['previous_revision']==previous
    rollback=json.loads(run([command,'rollback','--json']).stdout)
    run([command,'rollback','--apply','--expected-sha256',rollback['review_sha256'],'--json'])
    assert (prefix/'current').resolve().name==previous and sentinel.exists()
    run(install(revision))
    # Interrupted switch/receipt metadata mismatch is repaired only while inactive
    # by rerunning the exact installer; no durable publication data is rolled back.
    value=json.loads(receipt.read_text());value['resolved_revision']=previous
    value['release_path']=str(prefix/'releases'/previous)
    value['runtime_tree_sha256']=value['previous_runtime_tree_sha256']
    receipt.write_text(json.dumps(value));run(install(revision))
    assert json.loads(receipt.read_text())['resolved_revision']==revision and sentinel.exists()
    run(install(revision))
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
    assert json.loads(admission.stdout).get('host_capability_proof', {}).get('status') == 'READY', admission.stdout
    sys.path.insert(0, str(runtime / 'src'))
    os.environ.update(env)
    if sys.platform == 'darwin':
        from ocpf_post.launchd_services import LaunchdServiceController
        # Environment is deliberately installed only for this isolated acceptance.
        manager = LaunchdServiceController()
        try:
            manager.stage(runtime_root=runtime, state_root=root / 'state' / 'poststeward' / 'runtime',
                          config_root=root / 'config' / 'poststeward' / 'runtime')
            assert manager.arm()['all_active']
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                observed=manager._run(['launchctl','print',manager.domain+'/com.poststeward.run-due'],timeout=10)
                if re.search(r'last exit code\s*=\s*3\b',observed.stdout):break
                time.sleep(.2)
            else:raise AssertionError('Native launchd worker did not demonstrate inactive exit 3: '+observed.stdout)
            assert not any(x['active'] for x in manager.disarm()['timers'])
            assert manager.arm()['all_active']
        finally:
            manager.disarm()
            for path in manager.directory.glob('com.poststeward.*.plist'):
                path.unlink()
    else:
        from ocpf_post.setup_activation import SystemdServiceController
        manager = SystemdServiceController()
        state_root=root/'state'/'poststeward'/'runtime'
        config_root=root/'config'/'poststeward'/'runtime'
        state_root.mkdir(parents=True,exist_ok=True)
        config_root.mkdir(parents=True,exist_ok=True)
        try:
            manager.stage(runtime_root=runtime,state_root=state_root,config_root=config_root)
            unit_directory=root/'config'/'systemd'/'user'
            validated=manager._run(['systemd-analyze','--user','verify',*[str(path) for path in unit_directory.glob('poststeward-*')]],timeout=30)
            print('native_unit_validation',validated.returncode,validated.stdout,validated.stderr)
            try:
                assert manager.arm()['all_active']
            except Exception:
                from ocpf_post.setup_activation import TIMERS
                print(json.dumps({'native_systemd_diagnostics':manager.inspect()}))
                started=manager._run(['systemctl','--user','start',*TIMERS],timeout=30)
                print('native_start_result',started.returncode,started.stdout,started.stderr)
                print(json.dumps({'native_systemd_after_explicit_start':manager.inspect()}))
                for unit in TIMERS:
                    observed=manager._run(['systemctl','--user','show',unit,
                        '--property=ActiveState,SubState,UnitFileState,FragmentPath,LoadState,Result,ConditionResult,LastTriggerUSec,NextElapseUSecMonotonic'],timeout=10)
                    print(unit,observed.stdout,observed.stderr)
                raise
            assert not any(x['active'] for x in manager.disarm()['timers'])
            # Reactivation follows the product's stage -> arm protocol again.
            # systemd disable removes external links for custom XDG roots.
            manager.stage(runtime_root=runtime,state_root=state_root,config_root=config_root)
            assert manager.arm()['all_active']
            # Start one real guarded worker now rather than infer execution from timer enablement.
            worker=manager._run(['systemctl','--user','start','poststeward-run-due.service'],timeout=30)
            exit_status=manager._run(['systemctl','--user','show','poststeward-run-due.service',
                '--property=ExecMainStatus','--value'],timeout=30).stdout.strip()
            assert exit_status=='3', 'Expected inactive authority fence from real service execution, got '+exit_status
        finally:
            manager.disarm()
    from ocpf_post.product_runtime import apply_environment
    apply_environment()
    from ocpf_post import poststeward_cloud
    poststeward_cloud._write({'schema_version':1,'runtime_token':'synthetic-expired-never-sent','token_expires_at':0})
    try:poststeward_cloud.runtime_token()
    except poststeward_cloud.CloudError as error:assert error.code=='RUNTIME_PAIRING_EXPIRED'
    else:raise AssertionError('Expired local pairing token was accepted')
    assert poststeward_cloud.client_path().stat().st_mode&0o777==0o600
    poststeward_cloud.client_path().unlink()
    from ocpf_post.setup_browser import build_server, SetupBrowserError
    occupied=socket.socket();occupied.bind(('127.0.0.1',0));occupied.listen()
    try:
        try:build_server(root/'browser-config',port=occupied.getsockname()[1])
        except SetupBrowserError as error:assert error.code=='setup.browser.bind_failed'
        else:raise AssertionError('Occupied setup port was silently accepted')
    finally:occupied.close()
    server,app=build_server(root/'browser-config')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(app.bootstrap_url(server.server_port),timeout=10) as response:
            assert response.status==200 and b'Setup' in response.read()
        assert server.server_address[0]=='127.0.0.1'
    finally:server.shutdown();server.server_close();thread.join(timeout=10)
    # Explicit software removal retains isolated durable data; no cloud account changes.
    uninstall=json.loads(run([command,'uninstall','--retain-data','--json']).stdout)
    run([command,'uninstall','--retain-data','--apply','--expected-sha256',uninstall['review_sha256'],'--json'])
    assert not Path(command).exists() and sentinel.exists()
    run(install(revision))
    deletion=json.loads(run([command,'uninstall','--delete-data','--json']).stdout)
    run([command,'uninstall','--delete-data','--apply','--expected-sha256',deletion['review_sha256'],'--json'])
    assert not Path(command).exists() and not sentinel.exists()
    print(json.dumps({'platform': sys.platform, 'architecture': platform.machine(), 'revision': revision,
                      'macos_version': platform.mac_ver()[0], 'wsl': bool(os.environ.get('WSL_DISTRO_NAME')),
                      'fresh_install': 'passed', 'repeat_install': 'passed', 'spaces_and_apostrophes': 'passed',
                      'unpaired_fencing': 'passed', 'native_admission': json.loads(admission.stdout),
                      'native_service_load_stop_restart': 'passed', 'uninstall_retain_data':'passed','uninstall_delete_data':'passed',
                      'native_guarded_worker_exit':'3','upgrade_and_reviewed_rollback':'passed',
                      'http_failure_preserves_current':'passed','truncated_download_fault_injection':'passed',
                      'interrupted_metadata_repair':'passed','expired_pairing_token_preflight':'passed',
                      'loopback_setup_and_occupied_port':'passed',
                      'real_owner_provider_acceptance': 'unverified', 'provider_effects': 0}))
