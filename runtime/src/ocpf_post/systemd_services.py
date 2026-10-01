"""Stage PostSteward-owned user units with systemd-safe arguments and paths."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

JOBS = {'poststeward-run-due': ('run-due',60), 'poststeward-portfolio-refill':('refill',900),
        'poststeward-collection':('collect',900), 'poststeward-replies':('respond',600)}


def quote(value: str, *, command: bool = False) -> str:
    if '\n' in value or '\r' in value or '\0' in value:
        raise ValueError('Control characters are not allowed in service paths')
    value=value.replace('%','%%')
    if command:
        value=value.replace('$','$$')
    return json.dumps(value,ensure_ascii=False)


def stage(*, runtime_root: Path, state_root: Path, config_root: Path) -> dict[str, Any]:
    from ocpf_post.product_runtime import resolved_paths
    directory=Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'systemd'/'user'
    directory.mkdir(parents=True,exist_ok=True)
    env={name:value for name,value in os.environ.items() if name.startswith('XDG_')}
    env.update({'PATH':str(Path(sys.executable).parent)+':/usr/bin:/bin', 'PYTHONPATH':str(runtime_root/'src'),
                'PYTHONDONTWRITEBYTECODE':'1','POSTSTEWARD_RUNTIME_ROOT':str(runtime_root),
                'POSTSTEWARD_RUNTIME_STATE_DIR':str(state_root),'POSTSTEWARD_RUNTIME_CONFIG_DIR':str(config_root),
                'POSTSTEWARD_SETUP_STATE_DIR':str(resolved_paths()['setup']),'POSTSTEWARD_REQUIRE_CLOUD_FENCE':'1'})
    for name in ('POSTSTEWARD_ORIGIN','POSTSTEWARD_RUNTIME_RELEASE_SHA'):
        if os.environ.get(name): env[name]=os.environ[name]
    environment='\n'.join('Environment='+quote(name+'='+value) for name,value in env.items())
    for stem,(action,interval) in JOBS.items():
        service=f'''# managed-by: poststeward-service-controller
[Unit]
Description=PostSteward guarded {action}
[Service]
Type=oneshot
WorkingDirectory={quote(str(runtime_root))}
{environment}
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={quote(str(state_root))} {quote(str(config_root))}
RestrictSUIDSGID=true
LockPersonality=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ExecStart={quote(sys.executable,command=True)} -m ocpf_post.service_runner {action}
'''
        timer=f'''# managed-by: poststeward-service-controller
[Unit]
Description=PostSteward guarded {action} interval
[Timer]
OnBootSec=2min
OnUnitActiveSec={interval}s
AccuracySec=15s
Persistent=true
[Install]
WantedBy=timers.target
'''
        for suffix,text in (('service',service),('timer',timer)):
            target=directory/(stem+'.'+suffix)
            if target.is_symlink(): raise ValueError('Refusing symlinked user unit')
            if target.exists() and 'PostSteward' not in target.read_text() and 'poststeward' not in target.read_text():
                raise ValueError('Refusing unrelated user unit')
            fd,temporary=tempfile.mkstemp(prefix='.poststeward-',dir=directory)
            try:
                with os.fdopen(fd,'w') as handle:
                    handle.write(text);handle.flush();os.fsync(handle.fileno())
                os.replace(temporary,target)
            finally: Path(temporary).unlink(missing_ok=True)
    # A user manager retains its own XDG paths from login, rather than inheriting
    # the calling CLI's environment. Explicit links make custom private roots work.
    units=[str(directory/(stem+'.'+suffix)) for stem in JOBS for suffix in ('service','timer')]
    linked=subprocess.run(['systemctl','--user','link',*units],capture_output=True,timeout=30,check=False)
    if linked.returncode: raise ValueError('Could not link owned PostSteward units into the user manager')
    result=subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True,timeout=30,check=False)
    if result.returncode: raise ValueError('Could not reload the PostSteward user service manager')
    return {'status':'staged','manager':'systemd','provider_consequence':False}
