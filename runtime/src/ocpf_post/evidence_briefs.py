"""Reviewed evidence/angle inputs compiled through the existing campaign importer."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from ocpf_post import onboarding as imports
from ocpf_post.campaigns import builtin_manifest, builtin_text, campaign_ids
from ocpf_post.source_evidence import REPOSITORY

KINDS = {'source', 'tests', 'deployment', 'runtime'}


def _list(value, name, maximum):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise imports.OnboardingError(f'{name} must contain 1 to {maximum} entries')
    return value


def _refs(value, known, name):
    _list(value, name, 16)
    if any(not isinstance(key, str) or key not in known for key in value) or len(set(value)) != len(value):
        raise imports.OnboardingError(f'{name} contains unknown or repeated references')
    return value


def _words(text):
    return re.findall(r'\w+', text.casefold(), flags=re.UNICODE)


def _url(value):
    imports._text(value, 'evidence URL', 1000)
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise imports.OnboardingError('Evidence URLs must be HTTPS without credentials, queries or fragments')
    return value


def prepare_brief(value, digest, *, allocate=False, now=None):
    now = now or datetime.now(timezone.utc)
    fields = {'schema_version', 'brief_id', 'project', 'repository', 'revision', 'claim_boundary',
              'review_notes', 'evidence', 'claims', 'angles'}
    imports._object(value, fields, fields, 'evidence brief')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise imports.OnboardingError('Unsupported evidence brief schema')
    imports._slug(value['brief_id'], 'brief ID')
    imports._slug(value['project'], 'project')
    if not isinstance(value['repository'], str) or not REPOSITORY.fullmatch(value['repository']):
        raise imports.OnboardingError('Repository must be a GitHub owner/name')
    if not isinstance(value['revision'], str) or not re.fullmatch('[a-f0-9]{40}', value['revision']):
        raise imports.OnboardingError('Evidence requires one exact full commit revision')
    for field in ('claim_boundary', 'review_notes'):
        imports._text(value[field], field, 1000)
    evidence = {}
    for row in _list(value['evidence'], 'evidence', 16):
        fields = {'id', 'kind', 'revision', 'url', 'observed_at', 'summary', 'scope'}
        imports._object(row, fields, fields, 'evidence record')
        imports._slug(row['id'], 'evidence ID')
        if row['id'] in evidence or not isinstance(row['kind'], str) or row['kind'] not in KINDS:
            raise imports.OnboardingError('Repeated evidence ID or unsupported evidence kind')
        if row['revision'] != value['revision']:
            raise imports.OnboardingError('Evidence revision must match the brief revision')
        _url(row['url'])
        if imports._timestamp(row['observed_at'], 'observed_at') > now:
            raise imports.OnboardingError('Evidence observation cannot be in the future')
        for field in ('summary', 'scope'):
            imports._text(row[field], field, 1000)
        evidence[row['id']] = row
    claims = {}
    for row in _list(value['claims'], 'claims', 16):
        fields = {'id', 'text', 'kind', 'evidence_ids'}
        imports._object(row, fields, fields, 'claim')
        imports._slug(row['id'], 'claim ID')
        imports._text(row['text'], 'claim text', 1500)
        if row['id'] in claims or not isinstance(row['kind'], str) or row['kind'] not in KINDS:
            raise imports.OnboardingError('Repeated claim ID or unsupported claim kind')
        refs = _refs(row['evidence_ids'], evidence, 'claim evidence')
        if not any(evidence[key]['kind'] == row['kind'] for key in refs):
            raise imports.OnboardingError('Claim requires evidence of its own kind; source or CI cannot promote it')
        claims[row['id']] = row
    packages, ids, purposes = [], set(), set()
    for angle in _list(value['angles'], 'angles', 6):
        fields = {'id', 'campaign', 'title', 'audience', 'purpose', 'destinations', 'paragraphs', 'allocation'}
        imports._object(angle, fields, fields - {'allocation'}, 'angle')
        imports._slug(angle['id'], 'angle ID')
        for field in ('audience', 'purpose'):
            imports._text(angle[field], field, 500)
        purpose = tuple(sorted(_words(angle['purpose'])))
        if angle['id'] in ids or purpose in purposes:
            raise imports.OnboardingError('Angles must have distinct IDs and stated purposes')
        ids.add(angle['id']); purposes.add(purpose)
        if not isinstance(angle['paragraphs'], dict) or set(angle['paragraphs']) - imports.PROVIDERS:
            raise imports.OnboardingError('Angle paragraphs must be keyed by supported provider')
        texts, used = {}, {}
        for provider, paragraphs in angle['paragraphs'].items():
            rendered, refs = [], []
            for part in _list(paragraphs, 'paragraphs', 8):
                if not isinstance(part, dict) or set(part) not in ({'text'}, {'claim'}):
                    raise imports.OnboardingError('Each paragraph must contain exactly text or a claim reference')
                if 'claim' in part:
                    key = part['claim']
                    if not isinstance(key, str) or key not in claims:
                        raise imports.OnboardingError('Unknown paragraph claim')
                    rendered.append(claims[key]['text']); refs.append(key)
                else:
                    rendered.append(imports._text(part['text'], 'editorial paragraph', 1500))
            if not refs:
                raise imports.OnboardingError('Each platform copy must include an evidence-linked claim')
            texts[provider] = '\n\n'.join(rendered)
            used[provider] = sorted(set(refs))
        raw = {'schema_version': 1, 'campaign': angle['campaign'], 'project': value['project'],
               'title': angle['title'], 'status': 'COPY-READY', 'destinations': angle['destinations'], 'texts': texts,
               'source': {'type': 'owner_approved', 'source_id': f"brief:{value['project']}:{value['brief_id']}"}}
        if 'allocation' in angle:
            raw['allocation'] = angle['allocation']
        manifest, texts = imports.validate_campaign(raw, allocate=allocate, now=now)
        manifest['claim_boundary'] = value['claim_boundary']
        manifest['evidence_brief'] = {
            'schema_version': 1, 'brief_id': value['brief_id'], 'input_sha256': digest,
            'repository': value['repository'], 'revision': value['revision'],
            'evidence': value['evidence'], 'claims': value['claims'], 'review_notes': value['review_notes'],
            'angle': {k: angle[k] for k in ('id', 'audience', 'purpose')}, 'used_claims': used,
            'verification': 'operator_reviewed_references_not_independently_verified',
        }
        packages.append({'manifest': manifest, 'texts': texts})
    if len({p['manifest']['campaign'] for p in packages}) != len(packages):
        raise imports.OnboardingError('Brief repeats a campaign ID')
    return packages


def _copy_review(packages):
    """Exact/reordered words block; overlap is an explicit heuristic, not semantics."""
    comparisons, seen = [], {}
    current_ids = {p['manifest']['campaign'] for p in packages}
    for cid in campaign_ids():
        if cid in current_ids:
            continue
        manifest = builtin_manifest(cid)
        if manifest.get('project') != packages[0]['manifest']['project']:
            continue
        for provider in manifest.get('providers', []):
            text = builtin_text(cid, provider)
            if text:
                seen.setdefault(provider, []).append((cid, text))
    for package in packages:
        cid = package['manifest']['campaign']
        for provider, text in package['texts'].items():
            words = Counter(_words(text))
            for old_id, old_text in seen.get(provider, []):
                old_words = Counter(_words(old_text))
                if words == old_words:
                    raise imports.OnboardingError(f'{cid}/{provider} repeats or reorders existing copy from {old_id}')
                a, b = set(words), set(old_words)
                score = len(a & b) / max(1, len(a | b))
                if score >= .65:
                    comparisons.append({'campaign': cid, 'provider': provider, 'compared_with': old_id,
                                        'word_overlap': round(score, 3), 'review': 'high overlap; review whether this adds a distinct lesson'})
            seen.setdefault(provider, []).append((cid, text))
    return {'method': 'case-insensitive word counts and token-set Jaccard overlap',
            'similarity_warnings': comparisons, 'semantic_quality_verified': False,
            'boundary': 'Distinct purposes and evidence-linked claims require editorial review; a heuristic cannot establish usefulness or truth'}


def import_brief(path, *, apply=False, expected_sha256=None, allocate=False, now=None):
    value, digest = imports.read_input(path, apply=apply, expected_sha256=expected_sha256)
    def prepare():
        packages = prepare_brief(value, digest, allocate=allocate, now=now)
        # All conflicts checked before the first campaign is made visible.
        existing = [imports._existing_campaign(p['manifest'], p['texts']) for p in packages]
        return packages, existing, _copy_review(packages)
    if apply:
        with imports._import_lock():
            packages, existing, review = prepare()
            for package, exists in zip(packages, existing):
                if not exists:
                    imports._save_campaign(package['manifest'], package['texts'])
    else:
        packages, existing, review = prepare()
    return {'schema_version': 1, 'kind': 'evidence_brief', 'brief_id': value['brief_id'], 'input_sha256': digest,
            'apply': apply, 'result': 'already_present' if all(existing) else 'imported' if apply else 'preview',
            'campaigns': packages, 'copy_review': review, 'allocation_enabled': allocate,
            'boundary': 'Exact rendered copy and referenced evidence are reviewed together. No external evidence fetch, provider call or reservation. Each campaign is atomic; an interrupted batch can be resumed with the identical reviewed input. Allocation requires --allocate.'}


def cmd_import_brief(args):
    imports._run_cli(lambda: import_brief(args.file, apply=args.apply, expected_sha256=args.expected_sha256, allocate=args.allocate))
