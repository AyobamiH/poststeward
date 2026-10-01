"""Portable, bounded background execution. Same local marker/cloud authority.

Unlike inherited shell helpers this works without GNU timeout, a Git checkout,
or unquoted unit paths. It does not create or renew authority generations.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    from ocpf_post.product_runtime import apply_environment
    from ocpf_post import automation_authority
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in {'run-due', 'refill', 'collect', 'respond'}:
        return 2
    paths = apply_environment()
    # check() owns the reviewed local identity/revision/marker validation.
    guard = subprocess.run([sys.executable, '-m', 'ocpf_post.automation_authority', 'check'],
                           stdout=subprocess.DEVNULL, timeout=30, check=False)
    if guard.returncode:
        return guard.returncode
    root = Path(os.environ['POSTSTEWARD_RUNTIME_ROOT']).resolve()
    def command(*parts: str) -> int:
        return subprocess.run([sys.executable, '-m', 'ocpf_post.product_entry', *parts],
                              cwd=root, timeout=180, check=False).returncode
    action = args[0]
    if action in {'run-due', 'respond'} and command('cloud', 'heartbeat'):
        return 3
    if action == 'run-due':
        return command('run-due')
    if action == 'refill':
        for parts in [('portfolio', 'experiment', 'reconcile', '--apply'), ('replenish', 'refresh', '--apply')]:
            command(*parts)
        for parts in [('replenish', 'reconcile'), ('portfolio', 'reconcile'),
                      ('portfolio', 'refill', '--apply', '--horizon-minutes', '75')]:
            result = command(*parts)
            if result:
                return result
        return 0
    return subprocess.run([sys.executable, '-m', 'ocpf_post.operating_cycles', action],
                          cwd=root, timeout=180, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
