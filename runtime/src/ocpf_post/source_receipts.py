"""Local generated-source milestone evidence across campaign revisions."""
from __future__ import annotations

from datetime import datetime, timezone

from ocpf_post.campaigns import builtin_manifest, campaign_ids
from ocpf_post.scheduler import schedule_records
from ocpf_post.schedule_semantics import classify_schedule
from ocpf_post.state import iter_receipts
from ocpf_post.source_evidence import _configured

PUBLISHED = {'published_verified', 'published_unverified'}


def publication_inputs():
    """One strict local input set shared by a portfolio observation."""
    from ocpf_post.health import read_log, fold_schedules
    from ocpf_post.state import state_dir
    fold_schedules(read_log(state_dir() / 'schedule-events.jsonl'))
    read_log(state_dir() / 'publish-receipts.jsonl')
    return ({cid: builtin_manifest(cid) for cid in campaign_ids()}, list(schedule_records()), list(iter_receipts()))


def source_receipts(project, *, brief_id=None, reviewed_briefs=False, reviewed_vaults=False, inputs=None, now=None):
    if reviewed_briefs and reviewed_vaults:
        raise ValueError('Select one publication origin')
    observed_at = now or datetime.now(timezone.utc)
    profile, pinned_id = _configured(project) if not (reviewed_briefs or reviewed_vaults) else ({}, None)
    all_manifests, all_schedules, all_receipts = inputs if inputs is not None else publication_inputs()
    manifests = {}
    for cid, manifest in all_manifests.items():
        source = manifest.get('source') or {}
        if reviewed_vaults:
            vault = manifest.get('vault') or {}
            selected = (manifest.get('runtime_imported') is True and all(vault.get(k) for k in
                        ('id', 'document_id', 'key', 'base_campaign', 'revision')))
        elif reviewed_briefs:
            selected = (bool(manifest.get('evidence_brief')) and manifest.get('runtime_imported') is True
                        and (brief_id is None or manifest['evidence_brief'].get('brief_id') == brief_id))
        else:
            selected = manifest.get('runtime_generated') is True and source.get('repository') == profile['repository']
        if manifest.get('project') == project and selected:
            activation = manifest.get('runtime_source') or {}
            if pinned_id is not None and activation.get('repository_id') != pinned_id:
                continue
            manifests[cid] = manifest
    schedules = [r for r in all_schedules if r.get('campaign') in manifests]
    receipts = [r for r in all_receipts if r.get('campaign') in manifests]
    observations = []
    for schedule in schedules:
        if schedule.get('status') not in PUBLISHED or not schedule.get('post_id'):
            continue
        matching = []
        for receipt in receipts:
            if receipt.get('status') not in PUBLISHED:
                continue
            fields = ('campaign', 'provider', 'account_id', 'text_sha256', 'post_id')
            if any(not schedule.get(k) or receipt.get(k) != schedule[k] for k in fields):
                continue
            if receipt.get('schedule_id') and receipt['schedule_id'] != schedule['schedule_id']:
                continue
            matching.append(receipt)
        if not matching:
            continue
        receipt = next((r for r in reversed(matching) if r.get('status') == 'published_verified' and r.get('readback_verified') is True), matching[-1])
        meaning = classify_schedule(schedule, now=observed_at)
        ledger_verified = (receipt.get('status') == 'published_verified' and receipt.get('readback_verified') is True
                           and schedule.get('status') == 'published_verified' and schedule.get('readback_verified') is True)
        verified = ledger_verified or meaning.readback_verified
        manifest = manifests[schedule['campaign']]
        observations.append({
            'campaign': schedule['campaign'], 'schedule_id': schedule['schedule_id'],
            'provider': schedule['provider'], 'account_id': schedule['account_id'],
            'text_sha256': schedule['text_sha256'], 'post_id': schedule['post_id'],
            'url': receipt.get('url') or schedule.get('url'), 'run_at': schedule.get('run_at'),
            'recorded_at': receipt.get('recorded_at'), 'readback_verified': verified,
            'effective_schedule_state': meaning.state,
            'receipt_status': receipt['status'], 'source': manifest.get('source'),
            'vault': manifest.get('vault'),
            'runtime_activation': (manifest.get('runtime_source') or {}).get('id'),
            'link': 'explicit_schedule_id' if receipt.get('schedule_id') else 'legacy_schedule_post_identity_hash_match',
        })

    def schedule_state(row):
        meaning = classify_schedule(row, now=observed_at)
        result = {k: row.get(k) for k in ('campaign', 'schedule_id', 'provider', 'status', 'run_at')}
        result.update(effective_state=meaning.state, readback_verified=meaning.readback_verified)
        return result

    return {
        'schema_version': 1, 'project': project, 'observed_at': observed_at.isoformat(),
        'milestone': 'vault_scheduled_publication_receipt' if reviewed_vaults else 'reviewed_brief_scheduled_publication_receipt' if reviewed_briefs else 'generated_campaign_scheduled_publication_receipt',
        'status': 'observed' if observations else 'pending',
        ('vault_campaign_count' if reviewed_vaults else 'reviewed_campaign_count' if reviewed_briefs else 'generated_campaign_count'): len(manifests),
        'scheduled_publications': observations,
        'readback_verified_count': sum(r['readback_verified'] for r in observations),
        'schedule_states': [schedule_state(r) for r in schedules],
        'unmatched_publication_receipt_count': sum(1 for r in receipts if r.get('status') in PUBLISHED and not any(
            r.get('campaign') == o['campaign'] and r.get('provider') == o['provider']
            and r.get('account_id') == o['account_id'] and r.get('post_id') == o['post_id']
            and r.get('text_sha256') == o['text_sha256']
            and (not r.get('schedule_id') or r['schedule_id'] == o['schedule_id']) for o in observations)),
        'boundary': ('Vault campaigns only; direct publication does not complete this milestone. ' if reviewed_vaults else 'Reviewed brief campaigns only; direct publication does not complete this milestone. ' if reviewed_briefs else 'Generated source campaigns only. ') + 'Local ledger observation across all campaign revisions, including superseded campaigns. Publication requires an exact matching schedule and receipt by campaign, provider, account, payload hash and post ID; persisted separate readback may verify that exact identity without rewriting either ledger. No provider call, retry, publication or source activation occurs here. Pending is not failure.',
    }
