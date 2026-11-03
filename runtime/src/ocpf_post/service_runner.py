"""Portable, bounded background execution. Same local marker/cloud authority.

Unlike inherited shell helpers this works without GNU timeout, a Git checkout,
or unquoted unit paths. It does not create or renew authority generations.
"""
from __future__ import annotations

import json
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
    try:
        context = automation_authority.unattended_context(paths['state_dir'])
    except automation_authority.AutomationAuthorityError as exc:
        print(f"Unattended authority blocked: {exc}", file=sys.stderr)
        return 3
    child_environment = {
        **os.environ, automation_authority.UNATTENDED_FENCE_ENV: json.dumps(context, sort_keys=True),
    }
    root = Path(os.environ['POSTSTEWARD_RUNTIME_ROOT']).resolve()
    def command(*parts: str) -> int:
        return subprocess.run([sys.executable, '-m', 'ocpf_post.product_entry', *parts],
                              cwd=root, env=child_environment, timeout=180, check=False).returncode
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
                          cwd=root, env=child_environment, timeout=180, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
