#!/usr/bin/env python3
"""Gather open-work evidence without repeating established setup or publication."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CLI = str(ROOT / 'ocpf-post')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observe-only', action='store_true', help='Local evidence only (the default)')
    parser.add_argument('--refresh', action='store_true', help='Refresh vault, metrics and inbound observations with existing authority')
    parser.add_argument('--readback', action='store_true', help='Verify existing provider IDs; never send')
    parser.add_argument('--show-saved', action='store_true', help='Print diagnostics from the existing report without rerunning or overwriting it')
    args = parser.parse_args(argv)
    if args.observe_only and (args.refresh or args.readback):
        parser.error('--observe-only cannot be combined with network observation')
    if args.show_saved and (args.observe_only or args.refresh or args.readback):
        parser.error('--show-saved cannot be combined with collection options')
    sys.path.insert(0, str(ROOT / 'src'))
    from ocpf_post import local_store
    from ocpf_post.state import state_dir
    from ocpf_post.operating_cycles import outcome
    from ocpf_post.open_work_summary import summary
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    rows = []
    destination = state_dir() / 'operating-checks.json'
    if args.show_saved:
        if not destination.exists():
            parser.error('No saved operating-checks.json exists')
        print(json.dumps(summary(local_store.read(destination), destination), indent=2))
        return
    def step(name, command, seconds=180, accepted=(0,)):
        try:
            process = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=seconds)
            value = json.loads(process.stdout)
            if not isinstance(value, dict):
                raise ValueError('Expected structured observation')
            status, counts = outcome(value)
            row = {'step': name, 'exit_code': process.returncode,
                   'execution_ok': process.returncode in accepted, 'status': status,
                   'outcome_counts': counts, 'result': value}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            row = {'step': name, 'execution_ok': False, 'status': 'unavailable',
                   'result': {'status': 'unavailable', 'error_type': type(exc).__name__}}
        rows.append(row)
        print(json.dumps({'step': name, 'status': row['status'], 'execution_ok': row['execution_ok']},
                         indent=2), flush=True)
        local_store.write(destination, {'schema_version': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'steps': rows, 'boundary': 'Open-work observations only. No feed setup, established application check, new schedule, publication, failed-send retry, capacity change or Metricool.'})
    if args.refresh:
        step('vault-refresh', [CLI, 'vault', 'sync', '--apply'], 480)
        step('metrics-due', [CLI, 'performance', 'capture-due', '--apply'])
        step('inbound-replies', [CLI, 'engagement', 'sync', '--apply'], 90)
    if args.readback:
        step('reply-readback', [CLI, 'engagement', 'reconcile', '--apply'])
        step('additional-account-readiness', [sys.executable, '-m', 'ocpf_post.open_work', 'accounts'])
        step('campaign-readback', [sys.executable, '-m', 'ocpf_post.open_work', 'readback', '--apply'])
        step('current-delivery-gates', [sys.executable, '-m', 'ocpf_post.delivery_gates'])
    step('timer-and-publishing-health', [CLI, 'health', '--json'], accepted=(0, 1, 2, 3))
    step('current-queue-replay', [sys.executable, '-m', 'ocpf_post.open_work', 'queue'], 240)
    step('open-delivery-and-operational-evidence', [sys.executable, '-m', 'ocpf_post.open_work', 'report'], 240)
    print(json.dumps(summary(local_store.read(destination), destination), indent=2))


if __name__ == '__main__':
    main()
