"""Outbound, generation-bound command polling. No shell or arbitrary file access."""
from __future__ import annotations

import json
import time
from typing import Any

from ocpf_post import local_store
from ocpf_post.poststeward_cloud import CloudError, _request, heartbeat, installation_identity, runtime_token
from ocpf_post.state import state_dir


def _execute(command: dict[str, Any]) -> dict[str, Any]:
    operation = command["operation"]
    value = command["input"]
    if operation == "runtime_inspect":
        view = value["view"]
        if view == "status":
            from ocpf_post.automation_authority import read
            marker = read(state_dir())
            return {"automation": {key: marker.get(key) for key in ("status", "authority_generation", "runtime_revision")}}
        if view == "projects":
            from ocpf_post.registry import load_registry, project_summary
            return {"projects": [project_summary(key) for key in sorted(load_registry()["projects"])[:50]]}
        if view == "campaigns":
            from ocpf_post.campaigns import campaign_ids, builtin_manifest
            return {"campaigns": [{"campaign": key, "project": builtin_manifest(key).get("project")} for key in campaign_ids()[:50]]}
        if view == "schedules":
            from ocpf_post.scheduler import schedule_records
            from ocpf_post.scheduler_cli import _SAFE_SCHEDULE_FIELDS, _safe_subset
            return {"schedules": [_safe_subset(row, _SAFE_SCHEDULE_FIELDS) for row in schedule_records()[-50:]]}
    if operation == "runtime_schedule_create":
        from ocpf_post.scheduler import create_schedule
        row = create_schedule(campaign=value["campaign"], provider=value["provider"], at=value["at"],
                              timezone_name=value.get("timezone", "UTC"), bridge_command_id=command["commandId"])
        return {"scheduleId": row["schedule_id"], "status": row["status"], "runAt": row["run_at"]}
    if operation == "runtime_schedule_cancel":
        from ocpf_post.scheduler import cancel_schedule
        row = cancel_schedule(value["scheduleId"])
        return {"scheduleId": row["schedule_id"], "status": row["status"]}
    raise CloudError("RUNTIME_OPERATION_UNSUPPORTED", "Unsupported bounded local operation.")


def run_once() -> dict[str, Any]:
    """Claim at most one command; persist intent before mutation and never redispatch it."""
    executor = heartbeat()
    generation = executor["authorityGeneration"]
    identity = installation_identity()
    if executor.get("activeInstallationId") != identity.get("installation_id"):
        raise CloudError("RUNTIME_EXECUTOR_FENCED", "This machine is not the active executor.")
    journal = state_dir() / "remote-commands.json"
    with local_store.locked(journal):
        state = local_store.read(journal) or {"schema_version": 1, "commands": {}}
        entries = state["commands"]
        # Retry only result acknowledgement after response loss. An interrupted
        # mutation is reported unknown; it is never re-executed.
        pending = next((row for row in entries.values() if row["generation"] == generation and not row.get("acknowledged")), None)
        if pending is None:
            _, response = _request("POST", "/api/runtime/commands/claim", token=runtime_token(),
                                   payload={"authorityGeneration": generation})
            command = response.get("command")
            if command is None:
                return {"status": "idle", "authorityGeneration": generation}
            if (command.get("installationId") != identity["installation_id"]
                    or command.get("authorityGeneration") != generation
                    or command.get("expiresAt", 0) <= int(time.time() * 1000)):
                raise CloudError("RUNTIME_COMMAND_NOT_EXECUTABLE", "Stale command refused.")
            cid = command["commandId"]
            if cid in entries:
                pending = entries[cid]
            else:
                _request("POST", "/api/runtime/commands/authorize", token=runtime_token(),
                         payload={"commandId": cid, "authorityGeneration": generation})
                pending = {"command_id": cid, "generation": generation, "status": "started"}
                entries[cid] = pending
                local_store.write(journal, state)
                try:
                    result = _execute(command)
                    while len(json.dumps(result).encode("utf-8")) > 24 * 1024:
                        rows = next((value for value in result.values() if isinstance(value, list) and value), None)
                        if rows is None:
                            raise ValueError("Local result exceeds the bounded response")
                        rows.pop(0)
                        result["truncated"] = True
                    pending.update(status="completed", result=result)
                except Exception:
                    # Raw exception text can contain private local paths or input.
                    pending.update(status="failed", result={"error": {"code": "RUNTIME_LOCAL_OPERATION_FAILED"}})
                local_store.write(journal, state)
        if pending["status"] == "started":
            pending.update(status="failed", result={"error": {"code": "RUNTIME_COMMAND_OUTCOME_UNKNOWN"}})
            local_store.write(journal, state)
        _, receipt = _request("POST", "/api/runtime/commands/complete", token=runtime_token(), payload={
            "commandId": pending["command_id"], "authorityGeneration": generation,
            "status": pending["status"], "result": pending["result"],
        })
        pending["acknowledged"] = True
        acknowledged = [key for key, row in entries.items() if row.get("acknowledged")]
        for key in acknowledged[:-256]:
            del entries[key]
        local_store.write(journal, state)
        return receipt
