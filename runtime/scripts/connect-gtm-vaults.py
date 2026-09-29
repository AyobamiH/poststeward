#!/usr/bin/env python3
"""One-time owner invocation: connect the three inspected vaults on this host."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CLI = str(ROOT / 'ocpf-post')


def run(*args):
    completed = subprocess.run([CLI, *args], cwd=ROOT, check=True, text=True, capture_output=True)
    print(completed.stdout, end='')
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-file', help='Private Google Desktop OAuth client JSON; omit when host credentials already exist')
    args = parser.parse_args()
    if args.client_file:
        # Stream the browser consent URL immediately; never capture or print tokens.
        subprocess.run([CLI, 'vault', 'auth', '--client-file', str(Path(args.client_file).expanduser().resolve())], cwd=ROOT, check=True)
    status = run('vault', 'status')
    if not status['credential_present']:
        raise SystemExit('Host Google credentials missing. Re-run with --client-file after Google Desktop OAuth setup.')
    for project in ('oneclickpostfactory', 'proof-and-state', 'parcelbasis'):
        path = str(ROOT / 'examples' / (project + '-vault.json'))
        preview = run('vault', 'register', '--file', path, '--enable')
        run('vault', 'register', '--file', path, '--enable', '--apply', '--expected-sha256', preview['input_sha256'])
    synced = run('vault', 'sync', '--apply')
    if len(synced['vaults']) < 3 or any(v['result'] != 'synced' for v in synced['vaults']):
        raise SystemExit('Not all registered vaults synced; inspect the result above. No social post was created.')
    print('Three vaults connected. Later approved entries follow the existing refill timer. No social post was created by setup.')


if __name__ == '__main__':
    main()
