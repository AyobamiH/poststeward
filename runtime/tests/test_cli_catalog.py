from __future__ import annotations

import argparse
import json
import subprocess
import unittest

from ocpf_post import cli, health, performance_cli, portfolio_cli, provider_runner, registry_cli, replenisher_cli, scheduler_cli, setup_cli
from ocpf_post.cli_catalog import COMMANDS, SCHEMA_VERSION, catalogue
from ocpf_post.cli_surface import command_surface


def _leaf_paths(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> set[str]:
    out: set[str] = set()
    sub_actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not sub_actions:
        if prefix:
            out.add(" ".join(prefix))
        return out
    for action in sub_actions:
        for name, child in action.choices.items():
            out.update(_leaf_paths(child, (*prefix, name)))
    return out


def _surface() -> set[str]:
    paths = _leaf_paths(cli.build_parser())
    paths |= {f"registry {p}" for p in _leaf_paths(registry_cli.build_parser())}
    paths |= {f"performance {p}" for p in _leaf_paths(performance_cli.build_parser())}
    paths |= {f"portfolio {p}" for p in _leaf_paths(portfolio_cli.build_parser())}
    paths |= {f"replenish {p}" for p in _leaf_paths(replenisher_cli.build_parser())}
    paths |= _leaf_paths(scheduler_cli.build_parser())

    provider_parser = provider_runner.build_parser()
    providers: list[str] = []
    for action in provider_parser._actions:
        if getattr(action, "dest", None) == "provider" and action.choices:
            providers = list(action.choices)
    provider_commands = _leaf_paths(provider_parser)
    # provider_runner's leaf walker sees only the command because provider is positional.
    paths |= {f"{provider} {command}" for provider in providers for command in provider_commands}
    paths.add("help")
    paths |= _leaf_paths(health.build_parser(), ("health",))
    paths |= _leaf_paths(setup_cli.build_parser(), ("setup",))
    return paths


class CliCatalogueTests(unittest.TestCase):
    def test_catalogue_exactly_covers_public_leaf_commands(self) -> None:
        catalogued = {command.path for command in COMMANDS}
        self.assertEqual(catalogued, set(command_surface()))
        self.assertIn("console", catalogued)
        self.assertIn("state verify", catalogued)
        self.assertIn("state segment", catalogued)
        self.assertIn("credentials keyring migrate", catalogued)
        self.assertIn("campaign reconcile", catalogued)
        self.assertIn("runtime status", catalogued)
        self.assertIn("setup start", catalogued)
        self.assertIn("setup status", catalogued)
        self.assertIn("setup resume", catalogued)

    def test_catalogue_embeds_parser_argument_truth(self) -> None:
        surface = command_surface()
        rows = {row["path"]: row for row in catalogue()["commands"]}
        self.assertEqual(set(rows), set(surface))
        for path, parser in surface.items():
            self.assertEqual(rows[path]["arguments"], parser["arguments"], path)
        console = rows["console"]["arguments"]
        self.assertTrue(any("--port" in row["flags"] for row in console))
        self.assertTrue(any("--snapshot" in row["flags"] for row in console))
        page_connect = rows["accounts connect"]["arguments"]
        self.assertTrue(any("--reuse-default" in row["flags"] for row in page_connect))
        segment = rows["state segment"]["arguments"]
        ledger = next(row for row in segment if "--ledger" in row["flags"])
        self.assertEqual(ledger["choices"], ["receipts", "schedules", "performance"])
        keyring = rows["credentials keyring migrate"]["arguments"]
        provider = next(row for row in keyring if "--provider" in row["flags"])
        self.assertEqual(provider["choices"], ["x", "threads", "linkedin"])
        reconcile = rows["campaign reconcile"]["arguments"]
        self.assertTrue(any("--apply" in row["flags"] for row in reconcile))
        provider = next(row for row in reconcile if "--provider" in row["flags"])
        self.assertEqual(provider["choices"], ["x", "threads", "linkedin"])
        setup_start = rows["setup start"]["arguments"]
        pace = next(row for row in setup_start if "--pace" in row["flags"])
        self.assertIsNone(pace["default"])
        self.assertEqual(pace["choices"], ["occasional", "regular", "active", "high", "custom"])

    def test_every_command_has_consequence_classification(self) -> None:
        allowed = {"READ_ONLY", "LOCAL_STATE_WRITE", "FUTURE_CONSEQUENCE", "EXTERNAL_PROVIDER_EFFECT", "AUTHORITY_CHANGE"}
        for command in COMMANDS:
            self.assertIn(command.consequence, allowed, command.path)
            if command.external_effect:
                self.assertEqual(command.consequence, "EXTERNAL_PROVIDER_EFFECT", command.path)

    def test_json_help_is_versioned_and_machine_readable(self) -> None:
        data = catalogue()
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertTrue(data["cli_version"])
        self.assertEqual(len(data["commands"]), len(COMMANDS))

    def test_root_help_exposes_modern_command_families(self) -> None:
        result = subprocess.run(["./ocpf-post", "--help"], capture_output=True, text=True, check=True)
        for token in ("setup", "portfolio", "replenish", "schedule", "performance", "threads", "linkedin", "run-due", "console", "capabilities", "credentials", "state", "runtime"):
            self.assertIn(token, result.stdout)

    def test_scoped_json_help(self) -> None:
        result = subprocess.run(["./ocpf-post", "help", "portfolio", "--json"], capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data["scope"], "portfolio")
        self.assertTrue(data["commands"])
        self.assertTrue(all(item["path"].startswith("portfolio ") for item in data["commands"]))


if __name__ == "__main__":
    unittest.main()
