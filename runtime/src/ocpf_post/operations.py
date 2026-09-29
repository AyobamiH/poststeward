"""One local report preserving all five operating milestones."""
from datetime import datetime, timezone
import json

from ocpf_post.source_receipts import source_receipts
from ocpf_post.state import state_dir, read_json
from ocpf_post.onboarding import _timestamp


def report(project, *, now=None, inputs=None, include_account_reports=True):
    from ocpf_post.vault_sync import policies, observations
    from ocpf_post.google_vault import credential_path
    now = now or datetime.now(timezone.utc)
    items = {}
    for name, kwargs in [('generated_publication', {}), ('brief_publication', {'reviewed_briefs': True}),
                         ('vault_publication', {'reviewed_vaults': True})]:
        try:
            items[name] = source_receipts(project, now=now, inputs=inputs, **kwargs)
        except (OSError, ValueError, KeyError, TypeError):
            items[name] = {'status': 'unavailable'}
    observed = observations()
    vaults = []
    for key, policy in policies().items():
        if policy['project'] != project:
            continue
        row = observed.get(key, {})
        fresh = bool(row.get('valid_until')) and now < _timestamp(row['valid_until'], 'vault expiry')
        vaults.append({'id': key, 'enabled': policy['enabled'], 'observation_fresh': fresh,
                       'version': row.get('version'), 'active_campaigns': list(row.get('active', {}).values()),
                       'skipped': row.get('skipped', [])})
    items['vault_sync'] = {'status': 'observed' if vaults and all(v['enabled'] and v['observation_fresh'] for v in vaults) else 'pending',
                            'credential_present': credential_path().exists(), 'vaults': vaults}
    application = read_json(state_dir() / 'application-evidence' / (project + '.json'))
    if application.get('expires_at') and now >= _timestamp(application['expires_at'], 'application expiry'):
        application = {**application, 'status': 'stale'}
    items['application_evidence'] = application or {'status': 'pending'}
    from ocpf_post.performance_review import review, publications
    from ocpf_post.campaigns import builtin_manifest
    groups = {}
    for key in publications():
        if builtin_manifest(key[0]).get('project') == project:
            groups.setdefault((key[1], key[2]), set()).add(key[0])
    cohorts = [review(sorted(ids)[:50], provider=p, account_id=a) for (p, a), ids in groups.items() if len(ids) >= 2]
    items['performance_review'] = {'status': 'observed' if any(c['status'] == 'comparable' for c in cohorts) else 'insufficient_evidence',
                                  'cohorts': cohorts, 'ranking_changed': False}
    from ocpf_post.queue_watch import report as queue_report
    try:
        queue = queue_report(now=now)
        items['queue_supervision'] = {'status': queue['status'], 'last_evaluated_at': queue['last_evaluated_at'],
            'issues': [r for r in queue['issues'] if r['project'] == project],
            'project_counts': [r for r in queue['by_project'] if r['project'] == project],
            'scope': 'Status describes the portfolio monitor; issues and counts are filtered to this project.'}
    except (OSError, ValueError, KeyError, TypeError):
        items['queue_supervision'] = {'status': 'unavailable'}
    from ocpf_post.capacity_experiment import report as capacity_report
    from ocpf_post.engagement import report as engagement_report
    for name, observer in ([('capacity_experiment', capacity_report), ('engagement', engagement_report)] if include_account_reports else []):
        try:
            items[name] = {**observer(now=now), 'scope': 'Portfolio/account-level observation, not project-specific completion'}
        except (OSError, ValueError, KeyError, TypeError):
            items[name] = {'status': 'unavailable'}
    return {'schema_version': 1, 'project': project, 'observed_at': now.isoformat(), 'items': items,
            'boundary': 'Local evidence only. Unknown or pending outcomes are never closed by installation, CI or another milestone.'}


def cmd_receipts(args):
    print(json.dumps(source_receipts(args.project, reviewed_briefs=True, brief_id=args.brief_id), indent=2))


def cmd_operations(args):
    from ocpf_post.onboarding import _slug
    from ocpf_post.operations_snapshot import portfolio_report, save_report
    if args.project:
        _slug(args.project, 'project')
    result = portfolio_report() if args.all_projects else report(args.project)
    print(json.dumps(save_report(result) if args.save else result, indent=2))


def add_parsers(sub):
    from ocpf_post import vault_sync, application_evidence, google_vault
    from ocpf_post.onboarding import _run_cli, add_import_arguments
    vault = sub.add_parser('vault', help='Synchronise designated approved Google Doc entries')
    commands = vault.add_subparsers(dest='vault_command', required=True)
    register = commands.add_parser('register', help='Preview or register vault authority')
    add_import_arguments(register)
    register.add_argument('--enable', action='store_true', help='Authorise future approved entries from this document')
    register.set_defaults(func=lambda a: _run_cli(lambda: vault_sync.register(a.file, apply=a.apply, expected_sha256=a.expected_sha256, enable=a.enable)))
    extend = commands.add_parser('extend', help='Review adding one provider destination to an enabled vault')
    extend.add_argument('--vault-id', required=True)
    extend.add_argument('--provider', required=True, choices=('x', 'threads', 'linkedin'))
    extend.add_argument('--account', required=True, help='Existing project-scoped account alias')
    extend.add_argument('--apply', action='store_true')
    extend.add_argument('--expected-sha256', help='Review hash binding current policy, accounts and document copy')
    extend.set_defaults(func=lambda a: _run_cli(lambda: vault_sync.extend(a.vault_id, a.provider, a.account, apply=a.apply, expected_sha256=a.expected_sha256)))
    sync = commands.add_parser('sync', help='Read approved entries and optionally reconcile local campaigns')
    sync.add_argument('--vault-id'); sync.add_argument('--apply', action='store_true')
    sync.set_defaults(func=lambda a: _run_cli(lambda: vault_sync.sync(apply=a.apply, vault_id=a.vault_id)))
    status = commands.add_parser('status', help='Read local vault policies and observations')
    status.set_defaults(func=lambda a: _run_cli(lambda: {'policies': vault_sync.policies(), 'observations': vault_sync.observations(), 'credential_present': google_vault.credential_path().exists(), 'google': google_vault.credential_capabilities()}))
    from ocpf_post.vault_coverage import coverage
    audit = commands.add_parser('coverage', help='Compare reviewed portfolio inventory with local registrations and intact imports')
    audit.add_argument('--inventory', required=True)
    audit.set_defaults(func=lambda a: _run_cli(lambda: coverage(a.inventory)))
    credentials = commands.add_parser('credentials', help='Install a private Google authorized_user credential file')
    credentials.add_argument('--file', required=True)
    credentials.set_defaults(func=lambda a: _run_cli(lambda: google_vault.install_credentials(a.file)))
    auth = commands.add_parser('auth', help='Connect Google read-only access with Desktop OAuth and PKCE')
    auth.add_argument('--client-file', required=True); auth.add_argument('--port', type=int, default=8766)
    auth.set_defaults(func=lambda a: _run_cli(lambda: google_vault.authorize(a.client_file, port=a.port)))
    evidence = sub.add_parser('evidence', help='Observe scoped application assertions')
    commands = evidence.add_subparsers(dest='evidence_command', required=True)
    check = commands.add_parser('check', help='Preview or execute a revision-bound HTTPS JSON check')
    add_import_arguments(check); check.add_argument('--sha', required=True)
    check.set_defaults(func=lambda a: _run_cli(lambda: application_evidence.check(a.file, sha=a.sha, apply=a.apply, expected_sha256=a.expected_sha256)))
