#!/usr/bin/env python3
"""Restore existing user timers and the independent collector to this checkout without replaying posts."""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
TIMERS = ('post-once-run-due.timer', 'post-once-portfolio-refill.timer', 'post-once-collection.timer', 'post-once-replies.timer')
SERVICES = ('post-once-run-due.service', 'post-once-portfolio-refill.service', 'post-once-collection.service', 'post-once-replies.service')


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True,
                          timeout=kwargs.pop('timeout', 30), **kwargs)


def unit_state(unit):
    if unit.startswith(('post-once-collection.', 'post-once-replies.')):
        loaded = run(['systemctl', '--user', 'show', unit, '-p', 'LoadState', '--value'])
        if loaded.stdout.strip() == 'not-found':
            return 'missing'
    result = run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--value'])
    if result.returncode or not result.stdout.strip():
        raise RuntimeError('Could not inspect user service state')
    return result.stdout.strip()


def main():
    # No fetch, pull, checkout, reset, release promotion or credential changes.
    for args in (['git', 'diff', '--quiet'], ['git', 'diff', '--cached', '--quiet']):
        if run(args).returncode:
            raise RuntimeError('Tracked checkout changes must be resolved before restoring timers')
    original = {t: unit_state(t) for t in TIMERS}
    installed = False
    steps = []
    try:
        installed_timers = [t for t in TIMERS if original[t] != 'missing']
        if run(['systemctl', '--user', 'stop', *installed_timers]).returncode:
            raise RuntimeError('Could not pause timer wake-ups')
        # Never stop/kill an executing service or interrupt a provider write.
        deadline = time.monotonic() + 55
        while any(unit_state(s) not in {'inactive', 'failed', 'missing'} for s in SERVICES):
            if time.monotonic() >= deadline:
                raise RuntimeError('A service is still running; no timer files were changed')
            time.sleep(1)
        result = run(['sh', 'scripts/install-user-portfolio-timer'],
                     env={**os.environ, 'OCPF_POST_DEFER_TIMER_START': '1'})
        if result.returncode:
            raise RuntimeError('Timer installation failed; inspect the local systemd configuration')
        installed = True
        collection = run(['systemctl', '--user', 'start', '--no-block', SERVICES[2]])
        steps.append({'step': 'collection-start-requested', 'exit_code': collection.returncode})
        refill = run(['systemctl', '--user', 'start', '--no-block', SERVICES[1]])
        steps.append({'step': 'normal-refill-start-requested', 'exit_code': refill.returncode})
        print(
            'Timer paths restored. Normal collection/refill work was handed to systemd '
            'without making release reconciliation wait for operational completion.',
            flush=True,
        )
    finally:
        resume = list(TIMERS) if installed else [t for t in TIMERS if original[t] == 'active']
        if resume:
            result = run(['systemctl', '--user', 'start', *resume])
            if result.returncode:
                raise RuntimeError('Could not resume timers; inspect systemctl --user status')
    status = {t: unit_state(t) for t in TIMERS}
    report = {'result': 'restored' if all(v == 'active' for v in status.values())
              and all(s['exit_code'] == 0 for s in steps) else 'attention',
              'timers': status, 'steps': steps,
              'boundary': 'Timer paths restored to this checkout. Collection/refill start requests are handed to systemd and may still be running after reconciliation; normal authorised scheduling resumes, no failed schedule or receipt was rewritten, and publication remains unconfirmed until observed.'}
    print(json.dumps(report, indent=2))
    return 0 if report['result'] == 'restored' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        # Provider/service stdout may contain private data; do not echo it.
        print(json.dumps({'result': 'attention', 'error_type': type(exc).__name__,
                          'next_action': 'Inspect systemctl --user status for post-once timers and services.'}))
        raise SystemExit(2)
