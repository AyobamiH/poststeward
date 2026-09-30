from __future__ import annotations

import ast
from collections import Counter
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "owner-guide.py"
SPEC = importlib.util.spec_from_file_location("owner_command_guide", PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class OwnerCommandGuideTests(unittest.TestCase):
    def test_committed_index_matches_canonical_catalogue(self):
        text = module.GUIDE.read_text(encoding="utf-8")
        self.assertEqual(text, module.refreshed(text))

    def test_every_native_path_occurs_once_across_both_levels(self):
        commands = module.COMMANDS
        longest_first = sorted((c.path for c in commands), key=len, reverse=True)
        cells = re.findall(r"^\| `\./ocpf-post ([^`]+)` \|", module.render_index(), re.M)
        matched = []
        for cell in cells:
            matches = [path for path in longest_first if cell == path or cell.startswith(path + " ")]
            self.assertTrue(matches, cell)
            matched.append(matches[0])
        self.assertEqual(Counter(matched), Counter(c.path for c in commands))

    def test_beginner_and_advanced_have_distinct_rows(self):
        beginner = module.render_index(level="beginner")
        advanced = module.render_index(level="advanced")
        self.assertNotIn("### Advanced command list", beginner)
        self.assertNotIn("### Beginner command list", advanced)
        self.assertIn("### Advanced command list", advanced)
        self.assertNotIn("| `./ocpf-post publish`", beginner)
        self.assertIn("| `./ocpf-post publish`", advanced)

    def test_beginner_safety_contracts_are_checked(self):
        from dataclasses import replace
        changed = tuple(replace(c, consequence="FUTURE_CONSEQUENCE") if c.path == "health" else c for c in module.COMMANDS)
        with self.assertRaises(ValueError):
            module.render_index(changed)

    def test_new_command_is_included_in_advanced_not_silently_lost(self):
        extra = module.Command("fixture inspect", "Read the fixture.", "READ_ONLY")
        text = module.render_index((*module.COMMANDS, extra))
        self.assertIn("| `./ocpf-post fixture inspect`", text)
        self.assertNotEqual(module.render_index(), text)

    def test_unknown_consequence_is_rejected(self):
        extra = module.Command("fixture mutate", "Unknown effect.", "UNKNOWN_EFFECT")
        with self.assertRaises(ValueError):
            module.render_index((*module.COMMANDS, extra))

    def test_duplicate_or_missing_beginner_entry_is_rejected(self):
        with self.assertRaises(ValueError):
            module.render_index((*module.COMMANDS, module.COMMANDS[0]))
        with self.assertRaises(ValueError):
            module.render_index(tuple(c for c in module.COMMANDS if c.path != "health"))

    def test_missing_or_duplicate_markers_are_rejected(self):
        for text in ("no markers", module.START + module.START + module.END):
            with self.assertRaises(ValueError):
                module.refreshed(text)

    def test_preserves_handwritten_instructions(self):
        text = "before\n" + module.START + "\nstale\n" + module.END + "\nafter\n"
        updated = module.refreshed(text)
        self.assertTrue(updated.startswith("before\n"))
        self.assertTrue(updated.endswith("\nafter\n"))
        self.assertNotIn("\nstale\n", updated)

    def test_cli_check_and_both_levels_execute_without_runtime_state(self):
        for arguments in (("--check",), ("--level", "beginner"), ("--level", "advanced")):
            result = subprocess.run([sys.executable, "-B", str(PATH), *arguments], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout)

    def test_repository_paths_and_grammar(self):
        ast.parse(PATH.read_text(encoding="utf-8"), feature_version=(3, 10))
        self.assertNotIn("/mnt/data/", PATH.read_text(encoding="utf-8"))
        self.assertNotIn("/home/johnh/", PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
