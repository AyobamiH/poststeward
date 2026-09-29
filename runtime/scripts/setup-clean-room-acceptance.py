#!/usr/bin/env python3
"""Milestone E clean-room acceptance.

Runs fresh + explore setup in isolated bootstrap workspaces while protecting
synthetic production config/state sentinels. No provider credentials, services,
or social effects are available to this acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


def digest_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    result: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run_json(repo: Path, env: dict[str, str], *args: str) -> dict[str, Any]:
    result = subprocess.run(
        [str(repo / "ocpf-post"), *args],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout={result.stdout[-2000:]}\nstderr={result.stderr[-2000:]}"
        )
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("setup command did not return a JSON object")
    return value


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="post-once-clean-room-") as raw:
        root = Path(raw)
        production_state = root / "production-state"
        production_config = root / "production-config"
        fresh_workspace = root / "fresh-bootstrap"
        explore_workspace = root / "explore-bootstrap"

        production_state.mkdir(mode=0o700)
        production_config.mkdir(mode=0o700)
        (production_state / "schedule-events.jsonl").write_text(
            '{"sentinel":"production schedule history"}\n',
            encoding="utf-8",
        )
        (production_state / "publish-receipts.jsonl").write_text(
            '{"sentinel":"production receipt history"}\n',
            encoding="utf-8",
        )
        (production_config / "credentials.local.json").write_text(
            '{"sentinel":"production credential boundary"}\n',
            encoding="utf-8",
        )
        (production_config / "accounts.local.json").write_text(
            '{"sentinel":"production account boundary"}\n',
            encoding="utf-8",
        )

        before_state = digest_tree(production_state)
        before_config = digest_tree(production_config)

        env = dict(os.environ)
        env.update(
            {
                "OCPF_POST_STATE_DIR": str(production_state),
                "OCPF_POST_CONFIG_DIR": str(production_config),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        for name in (
            "X_USER_ACCESS_TOKEN",
            "X_OAUTH2_ACCESS_TOKEN",
            "X_REFRESH_TOKEN",
            "X_CLIENT_ID",
            "X_CLIENT_SECRET",
            "THREADS_ACCESS_TOKEN",
            "LINKEDIN_ACCESS_TOKEN",
            "LINKEDIN_TOKEN",
            "LINKEDIN_CLIENT_ID",
            "LINKEDIN_CLIENT_SECRET",
        ):
            env.pop(name, None)

        fresh = run_json(
            repo,
            env,
            "setup",
            "start",
            "--workspace",
            str(fresh_workspace),
            "--mode",
            "fresh",
            "--operator-label",
            "Clean Room Example",
            "--timezone",
            "Europe/London",
            "--pace",
            "regular",
            "--json",
        )
        if fresh["session"]["stage"] != "configuration_ready":
            raise RuntimeError("fresh setup did not stop at configuration_ready")
        if fresh["operation"]["status"] != "prepared":
            raise RuntimeError("fresh operation unexpectedly active")
        if fresh["installation"]["status"] != "candidate":
            raise RuntimeError("fresh installation unexpectedly active")
        if fresh["configuration"]["daily_originals"] != 5:
            raise RuntimeError("fresh setup pace mismatch")
        if fresh["configuration"]["hard_ceiling"] != 100:
            raise RuntimeError("fresh hard ceiling mismatch")
        if fresh["publishing_authority"] is not False or fresh["automation_enabled"] is not False:
            raise RuntimeError("fresh setup crossed publication/automation boundary")

        fresh_status = run_json(
            repo,
            env,
            "setup",
            "status",
            "--workspace",
            str(fresh_workspace),
            "--json",
        )
        if fresh_status["session"]["revision"] != fresh["session"]["revision"]:
            raise RuntimeError("fresh read-only status changed setup revision")

        fresh_resume = run_json(
            repo,
            env,
            "setup",
            "resume",
            "--workspace",
            str(fresh_workspace),
            "--json",
        )
        if fresh_resume["session"]["revision"] != fresh["session"]["revision"]:
            raise RuntimeError("fresh repeated resume duplicated a completed transition")
        if fresh_resume["session"]["stage"] != "configuration_ready":
            raise RuntimeError("fresh resume crossed Milestone E boundary")

        explore = run_json(
            repo,
            env,
            "setup",
            "start",
            "--workspace",
            str(explore_workspace),
            "--mode",
            "explore",
            "--timezone",
            "Europe/London",
            "--pace",
            "occasional",
            "--json",
        )
        if explore["session"]["stage"] != "explore_ready":
            raise RuntimeError("explore setup did not reach explore_ready")
        if explore["operation"] is not None or explore["installation"] is not None:
            raise RuntimeError("explore created production operation/install identity")
        if explore["publishing_authority"] is not False or explore["automation_enabled"] is not False:
            raise RuntimeError("explore crossed publication/automation boundary")
        if explore["configuration"]["daily_originals"] != 3:
            raise RuntimeError("explore pace mismatch")

        if digest_tree(production_state) != before_state:
            raise RuntimeError("clean-room setup modified synthetic production state")
        if digest_tree(production_config) != before_config:
            raise RuntimeError("clean-room setup modified synthetic production config")

        for workspace in (fresh_workspace, explore_workspace):
            db = workspace / "setup-control.sqlite3"
            if not db.is_file():
                raise RuntimeError(f"missing setup control database: {db}")
            if (workspace.stat().st_mode & 0o077) != 0:
                raise RuntimeError(f"setup workspace is not private: {workspace}")
            if (db.stat().st_mode & 0o077) != 0:
                raise RuntimeError(f"setup database is not private: {db}")

        result = {
            "schema_version": 1,
            "status": "pass",
            "fresh_stage": fresh["session"]["stage"],
            "fresh_operation_status": fresh["operation"]["status"],
            "fresh_installation_status": fresh["installation"]["status"],
            "explore_stage": explore["session"]["stage"],
            "explore_operation_created": explore["operation"] is not None,
            "production_state_unchanged": True,
            "production_config_unchanged": True,
            "publishing_authority": False,
            "automation_enabled": False,
            "provider_credentials_present": False,
            "boundary": (
                "Milestone E clean-room acceptance only; no provider authorization, "
                "migration/handoff, service activation or social publication."
            ),
        }
        json.dump(result, sys.stdout, sort_keys=True, indent=2)
        sys.stdout.write("\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
