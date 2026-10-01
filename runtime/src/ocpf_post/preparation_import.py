"""Explicit owner-reviewed handoff of one approved cloud variant to local copy.

No model call, allocation, schedule or provider effect. The local review is the
authority: an exported JSON file is not a signed assertion of cloud provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from ocpf_post.onboarding import OnboardingError, import_campaign, read_input
from ocpf_post.poststeward_cloud import CloudError, bindings
from ocpf_post.registry import resolve_account


def import_preparation(path, *, project: str, campaign: str, variant: str,
                       account: str, apply=False, expected_sha256=None):
    exported, file_digest = read_input(path)
    if not isinstance(exported, dict) or not isinstance(exported.get('preparation'), dict):
        raise OnboardingError('Use the private preparation export downloaded after owner approval')
    job = exported['preparation']
    if job.get('status') != 'handed_off' or not job.get('campaign'):
        raise OnboardingError('Only owner-approved, handed-off copy can be imported')
    cloud = bindings()  # Read-only paired workspace/account evidence; no provider I/O.
    if exported.get('workspace') != cloud.get('workspace'):
        raise OnboardingError('Export belongs to a different workspace than this paired machine')
    drafts = [row for row in job.get('drafts', []) if isinstance(row, dict) and row.get('alias') == variant]
    channels = [row for row in job.get('channels', []) if isinstance(row, dict) and row.get('alias') == variant]
    if len(drafts) != 1 or len(channels) != 1:
        raise OnboardingError('Choose one included approved variant by its exact cloud account alias')
    channel = channels[0]
    provider = channel.get('provider')
    if provider not in {'x', 'threads', 'linkedin'}:
        raise OnboardingError('Unsupported preparation destination')
    matches = [row for row in cloud.get('accounts', []) if row.get('alias') == variant and row.get('active') is True
               and row.get('provider') == provider and row.get('version') == channel.get('binding')
               and row.get('identity', {}).get('id') == channel.get('identityId')]
    target = resolve_account(project, account, expected_provider=provider)
    if len(matches) != 1 or target.get('account_id') != channel.get('identityId'):
        raise OnboardingError('Destination binding changed or local account differs from the approved stable identity')
    value = {'schema_version': 1, 'campaign': campaign, 'project': project,
             'title': 'Owner-reviewed release preparation', 'status': 'COPY-READY',
             'destinations': {provider: account}, 'expected_identities':{provider:target['account_id']}, 'texts': {provider: drafts[0].get('text')},
             'source': {'type': 'owner_approved', 'source_id': 'poststeward-preparation:' + str(job.get('id'))[:100]}}
    review = {'file_sha256': file_digest, 'project': project, 'campaign': campaign,
              'variant': variant, 'account': account, 'identity': target.get('account_id'), 'input': value}
    review_digest = hashlib.sha256(json.dumps(review, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    if apply and expected_sha256 != review_digest:
        raise OnboardingError('Preparation or destination changed since preview; review the current SHA-256')
    with tempfile.TemporaryDirectory(prefix='poststeward-preparation-') as temporary:
        candidate = Path(temporary) / 'campaign.json'
        candidate.write_text(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False))
        candidate.chmod(0o600)
        preview = import_campaign(candidate)
        result = import_campaign(candidate, apply=True, expected_sha256=preview['input_sha256']) if apply else preview
    return {**result, 'review_sha256': review_digest, 'input_sha256': review_digest,
            'preparation': job.get('id'), 'cloud_campaign': job['campaign'],
            'boundary': 'Exact selected copy imported manual-only after local review. No model, allocation, schedule or provider effect. Export provenance is not independently authenticated.'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='poststeward preparation')
    commands = parser.add_subparsers(dest='action', required=True)
    command = commands.add_parser('import')
    for name in ('file', 'project', 'campaign', 'variant', 'account'):
        command.add_argument('--' + name, required=True)
    command.add_argument('--apply', action='store_true')
    command.add_argument('--expected-sha256')
    args = parser.parse_args(argv)
    try:
        result = import_preparation(args.file, project=args.project, campaign=args.campaign,
            variant=args.variant, account=args.account, apply=args.apply, expected_sha256=args.expected_sha256)
    except (ValueError, OSError, CloudError) as error:
        print(json.dumps({'error': {'code': 'PREPARATION_IMPORT_BLOCKED', 'message': str(error)}}))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
