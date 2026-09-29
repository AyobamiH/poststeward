#!/usr/bin/env python3
"""Capture/replay the host queue, optionally enable fair selection, audit vault coverage."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from ocpf_post import portfolio as base
from ocpf_post.portfolio_queue import snapshot, replay, read_snapshot, write_artifact, set_selection
from ocpf_post.vault_coverage import coverage
from ocpf_post.state import state_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-fairness', action='store_true',
                        help='After reproducible replay, change future selection only; preserve pace and reservations')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = state_dir() / 'queue-reviews' / stamp
    captured = snapshot(horizon_minutes=1440)
    write_artifact(folder / 'snapshot.json', captured)
    # Read back exactly the file the operator can supply for independent replay.
    result = replay(read_snapshot(folder / 'snapshot.json'))
    write_artifact(folder / 'replay.json', result)
    vaults = coverage(ROOT / 'docs/gtm/vault-expansion-20260910/inventory.json')
    write_artifact(folder / 'vault-coverage.json', vaults)
    if args.apply_fairness:
        if base.load_policy() != captured['policy']:
            raise base.PortfolioError('Policy changed during review; rerun before enabling fairness')
        set_selection('fair')
    summary = {
        'baseline_reproduced': result['baseline_reproduced'],
        'selection': base.load_policy().get('selection', 'legacy'),
        'policy_changed_by_command': args.apply_fairness and captured['policy'].get('selection', 'legacy') != 'fair',
        'comparison': {p: {'baseline_slots': before['selected'], 'fair_slots': result['fair_summary'][p]['selected'],
                           'baseline_projects': len(before['by_project']),
                           'fair_projects': len(result['fair_summary'][p]['by_project'])}
                       for p, before in result['baseline_summary'].items()},
        'vault_coverage': {k: vaults[k] for k in ('status', 'expected_projects', 'covered_projects', 'expected_entries', 'intact_imports')},
        'incomplete_vaults': [r['project'] for r in vaults['projects'] if r['status'] == 'incomplete'],
        'brief_and_original_vault_projection': [{k: r[k] for k in ('campaign', 'provider', 'projected_run_at', 'expiry_risk')}
                                               for r in result['waiting']['fair'] if r['campaign'].startswith(
            ('POSTONCE-BRIEF-', 'OCPF-VAULT-', 'PAS-VAULT-', 'PB-VAULT-'))],
        'evidence_directory': str(folder),
        'boundary': 'Fair selection applies at the next normal refill when enabled. Existing reservations continue. These projections are not schedules or receipts.',
    }
    write_artifact(folder / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (base.PortfolioError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f'Queue review failed: {exc}')
