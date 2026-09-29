#!/usr/bin/env python3
"""Fail closed when canonical release metadata drifts across repository surfaces."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _match(path: Path, pattern: str, label: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise SystemExit(f"release metadata missing: {label} in {path.relative_to(ROOT)}")
    return match.group(1)


def main() -> int:
    expected = _match(
        ROOT / "pyproject.toml",
        r'^version\s*=\s*"([^"]+)"',
        "project version",
    )
    observed = {
        "src/ocpf_post/__init__.py": _match(
            ROOT / "src" / "ocpf_post" / "__init__.py",
            r"^__version__\s*=\s*['\"]([^'\"]+)['\"]",
            "__version__",
        ),
        "README.md": _match(
            ROOT / "README.md",
            r"^\*\*Current release:\s*([0-9]+\.[0-9]+\.[0-9]+)",
            "README current release",
        ),
        "docs/CURRENT_STATE.md": _match(
            ROOT / "docs" / "CURRENT_STATE.md",
            r"^Current release:\s*([0-9]+\.[0-9]+\.[0-9]+)",
            "current-state release",
        ),
        "CHANGELOG.md": _match(
            ROOT / "CHANGELOG.md",
            r"^##\s+([0-9]+\.[0-9]+\.[0-9]+)\s+—",
            "latest changelog release",
        ),
    }
    drift = {path: version for path, version in observed.items() if version != expected}
    if drift:
        print(f"Canonical project version: {expected}")
        for path, version in drift.items():
            print(f"DRIFT {path}: {version}")
        return 1
    print(f"release metadata aligned: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
