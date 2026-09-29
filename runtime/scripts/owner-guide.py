#!/usr/bin/env python3
"""List every native owner command by level, or check the committed guide.

Imports the command catalogue, not live state or parser action functions. This
helper never executes the commands it lists. Only --write edits documentation.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ocpf_post.cli_catalog import COMMANDS, Command  # noqa: E402

START = "<!-- BEGIN OWNER COMMAND INDEX -->"
END = "<!-- END OWNER COMMAND INDEX -->"
GUIDE = ROOT / "docs" / "OWNER_COMMANDS.md"

# Runnable inspection examples after the guide's setup/placeholders are filled.
# Calendar is the one optional-report writer here: the example omits --save.
BEGINNER = {
    "health": "./ocpf-post health",
    "doctor": "./ocpf-post doctor",
    "work status": "./ocpf-post work status --json",
    "portfolio volume-audit": './ocpf-post portfolio volume-audit --date "$TODAY" --timezone Europe/London --json',
    "portfolio calendar": "./ocpf-post portfolio calendar --horizon-minutes 1440",
    "schedule list": "./ocpf-post schedule list",
    "portfolio status": "./ocpf-post portfolio status",
    "replenish status": "./ocpf-post replenish status",
    "vault status": "./ocpf-post vault status",
    "vault coverage": "./ocpf-post vault coverage --inventory docs/gtm/vault-expansion-20260910/inventory.json",
    "engagement status": "./ocpf-post engagement status",
    "engagement worker-status": "./ocpf-post engagement worker-status",
    "registry list": "./ocpf-post registry list",
    "accounts list": "./ocpf-post accounts list",
    "campaign show": './ocpf-post campaign show --campaign "$CAMPAIGN" --provider "$PROVIDER"',
    "campaign explain": './ocpf-post campaign explain --campaign "$CAMPAIGN" --provider "$PROVIDER" --json',
    "receipt": './ocpf-post receipt --campaign "$CAMPAIGN" --provider "$PROVIDER"',
    "schedule inspect": './ocpf-post schedule inspect "$SCHEDULE" --json',
    "help": "./ocpf-post help",
}
GROUPS = (
    ("READ_ONLY", "Advanced inspection: deeper or provider-facing reads"),
    ("LOCAL_STATE_WRITE", "Advanced: local evidence and report writes"),
    ("FUTURE_CONSEQUENCE", "Advanced: future scheduling and admission changes"),
    ("EXTERNAL_PROVIDER_EFFECT", "Advanced: publication, replies and notification delivery"),
    ("AUTHORITY_CHANGE", "Advanced: credentials, account authority and runtime maintenance"),
)


def cell(value: str) -> str:
    return value.replace("|", "&#124;").replace("\n", " ")


def render_index(commands: tuple[Command, ...] = COMMANDS, level: str = "all") -> str:
    by_path = {command.path: command for command in commands}
    if len(by_path) != len(commands) or not BEGINNER.keys() <= by_path.keys():
        raise ValueError("Duplicate catalogue entry or removed beginner command; review the guide")
    if any(command.consequence not in dict(GROUPS) for command in commands):
        raise ValueError("Unclassified consequence; review the guide")
    for path in BEGINNER:
        command = by_path[path]
        if command.consequence != "READ_ONLY" and not (
            path == "portfolio calendar" and command.safe_form == "omit --save"
        ):
            raise ValueError("A beginner command's safety contract changed; review the guide")
    if level not in {"all", "beginner", "advanced"}:
        raise ValueError("Unknown command level")
    beginner_count = len(BEGINNER)
    lines = [
        f"Native catalogue coverage: **{len(commands)} commands: {beginner_count} beginner and {len(commands) - beginner_count} advanced**.",
        "The repository report/guide helpers and shell commands are additional tools, not new native CLI commands.",
        "",
    ]
    if level in {"all", "beginner"}:
        lines += ["### Beginner command list", "", "Use the setup and exact placeholders explained above. These examples inspect rather than publish.", "", "| Command | Purpose from the CLI catalogue | Safety of this example |", "| --- | --- | --- |"]
        for path, example in BEGINNER.items():
            command = by_path[path]
            safety = "Inspect only; omit --save" if path == "portfolio calendar" else "READ_ONLY"
            lines.append(f"| `{cell(example)}` | {cell(command.summary)} | {safety} |")
        lines.append("")
    if level in {"all", "advanced"}:
        lines += ["### Advanced command list", "", "These are **command names**, not a run-all script. Required IDs, files and arguments are intentionally not invented. Inspect the exact form with `./ocpf-post help COMMAND_PATH` before using a row. The safety column comes from the native catalogue; no safe form means do not assume a preview exists.", ""]
        for consequence, title in GROUPS:
            lines += [f"#### {title}", "", f"Native consequence class: `{consequence}`.", "", "| Command name | Purpose from the CLI catalogue | Inspection form or warning |", "| --- | --- | --- |"]
            for command in sorted(commands, key=lambda item: item.path):
                if command.path in BEGINNER or command.consequence != consequence:
                    continue
                safety = command.safe_form or (
                    "Read only; may access provider or local state" if consequence == "READ_ONLY"
                    else "No preview declared; read help and review before execution"
                )
                lines.append(f"| `./ocpf-post {cell(command.path)}` | {cell(command.summary)} | {cell(safety)} |")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def refreshed(text: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("Owner guide must contain exactly one generated-index marker pair")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    return before + START + "\n\n" + render_index() + "\n" + END + after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", choices=("beginner", "advanced", "all"), default="all")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Fail if the committed index differs from the current CLI catalogue")
    mode.add_argument("--write", action="store_true", help="Regenerate only the guide's command-index block")
    args = parser.parse_args(argv)
    if args.check or args.write:
        if args.level != "all":
            parser.error("--check/--write always covers all commands; omit --level")
        original = GUIDE.read_text(encoding="utf-8")
        updated = refreshed(original)
        if args.write:
            GUIDE.write_text(updated, encoding="utf-8")
        elif original != updated:
            print("OWNER_GUIDE=STALE: run python3 scripts/owner-guide.py --write", file=sys.stderr)
            return 1
        print(f"OWNER_GUIDE=PASS commands={len(COMMANDS)}")
    else:
        print(render_index(level=args.level), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
