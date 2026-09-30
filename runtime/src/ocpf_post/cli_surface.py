"""Introspect the complete public argparse surface.

The curated CLI catalogue owns consequence semantics. This module owns mechanical
parser truth: public command paths, flags, defaults and choices.
"""
from __future__ import annotations

import argparse
import json
from typing import Any


def _jsonable(value: Any) -> Any:
    if value is argparse.SUPPRESS:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _argument(action: argparse.Action) -> dict[str, Any] | None:
    if isinstance(action, argparse._HelpAction) or isinstance(action, argparse._SubParsersAction):
        return None
    flags = list(action.option_strings)
    positional = not flags
    choices = list(action.choices) if action.choices is not None else None
    return {
        "name": action.dest,
        "flags": flags,
        "positional": positional,
        "required": bool(getattr(action, "required", False)) or (positional and action.nargs not in ("?", "*")),
        "nargs": _jsonable(action.nargs),
        "default": _jsonable(action.default),
        "choices": [_jsonable(item) for item in choices] if choices is not None else None,
        "help": None if action.help is argparse.SUPPRESS else action.help,
    }


def _leaves(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
    sub_actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not sub_actions:
        if not prefix:
            return {}
        arguments = [row for action in parser._actions if (row := _argument(action)) is not None]
        return {" ".join(prefix): {"prog": parser.prog, "description": parser.description, "arguments": arguments}}
    result: dict[str, dict[str, Any]] = {}
    for action in sub_actions:
        for name, child in action.choices.items():
            result.update(_leaves(child, (*prefix, name)))
    return result


def command_surface() -> dict[str, dict[str, Any]]:
    """Return every public leaf command routed by ocpf-post."""
    from ocpf_post import cli, health, observer_cli, performance_cli, portfolio_cli, provider_runner, registry_cli, replenisher_cli, scheduler_cli, setup_cli

    paths = _leaves(cli.build_parser())
    for prefix, parser in (
        ("registry", registry_cli.build_parser()),
        ("performance", performance_cli.build_parser()),
        ("portfolio", portfolio_cli.build_parser()),
        ("replenish", replenisher_cli.build_parser()),
    ):
        for path, row in _leaves(parser).items():
            paths[f"{prefix} {path}"] = row

    paths.update(_leaves(scheduler_cli.build_parser()))

    provider_parser = provider_runner.build_parser()
    provider_choices: list[str] = []
    for action in provider_parser._actions:
        if getattr(action, "dest", None) == "provider" and action.choices:
            provider_choices = [str(value) for value in action.choices]
    for command, row in _leaves(provider_parser).items():
        for provider in provider_choices:
            paths[f"{provider} {command}"] = {
                **row,
                "arguments": [value for value in row["arguments"] if value.get("name") != "provider"],
            }

    paths.update(_leaves(health.build_parser(), ("health",)))
    paths.update(_leaves(observer_cli.build_parser(), ("console",)))
    paths.update(_leaves(setup_cli.build_parser(), ("setup",)))
    paths["help"] = {
        "prog": "ocpf-post help",
        "description": "Discover the complete command catalogue in text or JSON.",
        "arguments": [
            {"name": "scope", "flags": [], "positional": True, "required": False, "nargs": "*", "default": None, "choices": None, "help": "Optional command path to inspect."},
            {"name": "json", "flags": ["--json"], "positional": False, "required": False, "nargs": 0, "default": False, "choices": None, "help": "Return machine-readable discovery metadata."},
        ],
    }
    return dict(sorted(paths.items()))


def serialisable_surface() -> dict[str, Any]:
    return {"schema_version": 1, "commands": command_surface()}


def main() -> None:
    print(json.dumps(serialisable_surface(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
