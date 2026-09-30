from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Any


class PortfolioSourceError(ValueError):
    pass


def _read_source_file(name: str) -> dict[str, Any]:
    path = files("ocpf_post") / name
    if not path.is_file():
        return {"schema_version": 1, "projects": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise PortfolioSourceError(f"Unsupported source profile schema in {name}")
    projects = data.get("projects")
    if not isinstance(projects, dict):
        raise PortfolioSourceError(f"{name} projects must be an object")
    return data


def _read_inventory_extensions(name: str) -> dict[str, Any]:
    path = files("ocpf_post") / name
    if not path.is_file():
        return {"schema_version": 1, "extensions": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise PortfolioSourceError(f"Unsupported source inventory extension schema in {name}")
    extensions = data.get("extensions")
    if not isinstance(extensions, dict):
        raise PortfolioSourceError(f"{name} extensions must be an object")
    return data


def _apply_inventory_extensions(merged: dict[str, Any]) -> None:
    data = _read_inventory_extensions("portfolio_source_inventory_extensions.json")
    for project_id, extension in data["extensions"].items():
        if project_id not in merged["projects"]:
            raise PortfolioSourceError(f"Inventory extension references unknown project: {project_id}")
        if not isinstance(extension, dict) or set(extension) != {"inventory_append"}:
            raise PortfolioSourceError(
                f"Inventory extension {project_id} must contain only inventory_append"
            )
        rows = extension.get("inventory_append")
        if not isinstance(rows, list) or not rows:
            raise PortfolioSourceError(f"Inventory extension {project_id} must append at least one item")
        inventory = merged["projects"][project_id].get("inventory")
        if not isinstance(inventory, list):
            raise PortfolioSourceError(f"Source profile {project_id} inventory must be a list")
        known = {
            (
                str(row.get("title") or ""),
                str(row.get("hook") or ""),
                str(row.get("body") or ""),
                str(row.get("cta") or ""),
            )
            for row in inventory
            if isinstance(row, dict)
        }
        for row in rows:
            if not isinstance(row, dict):
                raise PortfolioSourceError(f"Inventory extension {project_id} contains a non-object item")
            required = ("source_sha", "title", "lane", "priority", "hook", "body", "cta")
            if any(not row.get(key) and row.get(key) != 0 for key in required):
                raise PortfolioSourceError(f"Inventory extension {project_id} item is incomplete")
            if not re.fullmatch(r"[0-9A-Fa-f]{40,64}", str(row["source_sha"])):
                raise PortfolioSourceError(f"Inventory extension {project_id} has an invalid source_sha")
            if str(row["lane"]) not in {"commercial", "evergreen"}:
                raise PortfolioSourceError(f"Inventory extension {project_id} has an invalid lane")
            identity = tuple(str(row.get(key) or "") for key in ("title", "hook", "body", "cta"))
            if identity in known:
                raise PortfolioSourceError(f"Inventory extension {project_id} duplicates existing copy")
            known.add(identity)
            inventory.append(dict(row))


def packaged_source_profiles() -> dict[str, Any]:
    from ocpf_post.product_runtime import standalone_product_active

    if standalone_product_active():
        return {"schema_version": 1, "projects": {}}

    merged: dict[str, Any] = {"schema_version": 1, "projects": {}}
    for name in ("portfolio_sources.json", "portfolio_sources_extra.json"):
        data = _read_source_file(name)
        for project_id, profile in data["projects"].items():
            if project_id in merged["projects"]:
                raise PortfolioSourceError(f"Duplicate portfolio source profile: {project_id}")
            if not isinstance(profile, dict):
                raise PortfolioSourceError(f"Source profile {project_id} must be an object")
            merged["projects"][project_id] = profile
    if not merged["projects"]:
        raise PortfolioSourceError("No portfolio source profiles are configured")
    _apply_inventory_extensions(merged)
    return merged


def merged_source_profiles() -> dict[str, Any]:
    from ocpf_post.runtime_sources import merge_runtime_sources
    return merge_runtime_sources(packaged_source_profiles())
