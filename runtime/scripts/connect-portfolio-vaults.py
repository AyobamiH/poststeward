#!/usr/bin/env python3
"""Preview or activate the 11 reviewed portfolio vault policies; never publish."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'ocpf-post'
PROJECTS = (
    'agent-shop-products', 'agrolink-nigeria', 'capability-intelligence',
    'coding-agent-skills', 'global-bill-forge', 'lovable-architecture-auditor',
    'oneclick-chatgpt-plugin', 'openclaw-operator',
    'public-decision-intelligence', 'relay-live-business-engagement',
    'tail-wagging-websites',
)


def run(*args):
    result = subprocess.run([str(CLI), *args], cwd=ROOT, check=True,
                            text=True, capture_output=True)
    value = json.loads(result.stdout)
    print(json.dumps(value, ensure_ascii=False), flush=True)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='Register and enable exact reviewed policies, then sync their Docs')
    args = parser.parse_args()
    # Preview every policy before any registration, catching authority conflicts.
    previews = []
    for project in PROJECTS:
        path = str(ROOT / 'examples' / 'portfolio-vaults' / (project + '.json'))
        preview = run('vault', 'register', '--file', path, '--enable')
        previews.append((path, preview['input_sha256'], preview['policy']['id']))
    if not args.apply:
        print('Preview only: no credentials, registrations, imports or publications changed.')
        return
    if not run('vault', 'status')['credential_present']:
        raise SystemExit('Host Google credential is missing. Use the existing vault auth flow locally; never send credentials in chat.')
    for path, sha, _ in previews:
        run('vault', 'register', '--file', path, '--enable', '--apply', '--expected-sha256', sha)
    failures = []
    for _, _, identifier in previews:
        result = run('vault', 'sync', '--vault-id', identifier, '--apply')
        if len(result['vaults']) != 1 or result['vaults'][0]['result'] != 'synced':
            failures.append(identifier)
    if failures:
        raise SystemExit('Sync incomplete for: ' + ', '.join(failures) + '. Inspect the reports; rerunning is idempotent.')
    print('Eleven policies enabled and Docs synced. The existing allocator decides schedules within its current limits. No direct publication was requested.')


if __name__ == '__main__':
    main()
