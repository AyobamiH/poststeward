"""Reviewed lifecycle of installer-managed archives, independent of Git worktrees."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from ocpf_post import automation_authority, local_store
from ocpf_post.product_runtime import resolved_paths

SHA = re.compile(r'^[a-f0-9]{40}$')
SKIP = {'__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache', '.git', '.venv',
        'venv', 'dist', 'build', 'htmlcov'}


def receipt_path() -> Path:
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'poststeward' / 'install.json'


def tree_digest(root: Path) -> str:
    value = hashlib.sha256()
    for path in sorted(root.rglob('*'), key=lambda path:path.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if any(part in SKIP or part.endswith('.egg-info') for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError('Managed runtime contains a symbolic link')
        if not path.is_file() or path.name.endswith('.pyc') or path.name in {'.coverage', 'coverage.xml', 'unittest-results.log'}:
            continue
        data = path.read_bytes()
        value.update(relative.as_posix().encode() + b'\0' + str(len(data)).encode() + b'\0'
                     + hashlib.sha256(data).hexdigest().encode() + b'\n')
    return value.hexdigest()


def installation() -> tuple[dict[str, Any], Path, Path]:
    path = receipt_path()
    if not path.is_file() or path.is_symlink():
        raise ValueError('Installer receipt is missing or not a plain file')
    value = json.loads(path.read_text())
    if value.get('product') != 'poststeward' or not SHA.fullmatch(str(value.get('resolved_revision', ''))):
        raise ValueError('Installer receipt identity is invalid')
    prefix = Path(value.get('install_prefix') or Path(value['release_path']).parent.parent)
    binary = Path(value.get('bin_dir') or Path.home() / '.local' / 'bin')
    current = prefix / 'current'
    if not prefix.is_absolute() or prefix.is_symlink() or not current.is_symlink():
        raise ValueError('Installation is not a managed plain prefix/current symlink')
    expected = prefix / 'releases' / value['resolved_revision']
    if current.resolve() != expected.resolve() or expected.is_symlink() or not expected.is_dir():
        raise ValueError('Current release changed independently of its installer receipt')
    if tree_digest(expected) != value.get('runtime_tree_sha256'):
        raise ValueError('Current runtime does not match its recorded tree digest')
    shim = binary / 'poststeward'
    if shim.is_symlink() or not shim.is_file() or '# managed-by: poststeward-installer' not in shim.read_text():
        raise ValueError('PostSteward command is not an installer-managed plain shim')
    return value, prefix, binary


def require_inactive() -> None:
    marker = automation_authority.read(resolved_paths()['state'])
    if marker.get('status') == 'active':
        raise ValueError('Deactivate with the reviewed cloud-first workflow before lifecycle changes')
    from ocpf_post.host_platform import service_controller
    try:
        services = service_controller().inspect()
    except FileNotFoundError:
        services = {'timers': []}
    if any(row.get('active') or row.get('enabled') for row in services.get('timers', [])):
        raise ValueError('PostSteward background jobs are still enabled or loaded; deactivate first')


def lifecycle(action: str, *, retain_data: bool = True, apply: bool = False,
              expected_sha256: str | None = None) -> dict[str, Any]:
    if action not in {'rollback', 'uninstall'}:
        raise ValueError('Unknown installed lifecycle action')
    require_inactive()
    value, prefix, binary = installation()
    previous = value.get('previous_revision')
    target = prefix / 'releases' / str(previous)
    if action == 'rollback':
        if not SHA.fullmatch(str(previous)) or not target.is_dir() or target.is_symlink():
            raise ValueError('No retained previous exact release is available')
        if tree_digest(target) != value.get('previous_runtime_tree_sha256'):
            raise ValueError('Previous release no longer matches its saved tree digest')
        env = dict(os.environ, POSTSTEWARD_RUNTIME_RELEASE_SHA=previous)
        for route in (['--version'], ['help', '--json']):
            result = subprocess.run(['/bin/sh', str(target / 'poststeward'), *route], env=env,
                                    capture_output=True, timeout=30, check=False)
            if result.returncode:
                raise ValueError('Previous release does not pass CLI proof; current release preserved')
    data_paths = list(resolved_paths()[name] for name in ('config', 'state', 'setup'))
    client_dir = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'poststeward'
    if action == 'uninstall' and not retain_data:
        # Never interpret custom owner/reference paths as implicitly removable.
        for path in data_paths:
            if 'poststeward' not in path.parts or path.is_symlink():
                raise ValueError('Custom data root cannot be safely deleted; retain data and remove it manually after review')
    review = {'action': action, 'install_prefix': str(prefix), 'bin_dir': str(binary),
              'current_revision': value['resolved_revision'], 'previous_revision': previous,
              'retain_data': retain_data, 'data_paths': [str(x) for x in data_paths],
              'receipt_sha256': hashlib.sha256(receipt_path().read_bytes()).hexdigest()}
    review_hash = hashlib.sha256(json.dumps(review, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if not apply:
        return {'status': 'preview', **review, 'review_sha256': review_hash,
                'boundary': 'Software-only change while inactive. Durable publication evidence is never rolled back. Re-activation requires new review.'}
    if expected_sha256 != review_hash:
        raise ValueError('Installed lifecycle review changed')
    if action == 'rollback':
        # Atomic release switch; shim only carries metadata and must be updated too.
        shim = binary / 'poststeward'
        shim_text = re.sub(r'^export POSTSTEWARD_RUNTIME_RELEASE_SHA=.*$',
                           'export POSTSTEWARD_RUNTIME_RELEASE_SHA='+previous, shim.read_text(), flags=re.MULTILINE)
        fd, temporary = tempfile.mkstemp(prefix='.rollback-', dir=prefix)
        os.close(fd)
        Path(temporary).unlink()
        os.symlink(target, temporary)
        os.replace(temporary, prefix / 'current')
        local_store.write(receipt_path(), {**value, 'resolved_revision': previous,
            'release_path': str(target), 'runtime_tree_sha256': value['previous_runtime_tree_sha256'],
            'previous_revision': value['resolved_revision'], 'previous_runtime_tree_sha256': value['runtime_tree_sha256']})
        fd, temporary = tempfile.mkstemp(prefix='.poststeward-', dir=binary)
        with os.fdopen(fd, 'w') as handle:
            handle.write(shim_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o755)
        os.replace(temporary, shim)
    else:
        (binary / 'poststeward').unlink()
        (prefix / 'current').unlink()
        # Delete only exact managed archive directories; unrelated prefix files stay.
        for release in (prefix / 'releases').iterdir():
            if release.name in {value['resolved_revision'], value.get('previous_revision')} and release.is_dir() and not release.is_symlink():
                shutil.rmtree(release)
        receipt_path().unlink()
        if not retain_data:
            for path in data_paths:
                if path.exists():
                    shutil.rmtree(path)
            if client_dir.exists() and not client_dir.is_symlink():
                shutil.rmtree(client_dir)
    return {'status': 'rolled_back' if action == 'rollback' else 'uninstalled', **review,
            'review_sha256': review_hash, 'publishing_authority': False,
            'cloud_installation_revocation': 'Owner may revoke the inactive installation in the workspace.'}
