"""Add-only local project and approved campaign registration."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import random
import re
import shutil
import tempfile
import time

from ocpf_post.publication_payload import PUBLICATION_TEXT_LIMITS
from ocpf_post.state import config_dir, state_dir, ensure_private_dir, write_private_json

MAX_INPUT_BYTES = 1_000_000
PROVIDERS = {"x", "threads", "linkedin"}
# Whole-publication safety envelope. X/Threads over-length copy is segmented
# into provider-native reply chains later; this is not a per-post limit.
TEXT_LIMITS = dict(PUBLICATION_TEXT_LIMITS)


class OnboardingError(ValueError):
    pass


def _object(value, allowed, required, name):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise OnboardingError(f"Invalid {name} fields; consult docs/RUNTIME_ONBOARDING.md")
    return value


def _text(value, name, limit=120):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit:
        raise OnboardingError(f"Invalid {name}")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise OnboardingError(f"Control characters are not allowed in {name}")
    return value


def _slug(value, name):
    value = _text(value, name, 64)
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value):
        raise OnboardingError(f"Invalid {name}; use a lowercase hyphenated identifier")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OnboardingError("Duplicate JSON field")
        result[key] = value
    return result


def _decode(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise OnboardingError("Input must be valid UTF-8 JSON with unique fields") from exc


def read_input(path, *, apply=False, expected_sha256=None):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise OnboardingError("Cannot read input file") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise OnboardingError("Input exceeds the one-megabyte import limit")
    digest = hashlib.sha256(raw).hexdigest()
    if apply and not expected_sha256:
        raise OnboardingError("--apply requires --expected-sha256 from the reviewed preview")
    if expected_sha256 is not None and expected_sha256 != digest:
        raise OnboardingError("Input changed since preview; expected SHA-256 does not match")
    return _decode(raw), digest


def validate_project(value):
    allowed = {"schema_version", "project", "label", "campaign_prefixes", "accounts", "default_accounts"}
    value = _object(value, allowed, allowed, "project")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise OnboardingError("Unsupported project schema version")
    _slug(value["project"], "project")
    _text(value["label"], "label")
    prefixes = value["campaign_prefixes"]
    if not isinstance(prefixes, list) or not 1 <= len(prefixes) <= 16:
        raise OnboardingError("Provide 1 to 16 campaign prefixes")
    for prefix in prefixes:
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Z][A-Z0-9-]{0,29}-", prefix):
            raise OnboardingError("Prefixes must be uppercase identifiers ending in a hyphen")
    for index, prefix in enumerate(prefixes):
        if any(prefix.startswith(other) or other.startswith(prefix) for other in prefixes[index + 1:]):
            raise OnboardingError("Campaign prefixes overlap")
    accounts = value["accounts"]
    if not isinstance(accounts, dict) or not 1 <= len(accounts) <= 32:
        raise OnboardingError("Provide 1 to 32 account aliases")
    for alias, account in accounts.items():
        _slug(alias, "account alias")
        _object(account, {"provider", "account_id", "label", "role"}, {"provider", "account_id"}, "account")
        provider = account["provider"]
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise OnboardingError("Unsupported account provider")
        identity = _text(account["account_id"], "account ID", 128)
        pattern = r"urn:li:person:[A-Za-z0-9_-]+" if provider == "linkedin" else r"[0-9]+"
        if not re.fullmatch(pattern, identity):
            raise OnboardingError("Account ID does not match the supported provider identity format")
        for field in ("label", "role"):
            if field in account:
                _text(account[field], field)
    defaults = value["default_accounts"]
    if not isinstance(defaults, dict) or set(defaults) - PROVIDERS:
        raise OnboardingError("Invalid default accounts")
    for provider, alias in defaults.items():
        if not isinstance(alias, str) or alias not in accounts or accounts[alias]["provider"] != provider:
            raise OnboardingError("Default account must resolve to an alias for the same provider")
    return value


def registry_path():
    return config_dir() / "runtime-projects.json"


def _runtime_registry():
    path = registry_path()
    if not path.exists():
        return {"schema_version": 1, "projects": {}}
    data = _decode(path.read_bytes())
    _object(data, {"schema_version", "projects"}, {"schema_version", "projects"}, "runtime registry")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1 or not isinstance(data["projects"], dict):
        raise OnboardingError("Invalid runtime registry")
    return data


def _check_project_conflicts(project, projects):
    if project["project"] in projects:
        raise OnboardingError("Project ID already exists; imports cannot replace account authority")
    for existing in projects.values():
        for old in existing.get("campaign_prefixes", []):
            for new in project["campaign_prefixes"]:
                if new.startswith(old) or old.startswith(new):
                    raise OnboardingError("Campaign prefix overlaps an existing project")


def merge_runtime_registry(packaged):
    projects = dict(packaged["projects"])
    for project_id, raw in _runtime_registry()["projects"].items():
        project = validate_project(raw)
        if project_id != project["project"]:
            raise OnboardingError("Runtime project key and identity disagree")
        _check_project_conflicts(project, projects)
        projects[project_id] = {k: v for k, v in project.items() if k not in {"schema_version", "project"}}
    return {**packaged, "projects": projects}


def _coordination_path(name: str) -> Path:
    return config_dir() / f"{name}.lock"


def _read_lock_metadata(fd: int) -> dict:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096)
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def coordination_status(name: str = "source-onboarding") -> dict:
    """Inspect one advisory coordination lease without acquiring authority."""
    try:
        import fcntl
    except ImportError:
        return {
            "schema_version": 1,
            "status": "unavailable",
            "lock_name": name,
            "error_type": "PosixLockUnavailable",
            "retryable": False,
        }
    path = _coordination_path(name)
    if not path.exists():
        return {
            "schema_version": 1,
            "status": "available",
            "lock_name": name,
            "active": False,
            "retryable": False,
            "boundary": "Read-only coordination observation; absence of a held lock does not reserve the next operation.",
        }
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return {
            "schema_version": 1,
            "status": "unavailable",
            "lock_name": name,
            "error_type": type(exc).__name__,
            "retryable": False,
        }
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        metadata = _read_lock_metadata(fd)
    finally:
        os.close(fd)
    if acquired:
        return {
            "schema_version": 1,
            "status": "available",
            "lock_name": name,
            "active": False,
            "retryable": False,
            "boundary": "Read-only coordination observation; availability is a point-in-time result, not a reservation.",
        }
    acquired_at = metadata.get("acquired_at")
    age_seconds = None
    if isinstance(acquired_at, str):
        try:
            observed = datetime.fromisoformat(acquired_at.replace("Z", "+00:00"))
            if observed.tzinfo is not None:
                age_seconds = max(0.0, (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds())
        except ValueError:
            pass
    return {
        "schema_version": 1,
        "status": "active",
        "lock_name": name,
        "active": True,
        "retryable": True,
        "holder_identity": metadata.get("holder_identity"),
        "holder_pid": metadata.get("holder_pid"),
        "operation": metadata.get("operation"),
        "acquired_at": acquired_at,
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "boundary": "Coordination observation only. Do not break or delete an active lease; wait for the OS-released lock.",
    }


def wait_for_coordination(name: str = "source-onboarding", *, timeout_seconds: float = 30.0,
                          poll_seconds: float = 0.1) -> dict:
    """Wait for point-in-time lock availability without reserving the next operation."""
    if not isinstance(timeout_seconds, (int, float)) or not 0 <= float(timeout_seconds) <= 300:
        raise ValueError("timeout_seconds must be between 0 and 300")
    if not isinstance(poll_seconds, (int, float)) or not 0.05 <= float(poll_seconds) <= 5:
        raise ValueError("poll_seconds must be between 0.05 and 5")
    started = time.monotonic()
    while True:
        status = coordination_status(name)
        elapsed = time.monotonic() - started
        if status.get("status") != "active":
            return {
                **status,
                "wait_status": "available" if status.get("status") == "available" else "unavailable",
                "waited_seconds": round(elapsed, 3),
            }
        if elapsed >= float(timeout_seconds):
            return {
                **status,
                "wait_status": "timeout",
                "waited_seconds": round(elapsed, 3),
            }
        time.sleep(min(float(poll_seconds), max(0.0, float(timeout_seconds) - elapsed)))


@contextmanager
def _import_lock(name="onboarding", *, operation: str | None = None,
                 timeout_seconds: float = 0.0):
    # Auto-released by the OS on exit/crash. Never shares a lock with publishing.
    # The tiny metadata record mirrors lease-style holder/acquisition visibility;
    # the flock itself remains the only authority. Callers opt into bounded
    # acquisition waiting explicitly; the default remains fail-fast.
    if not isinstance(timeout_seconds, (int, float)) or not 0 <= float(timeout_seconds) <= 300:
        raise ValueError("timeout_seconds must be between 0 and 300")
    try:
        import fcntl
    except ImportError as exc:
        raise OnboardingError("Applying runtime imports requires POSIX file locks (Linux, WSL or macOS)") from exc
    ensure_private_dir(config_dir())
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(_coordination_path(name), flags, 0o600)
    started = time.monotonic()
    delay_seconds = 0.05
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = float(timeout_seconds) - (time.monotonic() - started)
                if remaining <= 0:
                    raise OnboardingError(
                        f"Another {name} operation is active; retry after it completes"
                    ) from exc
                # Full jitter avoids synchronising multiple bounded waiters while
                # keeping the retry budget local to the actual lock authority.
                time.sleep(random.uniform(0.0, min(delay_seconds, remaining)))
                delay_seconds = min(0.5, delay_seconds * 2)
        acquired = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        metadata = {
            "schema_version": 1,
            "lock_name": name,
            "holder_identity": f"pid:{os.getpid()}",
            "holder_pid": os.getpid(),
            "operation": operation or name,
            "acquired_at": acquired,
        }
        payload = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
        os.fsync(fd)
        yield
    finally:
        os.close(fd)


def import_project(path, *, apply=False, expected_sha256=None):
    value, digest = read_input(path, apply=apply, expected_sha256=expected_sha256)
    project = validate_project(value)
    def check():
        from ocpf_post.registry import load_registry
        runtime = _runtime_registry()
        existing = runtime["projects"].get(project["project"])
        merged = load_registry()  # Validate the complete authority, including collisions.
        if existing == project:
            return runtime, True
        _check_project_conflicts(project, merged["projects"])
        return runtime, False
    if apply:
        with _import_lock():
            runtime, exists = check()
            if not exists:
                runtime["projects"][project["project"]] = project
                write_private_json(registry_path(), runtime)
    else:
        _, exists = check()
    return {"schema_version": 1, "kind": "project", "apply": apply, "input_sha256": digest,
            "result": "already_present" if exists else "imported" if apply else "preview",
            "project": project, "identity_verified": False,
            "boundary": "Local expected account bindings only. No credentials imported, OAuth performed or provider identity verified."}


def _timestamp(value, name):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise OnboardingError(f"Invalid {name}") from exc
    if parsed.tzinfo is None:
        raise OnboardingError(f"{name} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def validate_campaign(value, *, allocate=False, now=None):
    from ocpf_post.campaigns import normalize_campaign_id
    from ocpf_post.registry import project, resolve_account
    fields = {"schema_version", "campaign", "project", "title", "status", "destinations", "texts", "source", "allocation"}
    required = fields - {"allocation"}
    value = _object(value, fields, required, "campaign")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["status"] != "COPY-READY":
        raise OnboardingError("Campaign must use schema_version 1 and status COPY-READY")
    cid = _text(value["campaign"], "campaign ID", 64)
    if normalize_campaign_id(cid) != cid:
        raise OnboardingError("Campaign ID must already be uppercase")
    if re.search(r"-(?:AUTO-[0-9]{2}[IQP]-[A-F0-9]{7}|EVENT-[A-F0-9]{8})$", cid):
        raise OnboardingError("Campaign ID uses the replenisher's reserved generated namespace")
    project_id = _slug(value["project"], "project")
    registered = project(project_id)
    if not any(cid.startswith(prefix) for prefix in registered.get("campaign_prefixes", [])):
        raise OnboardingError("Campaign ID is outside this project's registered prefixes")
    _text(value["title"], "campaign title", 200)
    destinations = value["destinations"]
    texts = value["texts"]
    if not isinstance(destinations, dict) or not destinations or set(destinations) - PROVIDERS:
        raise OnboardingError("Destinations must declare supported providers and project-scoped aliases")
    if not isinstance(texts, dict) or set(texts) != set(destinations):
        raise OnboardingError("Each destination requires exactly one approved text")
    for provider, alias in destinations.items():
        _slug(alias, "account alias")
        resolve_account(project_id, alias, expected_provider=provider)
        text = texts[provider]
        if not isinstance(text, str) or not text or text != text.strip() or len(text) > TEXT_LIMITS[provider]:
            raise OnboardingError(f"{provider} text must be nonempty, trimmed and at most {TEXT_LIMITS[provider]} characters")
        if any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in text):
            raise OnboardingError("Unsupported control characters in campaign text")
    source = _object(value["source"], {"type", "source_id"}, {"type", "source_id"}, "source")
    if source["type"] != "owner_approved":
        raise OnboardingError("Imported source type must be owner_approved; import does not verify external provenance")
    _text(source["source_id"], "source ID", 200)
    allocation = {"enabled": False}
    if "allocation" in value:
        fields = {"lane", "priority", "prepared_at", "expires_at"}
        raw = _object(value["allocation"], fields, fields, "allocation")
        if raw["lane"] not in ("development", "commercial", "evergreen") or type(raw["priority"]) is not int or not 0 <= raw["priority"] <= 100:
            raise OnboardingError("Invalid allocation lane or priority")
        prepared = _timestamp(raw["prepared_at"], "prepared_at")
        expires = _timestamp(raw["expires_at"], "expires_at")
        if expires <= prepared:
            raise OnboardingError("Allocation expiry must follow preparation")
        current = now or datetime.now(timezone.utc)
        if allocate and not prepared <= current < expires:
            raise OnboardingError("Allocator opt-in requires a currently fresh campaign")
        allocation = {**raw, "enabled": allocate}
    elif allocate:
        raise OnboardingError("--allocate requires explicit allocation metadata in the input")
    manifest = {"campaign": cid, "project": project_id, "title": value["title"], "status": "COPY-READY",
                "providers": sorted(destinations), "destinations": destinations, "source": source,
                "allocation": allocation, "runtime_imported": True,
                "payload_sha256": {p: hashlib.sha256(text.encode()).hexdigest() for p, text in texts.items()}}
    return manifest, texts


def _existing_campaign(manifest, texts):
    from ocpf_post.campaigns import runtime_campaign_root
    from ocpf_post.product_runtime import standalone_product_active

    cid = manifest["campaign"]
    if not standalone_product_active() and (files("ocpf_post") / "campaigns" / cid).exists():
        raise OnboardingError("Packaged campaign ID already exists and cannot be replaced")
    destination = runtime_campaign_root() / cid
    if not destination.exists():
        return False
    try:
        stored = _decode((destination / "manifest.json").read_bytes())
        same = stored == manifest and all((destination / f"{p}.txt").read_text(encoding="utf-8") == text + "\n" for p, text in texts.items())
    except (OSError, ValueError):
        same = False
    if not same:
        raise OnboardingError("Runtime campaign ID already exists with different data; use a new campaign ID")
    return True


def _save_campaign(manifest, texts):
    from ocpf_post.campaigns import runtime_campaign_root
    root = runtime_campaign_root()
    ensure_private_dir(root)
    staging_root = state_dir() / "onboarding-staging"
    ensure_private_dir(staging_root)
    staging = Path(tempfile.mkdtemp(prefix="campaign-", dir=staging_root))
    try:
        write_private_json(staging / "manifest.json", manifest)
        for provider, text in texts.items():
            with os.fdopen(os.open(staging / f"{provider}.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
                stream.write(text + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        # The live reader sees a complete campaign directory or none.
        # POSIX rename refuses a non-empty existing campaign directory.
        os.rename(staging, root / manifest["campaign"])
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def import_campaign(path, *, apply=False, expected_sha256=None, allocate=False, now=None):
    value, digest = read_input(path, apply=apply, expected_sha256=expected_sha256)
    manifest, texts = validate_campaign(value, allocate=allocate, now=now)
    if apply:
        with _import_lock():
            # Re-resolve authority after taking the import lock.
            manifest, texts = validate_campaign(value, allocate=allocate, now=now)
            exists = _existing_campaign(manifest, texts)
            if not exists:
                _save_campaign(manifest, texts)
    else:
        exists = _existing_campaign(manifest, texts)
    return {"schema_version": 1, "kind": "campaign", "apply": apply, "input_sha256": digest,
            "result": "already_present" if exists else "imported" if apply else "preview",
            "manifest": manifest, "texts": texts, "published": False, "allocation_enabled": allocate,
            "boundary": "No provider calls or reservations. --allocate explicitly permits the existing allocator to select this campaign for future publication."}


def add_import_arguments(parser, *, campaign=False):
    parser.add_argument("--file", required=True, help="Strict JSON import file; never include credentials")
    parser.add_argument("--apply", action="store_true", help="Save the reviewed import locally")
    parser.add_argument("--expected-sha256", help="Input SHA-256 returned by preview; required with --apply")
    if campaign:
        parser.add_argument("--allocate", action="store_true", help="Explicitly authorise eligible imported copy for the running portfolio allocator")


def cmd_import_project(args):
    _run_cli(lambda: import_project(args.file, apply=args.apply, expected_sha256=args.expected_sha256))


def cmd_import_campaign(args):
    _run_cli(lambda: import_campaign(args.file, apply=args.apply, expected_sha256=args.expected_sha256, allocate=args.allocate))


def _run_cli(fn):
    import sys
    try:
        result = fn()
    except (ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, OnboardingError) else "Import failed validation or could not access local state"
        print(f"Error: {message}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(result, indent=2, ensure_ascii=False))
