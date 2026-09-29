"""Explicit receipt-linked commercial observations; no inferred conversions."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import re

from ocpf_post import local_store
from ocpf_post.state import state_dir
from ocpf_post.engagement import digest, at
from ocpf_post.performance_review import publications


def path():
    return state_dir() / 'business-outcomes.json'


def _validate_document(data, *, now=None, expected_source=None, allow_empty=False):
    if not isinstance(data, dict) or set(data) != {'schema_version', 'source', 'events'} or data['schema_version'] != 1:
        raise ValueError('Invalid outcome document')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,79}', str(data.get('source') or '')):
        raise ValueError('Invalid outcome source')
    if expected_source is not None and data['source'] != expected_source:
        raise ValueError('Outcome source does not match authorised connector')
    if not isinstance(data.get('events'), list) or not 0 <= len(data['events']) <= 1000:
        raise ValueError('Invalid outcome event count')
    if not allow_empty and not data['events']:
        raise ValueError('Outcome document requires at least one event')
    now = now or datetime.now(timezone.utc)
    pub = publications()
    events = {}
    required = {'event_id', 'campaign', 'provider', 'account_id', 'post_id', 'occurred_at',
                'event_type', 'evidence_reference', 'revenue_minor', 'currency'}
    for event in data['events']:
        if not isinstance(event, dict) or set(event) != required:
            raise ValueError('Invalid outcome event')
        if not all(isinstance(event[k], str) and 0 < len(event[k]) <= 200
                   for k in required - {'revenue_minor', 'currency'}):
            raise ValueError('Expected bounded event identities')
        identity = tuple(event[k] for k in ('campaign', 'provider', 'account_id', 'post_id'))
        publication = pub.get(identity)
        if not publication or publication.get('effective_verified') is not True:
            raise ValueError('Exact verified publication required')
        if not publication['at'] <= at(event['occurred_at']) <= now:
            raise ValueError('Outcome outside publication/current time')
        if event['event_type'] not in {'enquiry', 'signup', 'sale'}:
            raise ValueError('Invalid outcome type')
        if event['event_type'] == 'sale':
            if (type(event['revenue_minor']) is not int
                    or not 0 <= event['revenue_minor'] <= 10**12
                    or not isinstance(event['currency'], str)
                    or not re.fullmatch('[A-Z]{3}', event['currency'])):
                raise ValueError('Sale requires nonnegative minor units and currency')
        elif event['revenue_minor'] is not None or event['currency'] is not None:
            raise ValueError('Revenue belongs only to sales')
        key = digest([data['source'], event['event_id']])
        value = {**event, 'source': data['source']}
        if key in events and events[key] != value:
            raise ValueError('Conflicting event identity')
        events[key] = value
    return events, digest(data)


def _commit(events):
    with local_store.locked(path()):
        current = local_store.read(path()) or {'schema_version': 1, 'events': {}}
        if current.get('schema_version') != 1 or not isinstance(current.get('events'), dict):
            raise ValueError('Invalid outcome state')
        for key, event in events.items():
            if key in current['events'] and current['events'][key] != event:
                raise ValueError('Existing event cannot be reassigned or overwritten')
        current['events'].update(events)
        local_store.write(path(), current)


def ingest_document(data, *, apply=False, expected_sha256=None, now=None,
                    expected_source=None, authority='reviewed_file'):
    events, review = _validate_document(
        data, now=now, expected_source=expected_source,
        allow_empty=authority == 'registered_connector',
    )
    if apply:
        if authority == 'reviewed_file':
            if expected_sha256 != review:
                raise ValueError('Apply requires the exact reviewed input hash')
        elif authority != 'registered_connector':
            raise ValueError('Unknown outcome import authority')
        _commit(events)
    return {
        'schema_version': 1,
        'result': 'imported' if apply else 'preview',
        'input_sha256': review,
        'event_count': len(events),
        'authority': authority,
        'boundary': (
            'Receipt-linked descriptive business observations only. Reviewed files require exact CAS; '
            'registered connectors have separate endpoint/credential activation. Events must still match '
            'an exact verified publication. No contact/customer data, currency conversion, copy changes, '
            'quota changes, causal attribution or automatic learning winner.'
        ),
    }


def ingest(filename, *, apply=False, expected_sha256=None, now=None):
    file = Path(filename)
    if file.stat().st_size > 1000000:
        raise ValueError('Outcome input too large')
    data = json.loads(file.read_text())
    return ingest_document(
        data, apply=apply, expected_sha256=expected_sha256, now=now,
        authority='reviewed_file',
    )


def ingest_authorized(data, *, expected_source, now=None):
    return ingest_document(
        data, apply=True, now=now, expected_source=expected_source,
        authority='registered_connector',
    )


def report():
    data = local_store.read(path())
    rows = list(data.get('events', {}).values())
    revenue = Counter()
    for row in rows:
        if row['event_type'] == 'sale':
            revenue[row['currency']] += row['revenue_minor']
    return {'schema_version': 1, 'status': 'observed' if rows else 'not_connected',
            'event_counts': dict(Counter(row['event_type'] for row in rows)),
            'revenue_minor_by_currency': dict(revenue), 'events': rows,
            'boundary': 'Only explicitly imported or authorised-connector receipt-linked events. Missing analytics stay unknown; no causal attribution or automatic rewriting of vaults, briefs or replies.'}


def add_parsers(sub):
    from ocpf_post.onboarding import _run_cli
    parser = sub.add_parser('outcomes')
    parser.add_argument('--file'); parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-sha256')
    def run(args):
        if not args.file and (args.apply or args.expected_sha256):
            raise ValueError('Outcome import requires a file')
        return ingest(args.file, apply=args.apply, expected_sha256=args.expected_sha256) if args.file else report()
    parser.set_defaults(func=lambda a: _run_cli(lambda: run(a)))
