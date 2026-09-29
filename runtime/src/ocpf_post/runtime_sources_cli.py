from __future__ import annotations

import json
import sys

from ocpf_post import runtime_sources as sources
from ocpf_post.onboarding import add_import_arguments


def run(args):
    try:
        if args.source_command == "import":
            result = sources.import_source(args.file, apply=args.apply, expected_sha256=args.expected_sha256)
        elif args.source_command == "list":
            result = sources.list_sources()
        elif args.source_command == "preview":
            result = sources.preview_source(args.project)
        elif args.source_command == "evidence":
            from ocpf_post.source_evidence import inspect_source
            result = inspect_source(args.project, sha=args.sha)
        elif args.source_command == "receipts":
            from ocpf_post.source_receipts import source_receipts
            result = source_receipts(args.project)
        elif args.source_command == "enable":
            result = sources.enable_source(args.project, expected_sha256=args.expected_sha256)
        else:
            result = sources.disable_source(args.project)
    except (ValueError, OSError, RuntimeError) as exc:
        from ocpf_post.replenisher import ReplenisherError
        from ocpf_post.onboarding import OnboardingError
        message = str(exc) if isinstance(exc, (sources.SourceError, OnboardingError, ReplenisherError)) else "Source operation failed validation or could not access local state"
        print(f"Error: {message}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def add_source_commands(parent):
    parser = parent.add_parser("source", help="Register, preview and activate runtime GitHub source policies")
    sub = parser.add_subparsers(dest="source_command", required=True)
    register = sub.add_parser("import", help="Preview or save an inactive, add-only source policy")
    add_import_arguments(register)
    register.set_defaults(func=run)
    listing = sub.add_parser("list", help="Read local runtime policies and activation state as JSON")
    listing.set_defaults(func=run)
    evidence = sub.add_parser("evidence", help="Read commit, workflow and deployment evidence without changing authority")
    evidence.add_argument("--project", required=True)
    evidence.add_argument("--sha", help="Exact full commit SHA; defaults to the observed default-branch head")
    evidence.set_defaults(func=run)
    receipts = sub.add_parser("receipts", help="Track generated campaigns through matching scheduled publication receipts")
    receipts.add_argument("--project", required=True)
    receipts.set_defaults(func=run)
    for name, help_text in (
        ("preview", "Read GitHub and show exact static copy plus the future event policy"),
        ("enable", "Authorise future replenishment using a freshly reviewed source preview"),
        ("disable", "Stop future replenishment; existing campaigns and schedules remain authorised"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--project", required=True)
        if name == "enable":
            command.add_argument("--expected-sha256", required=True, help="review_sha256 from source preview")
        command.set_defaults(func=run)
