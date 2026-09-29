"""Account onboarding never copies campaigns or sends a social post."""
import json
import sys
from pathlib import Path

from ocpf_post import account_profiles as accounts


def command(args):
    try:
        action = args.account_command
        if action == 'list':
            result = accounts.status()
        elif action == 'import':
            path = Path(args.file).expanduser()
            if path.stat().st_size > 65536:
                raise ValueError('Account import exceeds 64 KB')
            result = accounts.register(json.loads(path.read_text()), apply=args.apply, expected_sha256=args.expected_sha256)
        elif action == 'connect':
            result = accounts.connect(
                args.provider, args.account_id,
                credential_file=args.credential_file,
                oauth=args.oauth,
                client_id=args.client_id,
                reuse_default=args.reuse_default,
            )
        else:
            result = accounts.activation(args.provider, args.account_id, apply=getattr(args, 'apply', False),
                                         expected_sha256=getattr(args, 'expected_sha256', None), disable=action == 'disable')
        print(json.dumps(result, indent=2))
    except Exception as exc:
        # Provider bodies, token exchange URLs and credential content are never CLI diagnostics.
        print('Error: account operation failed (' + type(exc).__name__ + '). Check the private file, registered identity, connection and review hash.', file=sys.stderr)
        raise SystemExit(2) from None


def add_parser(sub):
    parser = sub.add_parser('accounts', help='Connect additional X/Threads/LinkedIn identities with independent budgets')
    actions = parser.add_subparsers(dest='account_command', required=True)
    for action in ('list', 'import', 'connect', 'enable', 'disable'):
        cmd = actions.add_parser(action)
        cmd.set_defaults(func=command)
        if action == 'import':
            cmd.add_argument('--file', required=True)
        if action in {'import', 'enable'}:
            cmd.add_argument('--apply', action='store_true')
            cmd.add_argument('--expected-sha256')
        if action in {'connect', 'enable', 'disable'}:
            cmd.add_argument('--provider', required=True, choices=['x', 'threads', 'linkedin'])
            cmd.add_argument('--account-id', required=True)
        if action == 'connect':
            method = cmd.add_mutually_exclusive_group(required=True)
            method.add_argument('--credential-file', help='Private JSON; never paste credentials into chat')
            method.add_argument('--oauth', action='store_true', help='X desktop PKCE consent')
            method.add_argument(
                '--reuse-default',
                action='store_true',
                help='LinkedIn Page only: reuse the existing default member credential by non-secret reference',
            )
            cmd.add_argument('--client-id')
