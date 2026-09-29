from __future__ import annotations

import argparse
import json
import sys

from ocpf_post.registry import RegistryError, load_registry, project_summary, resolve_account


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def cmd_list(_args: argparse.Namespace) -> None:
    data = load_registry()
    projects = data.get("projects", {})
    out = []
    for project_id in sorted(projects):
        value = projects[project_id]
        out.append({
            "project": project_id,
            "label": value.get("label") if isinstance(value, dict) else None,
            "campaign_prefixes": value.get("campaign_prefixes", []) if isinstance(value, dict) else [],
        })
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_show(args: argparse.Namespace) -> None:
    try:
        print(json.dumps(project_summary(args.project), indent=2, ensure_ascii=False))
    except RegistryError as exc:
        _die(str(exc), 4)


def cmd_resolve(args: argparse.Namespace) -> None:
    try:
        value = resolve_account(args.project, args.account, expected_provider=args.provider)
    except RegistryError as exc:
        _die(str(exc), 4)
    print(json.dumps(value, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocpf-post registry")
    sub = parser.add_subparsers(dest="command", required=True)

    from ocpf_post.onboarding import add_import_arguments, cmd_import_project
    import_cmd = sub.add_parser("import", help="Preview or add a runtime project and expected account bindings")
    add_import_arguments(import_cmd)
    import_cmd.set_defaults(func=cmd_import_project)

    list_cmd = sub.add_parser("list", help="List registered projects")
    list_cmd.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="Show one project and its stable account aliases")
    show.add_argument("--project", required=True)
    show.set_defaults(func=cmd_show)

    resolve = sub.add_parser("resolve", help="Resolve one account alias deterministically")
    resolve.add_argument("--project", required=True)
    resolve.add_argument("--account", required=True)
    resolve.add_argument("--provider")
    resolve.set_defaults(func=cmd_resolve)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
