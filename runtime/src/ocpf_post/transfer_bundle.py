"""Bounded, content-addressed Setup & Recovery transfer bundles.

This module deliberately does not inspect live Post-Once state, call providers,
mutate schedules, install services or grant publication authority. Callers must
supply an already-prepared set of semantic source objects.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile
from typing import Any, IO, Iterable, NoReturn
import uuid

UTC = timezone.utc
BUNDLE_FORMAT_VERSION = 1
CANONICALIZATION = "post-once-canonical-json-v1"
DIGEST_ALGORITHM = "sha256"
TRANSPORT = "ustar+gzip"
MANIFEST_NAME = "manifest.json"
OBJECT_PREFIX = "objects/sha256/"

MAX_COMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_OBJECTS = 10_000
MAX_OBJECT_BYTES = 256 * 1024 * 1024
MAX_TOTAL_OBJECT_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_NAME_BYTES = 96
QUARANTINE_RESERVE_BYTES = 256 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024

RESTORE_CLASSES = frozenset({"operation", "historical", "configuration", "held_authority", "evidence"})
_LOGICAL_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)*$")
_SHA_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_UTC_SECOND_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class BundleError(ValueError):
    """Stable transfer-bundle failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparedObject:
    logical_id: str
    restore_class: str
    media_type: str
    source: Path
    schema_version: int | None = None


def _fail(code: str, message: str) -> NoReturn:
    raise BundleError(code, message)


def _utc_iso(value: datetime | None = None) -> str:
    dt = (value or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ascii(value: Any, *, field: str, minimum: int = 1, maximum: int = 256) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        _fail("manifest_schema_invalid", f"Invalid {field}")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise BundleError("manifest_schema_invalid", f"{field} must be ASCII") from exc
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        _fail("manifest_schema_invalid", f"{field} contains control characters")
    return value


def _uuid4(value: Any, *, field: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BundleError("manifest_schema_invalid", f"Invalid {field}") from exc
    if parsed.version != 4 or str(parsed) != str(value):
        _fail("manifest_schema_invalid", f"{field} must be canonical UUID4")
    return str(parsed)


def _sha256_digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _validate_digest(value: Any, *, field: str = "digest") -> str:
    text = _ascii(value, field=field, minimum=71, maximum=71)
    if not _SHA_DIGEST_RE.fullmatch(text):
        _fail("manifest_schema_invalid", f"Invalid {field}")
    return text


def _object_path(digest: str) -> str:
    _validate_digest(digest)
    return OBJECT_PREFIX + digest.removeprefix("sha256:")


def _validate_logical_id(value: Any) -> str:
    text = _ascii(value, field="logical_id", maximum=200)
    if not _LOGICAL_ID_RE.fullmatch(text):
        _fail("manifest_schema_invalid", "Invalid logical_id")
    return text


def _validate_media_type(value: Any) -> str:
    text = _ascii(value, field="media_type", maximum=200)
    if "/" not in text or any(char.isspace() for char in text):
        _fail("manifest_schema_invalid", "Invalid media_type")
    return text


def _validate_restore_class(value: Any) -> str:
    text = _ascii(value, field="restore_class", maximum=40)
    if text not in RESTORE_CLASSES:
        _fail("manifest_schema_invalid", "Invalid restore_class")
    return text


def _validate_timestamp(value: Any) -> str:
    text = _ascii(value, field="sealed_at", minimum=20, maximum=20)
    if not _UTC_SECOND_RE.fullmatch(text):
        _fail("manifest_schema_invalid", "sealed_at must be UTC to whole seconds")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleError("manifest_schema_invalid", "Invalid sealed_at") from exc
    return text


def _validate_source_version(value: Any) -> str:
    text = _ascii(value, field="source_post_once_version", maximum=64)
    if not _VERSION_RE.fullmatch(text):
        _fail("manifest_schema_invalid", "Invalid source_post_once_version")
    return text


def _validate_source_revision(value: Any) -> str:
    text = _ascii(value, field="source_revision", minimum=40, maximum=40)
    if not _REVISION_RE.fullmatch(text):
        _fail("manifest_schema_invalid", "Invalid source_revision")
    return text


def _validate_schema_version(value: Any, *, field: str, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or value < 1:
        _fail("manifest_schema_invalid", f"Invalid {field}")
    return value


def _reject_float(_value: str) -> Any:
    _fail("manifest_float_not_allowed", "Canonical manifest numbers must be integers")


def _reject_constant(_value: str) -> Any:
    _fail("manifest_nonfinite_number", "Canonical manifest forbids non-finite numbers")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("manifest_duplicate_key", f"Duplicate manifest key: {key}")
        value[key] = item
    return value


def parse_manifest_bytes(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        _fail("manifest_too_large", "Transfer manifest exceeds the v1 size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError("manifest_invalid_utf8", "Transfer manifest is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except BundleError:
        raise
    except json.JSONDecodeError as exc:
        raise BundleError("manifest_json_invalid", "Transfer manifest is not valid JSON") from exc
    return validate_manifest(value)


def _canonical_value(value: Any, *, path: str = "$") -> Any:
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        _fail("manifest_float_not_allowed", f"Canonical manifest float at {path}")
    if isinstance(value, str):
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise BundleError("manifest_non_ascii", f"Canonical manifest string at {path} is not ASCII") from exc
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
            _fail("manifest_control_character", f"Canonical manifest control character at {path}")
        return value
    if isinstance(value, list):
        return [_canonical_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                _fail("manifest_non_string_key", f"Canonical manifest key at {path} is not a string")
            try:
                key.encode("ascii")
            except UnicodeEncodeError as exc:
                raise BundleError("manifest_non_ascii", f"Canonical manifest key at {path} is not ASCII") from exc
            normalized[key] = _canonical_value(value[key], path=f"{path}.{key}")
        return normalized
    _fail("manifest_unsupported_type", f"Unsupported canonical manifest type at {path}")


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    validated = validate_manifest(manifest)
    normalized = _canonical_value(validated)
    raw = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    if len(raw) > MAX_MANIFEST_BYTES:
        _fail("manifest_too_large", "Canonical transfer manifest exceeds the v1 size limit")
    return raw


def validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("manifest_schema_invalid", "Transfer manifest must be an object")

    required = {
        "bundle_format_version",
        "bundle_id",
        "canonicalization",
        "digest_algorithm",
        "transport",
        "sealed_at",
        "operation_id",
        "source_installation_id",
        "source_authority_generation",
        "source_post_once_version",
        "source_revision",
        "source_state_registry_version",
        "object_count",
        "total_object_bytes",
        "objects",
    }
    if set(value) != required:
        _fail("manifest_schema_invalid", "Transfer manifest fields do not match v1")

    if value["bundle_format_version"] != BUNDLE_FORMAT_VERSION:
        _fail("bundle_format_unsupported", "Unsupported transfer bundle format")
    if value["canonicalization"] != CANONICALIZATION:
        _fail("canonicalization_unsupported", "Unsupported transfer manifest canonicalization")
    if value["digest_algorithm"] != DIGEST_ALGORITHM:
        _fail("digest_algorithm_unsupported", "Unsupported transfer digest algorithm")
    if value["transport"] != TRANSPORT:
        _fail("transport_unsupported", "Unsupported transfer transport")

    _uuid4(value["bundle_id"], field="bundle_id")
    _validate_timestamp(value["sealed_at"])
    _uuid4(value["operation_id"], field="operation_id")
    _uuid4(value["source_installation_id"], field="source_installation_id")
    _validate_schema_version(value["source_authority_generation"], field="source_authority_generation")
    _validate_source_version(value["source_post_once_version"])
    _validate_source_revision(value["source_revision"])
    _validate_schema_version(value["source_state_registry_version"], field="source_state_registry_version")

    objects = value["objects"]
    if not isinstance(objects, list):
        _fail("manifest_schema_invalid", "objects must be an array")
    if len(objects) > MAX_OBJECTS:
        _fail("too_many_objects", "Transfer bundle has too many object descriptors")

    descriptors: list[dict[str, Any]] = []
    logical_ids: set[str] = set()
    digests: set[str] = set()
    total = 0
    previous_logical_id: str | None = None

    for row in objects:
        if not isinstance(row, dict) or set(row) != {
            "logical_id", "restore_class", "media_type", "digest", "size", "schema_version"
        }:
            _fail("manifest_schema_invalid", "Invalid transfer object descriptor fields")
        logical_id = _validate_logical_id(row["logical_id"])
        if logical_id in logical_ids:
            _fail("duplicate_logical_id", "Transfer manifest repeats a logical_id")
        if previous_logical_id is not None and logical_id <= previous_logical_id:
            _fail("objects_not_canonical_order", "Transfer objects must be sorted by logical_id")
        previous_logical_id = logical_id
        logical_ids.add(logical_id)

        digest = _validate_digest(row["digest"])
        if digest in digests:
            _fail("duplicate_object_digest", "V1 requires one descriptor per unique object digest")
        digests.add(digest)

        size = row["size"]
        if type(size) is not int or not 0 <= size <= MAX_OBJECT_BYTES:
            _fail("object_size_invalid", "Transfer object size exceeds the v1 limit")
        total += size
        if total > MAX_TOTAL_OBJECT_BYTES:
            _fail("total_object_bytes_exceeded", "Transfer object bytes exceed the v1 total limit")

        descriptor = {
            "logical_id": logical_id,
            "restore_class": _validate_restore_class(row["restore_class"]),
            "media_type": _validate_media_type(row["media_type"]),
            "digest": digest,
            "size": size,
            "schema_version": _validate_schema_version(
                row["schema_version"],
                field="object schema_version",
                nullable=True,
            ),
        }
        descriptors.append(descriptor)

    if type(value["object_count"]) is not int or value["object_count"] != len(descriptors):
        _fail("manifest_object_count_mismatch", "Manifest object_count is inconsistent")
    if type(value["total_object_bytes"]) is not int or value["total_object_bytes"] != total:
        _fail("manifest_total_bytes_mismatch", "Manifest total_object_bytes is inconsistent")

    return {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "bundle_id": value["bundle_id"],
        "canonicalization": CANONICALIZATION,
        "digest_algorithm": DIGEST_ALGORITHM,
        "transport": TRANSPORT,
        "sealed_at": value["sealed_at"],
        "operation_id": value["operation_id"],
        "source_installation_id": value["source_installation_id"],
        "source_authority_generation": value["source_authority_generation"],
        "source_post_once_version": value["source_post_once_version"],
        "source_revision": value["source_revision"],
        "source_state_registry_version": value["source_state_registry_version"],
        "object_count": len(descriptors),
        "total_object_bytes": total,
        "objects": descriptors,
    }


def _descriptor_from_prepared(row: PreparedObject, staged: Path) -> dict[str, Any]:
    logical_id = _validate_logical_id(row.logical_id)
    restore_class = _validate_restore_class(row.restore_class)
    media_type = _validate_media_type(row.media_type)
    schema_version = _validate_schema_version(row.schema_version, field="object schema_version", nullable=True)

    source = Path(row.source)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise BundleError("source_open_failed", f"Could not open prepared object {logical_id}") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            _fail("source_not_regular", f"Prepared object {logical_id} is not a regular file")
        if before.st_size > MAX_OBJECT_BYTES:
            _fail("object_size_invalid", f"Prepared object {logical_id} exceeds the v1 object limit")

        digest = hashlib.sha256()
        written = 0
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=".object-", dir=staged)
        try:
            with os.fdopen(tmp_fd, "wb") as output:
                while True:
                    chunk = os.read(fd, COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_OBJECT_BYTES:
                        _fail("object_size_invalid", f"Prepared object {logical_id} exceeds the v1 object limit")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(tmp_name, 0o600)
            after = os.fstat(fd)
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or written != before.st_size
            ):
                _fail("source_changed_during_seal", f"Prepared object {logical_id} changed while sealing")
            value = "sha256:" + digest.hexdigest()
            destination = staged / value.removeprefix("sha256:")
            os.replace(tmp_name, destination)
            os.chmod(destination, 0o600)
            return {
                "logical_id": logical_id,
                "restore_class": restore_class,
                "media_type": media_type,
                "digest": value,
                "size": written,
                "schema_version": schema_version,
            }
        finally:
            Path(tmp_name).unlink(missing_ok=True)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _seal_lock(parent: Path):
    try:
        import fcntl
    except ImportError as exc:
        raise BundleError("seal_lock_unsupported", "Transfer sealing requires POSIX file locking") from exc
    lock_path = parent / ".post-once-transfer.seal.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _require_seal_space(parent: Path, total_source_bytes: int) -> None:
    reserve = max(QUARANTINE_RESERVE_BYTES, total_source_bytes // 10)
    # Staged object bytes and the archive may coexist before the immutable name is published.
    required = (2 * total_source_bytes) + reserve
    if shutil.disk_usage(parent).free < required:
        _fail("seal_disk_space_insufficient", "Insufficient free space for crash-safe transfer sealing")


def _require_quarantine_space(parent: Path, total_object_bytes: int) -> None:
    reserve = max(QUARANTINE_RESERVE_BYTES, total_object_bytes // 10)
    if shutil.disk_usage(parent).free < total_object_bytes + reserve:
        _fail("quarantine_disk_space_insufficient", "Insufficient free space for transfer quarantine")


def _normalized_tarinfo(name: str, size: int) -> tarfile.TarInfo:
    if len(name.encode("ascii")) > MAX_MEMBER_NAME_BYTES:
        _fail("archive_member_name_too_long", "Transfer archive member name exceeds the v1 limit")
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.type = tarfile.REGTYPE
    return info


def _write_archive(temp_path: Path, manifest_raw: bytes, descriptors: list[dict[str, Any]], staged: Path) -> None:
    with temp_path.open("wb") as raw_output:
        with gzip.GzipFile(filename="", fileobj=raw_output, mode="wb", mtime=0) as gzip_output:
            with tarfile.open(fileobj=gzip_output, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                archive.addfile(_normalized_tarinfo(MANIFEST_NAME, len(manifest_raw)), io.BytesIO(manifest_raw))
                for descriptor in sorted(descriptors, key=lambda item: _object_path(item["digest"])):
                    member_name = _object_path(descriptor["digest"])
                    source = staged / descriptor["digest"].removeprefix("sha256:")
                    with source.open("rb") as handle:
                        archive.addfile(_normalized_tarinfo(member_name, descriptor["size"]), handle)
        raw_output.flush()
        os.fsync(raw_output.fileno())
    os.chmod(temp_path, 0o600)


def seal_bundle(
    output: str | Path,
    objects: Iterable[PreparedObject],
    *,
    operation_id: str,
    source_installation_id: str,
    source_authority_generation: int,
    source_post_once_version: str,
    source_revision: str,
    source_state_registry_version: int,
    bundle_id: str | None = None,
    sealed_at: datetime | None = None,
) -> dict[str, Any]:
    """Seal one already-prepared semantic object set into an immutable transfer artifact."""

    rows = list(objects)
    if len(rows) > MAX_OBJECTS:
        _fail("too_many_objects", "Transfer bundle has too many prepared objects")

    logical_ids = [_validate_logical_id(row.logical_id) for row in rows]
    if len(logical_ids) != len(set(logical_ids)):
        _fail("duplicate_logical_id", "Prepared object logical_id is duplicated")

    source_sizes = []
    for row in rows:
        source = Path(row.source)
        try:
            if source.is_symlink():
                _fail("source_not_regular", f"Prepared object {row.logical_id} may not be a symlink")
            size = source.stat().st_size
        except OSError as exc:
            raise BundleError("source_stat_failed", f"Could not inspect prepared object {row.logical_id}") from exc
        if size > MAX_OBJECT_BYTES:
            _fail("object_size_invalid", f"Prepared object {row.logical_id} exceeds the v1 object limit")
        source_sizes.append(size)

    total_source = sum(source_sizes)
    if total_source > MAX_TOTAL_OBJECT_BYTES:
        _fail("total_object_bytes_exceeded", "Prepared object bytes exceed the v1 total limit")

    final_path = Path(output).expanduser().resolve()
    parent = final_path.parent
    parent.mkdir(parents=True, exist_ok=True)

    bundle_uuid = _uuid4(bundle_id or str(uuid.uuid4()), field="bundle_id")
    operation_uuid = _uuid4(operation_id, field="operation_id")
    installation_uuid = _uuid4(source_installation_id, field="source_installation_id")
    _validate_schema_version(source_authority_generation, field="source_authority_generation")
    _validate_source_version(source_post_once_version)
    _validate_source_revision(source_revision)
    _validate_schema_version(source_state_registry_version, field="source_state_registry_version")

    with _seal_lock(parent):
        if final_path.exists():
            _fail("bundle_output_exists", "Immutable transfer bundle output already exists")
        _require_seal_space(parent, total_source)

        stage_root = Path(tempfile.mkdtemp(prefix=".post-once-transfer-stage-", dir=parent))
        stage_root.chmod(0o700)
        staged_objects = stage_root / "objects"
        staged_objects.mkdir(mode=0o700)
        temp_archive: Path | None = None

        try:
            descriptors = [_descriptor_from_prepared(row, staged_objects) for row in rows]
            descriptors.sort(key=lambda item: item["logical_id"])
            digests = [row["digest"] for row in descriptors]
            if len(digests) != len(set(digests)):
                _fail("duplicate_object_digest", "V1 requires one descriptor per unique object digest")
            total = sum(int(row["size"]) for row in descriptors)

            manifest = validate_manifest({
                "bundle_format_version": BUNDLE_FORMAT_VERSION,
                "bundle_id": bundle_uuid,
                "canonicalization": CANONICALIZATION,
                "digest_algorithm": DIGEST_ALGORITHM,
                "transport": TRANSPORT,
                "sealed_at": _utc_iso(sealed_at),
                "operation_id": operation_uuid,
                "source_installation_id": installation_uuid,
                "source_authority_generation": source_authority_generation,
                "source_post_once_version": source_post_once_version,
                "source_revision": source_revision,
                "source_state_registry_version": source_state_registry_version,
                "object_count": len(descriptors),
                "total_object_bytes": total,
                "objects": descriptors,
            })
            manifest_raw = canonical_manifest_bytes(manifest)
            manifest_path = stage_root / MANIFEST_NAME
            with manifest_path.open("wb") as handle:
                handle.write(manifest_raw)
                handle.flush()
                os.fsync(handle.fileno())
            manifest_path.chmod(0o600)
            _fsync_dir(staged_objects)
            _fsync_dir(stage_root)

            fd, temp_name = tempfile.mkstemp(prefix=f".{final_path.name}.", suffix=".tmp", dir=parent)
            os.close(fd)
            temp_archive = Path(temp_name)
            _write_archive(temp_archive, manifest_raw, descriptors, staged_objects)

            archive_size = temp_archive.stat().st_size
            if archive_size > MAX_COMPRESSED_BYTES:
                _fail("compressed_bundle_too_large", "Compressed transfer bundle exceeds the v1 limit")
            bundle_digest = _sha256_digest_file(temp_archive)
            manifest_digest = _sha256_digest_bytes(manifest_raw)

            try:
                os.link(temp_archive, final_path)
            except FileExistsError as exc:
                raise BundleError("bundle_output_exists", "Immutable transfer bundle output already exists") from exc
            except OSError as exc:
                if exc.errno in {
                    errno.EXDEV,
                    errno.EPERM,
                    errno.EACCES,
                    getattr(errno, "EOPNOTSUPP", errno.EPERM),
                    getattr(errno, "ENOTSUP", errno.EPERM),
                }:
                    raise BundleError(
                        "immutable_commit_unsupported",
                        "Destination filesystem cannot provide the v1 no-overwrite seal commit",
                    ) from exc
                raise
            final_path.chmod(0o600)
            _fsync_dir(parent)
            temp_archive.unlink()
            temp_archive = None
            _fsync_dir(parent)

            return {
                "schema_version": 1,
                "status": "sealed",
                "bundle_id": bundle_uuid,
                "operation_id": operation_uuid,
                "source_installation_id": installation_uuid,
                "source_authority_generation": source_authority_generation,
                "sealed_at": manifest["sealed_at"],
                "manifest_sha256": manifest_digest,
                "bundle_sha256": bundle_digest,
                "archive_bytes": archive_size,
                "object_count": manifest["object_count"],
                "total_object_bytes": manifest["total_object_bytes"],
                "output": str(final_path),
                "integrity": "sealed",
                "source_continuity": "not_established",
                "freshness": "not_established",
                "authenticity": "not_provided",
                "boundary": (
                    "Prepared semantic objects only. No live-state drain, provider call, restore promotion, "
                    "service mutation or publication authority was attempted."
                ),
            }
        finally:
            if temp_archive is not None:
                temp_archive.unlink(missing_ok=True)
            shutil.rmtree(stage_root, ignore_errors=True)


def _read_limited(handle: IO[bytes], limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = handle.read(min(COPY_CHUNK_BYTES, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            _fail("archive_member_too_large", "Archive member exceeds its v1 read limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_tar_member(member: tarfile.TarInfo, *, expected_name: str | None = None) -> None:
    try:
        encoded = member.name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise BundleError("archive_member_name_invalid", "Archive member name must be ASCII") from exc
    if len(encoded) > MAX_MEMBER_NAME_BYTES:
        _fail("archive_member_name_too_long", "Archive member name exceeds the v1 limit")
    if expected_name is not None and member.name != expected_name:
        _fail("unexpected_archive_member", f"Unexpected archive member: {member.name}")
    if not member.isfile():
        _fail("archive_member_not_regular", f"Archive member is not a regular file: {member.name}")


def _write_verified_object(
    source: IO[bytes],
    *,
    descriptor: dict[str, Any],
    destination: Path,
) -> None:
    expected_size = int(descriptor["size"])
    expected_digest = str(descriptor["digest"])
    digest = hashlib.sha256()
    written = 0

    fd, temp_name = tempfile.mkstemp(prefix=".object-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            while True:
                chunk = source.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > expected_size:
                    _fail("archive_member_size_mismatch", "Archive object exceeded its declared size")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp_name, 0o600)
        if written != expected_size:
            _fail("archive_member_size_mismatch", "Archive object did not match its declared size")
        observed = "sha256:" + digest.hexdigest()
        if observed != expected_digest:
            _fail("object_digest_mismatch", "Archive object digest does not match the manifest")
        if destination.exists():
            _fail("archive_member_duplicate", "Archive repeats a content-addressed object")
        os.replace(temp_name, destination)
        os.chmod(destination, 0o600)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def verify_to_quarantine(
    bundle: str | Path,
    quarantine_parent: str | Path,
    *,
    expected_bundle_sha256: str | None = None,
) -> dict[str, Any]:
    """Stream-verify one bundle and materialize only verified objects into fresh quarantine."""

    bundle_path = Path(bundle).expanduser().resolve()
    if not bundle_path.is_file():
        _fail("bundle_not_found", "Transfer bundle does not exist")
    compressed_size = bundle_path.stat().st_size
    if compressed_size > MAX_COMPRESSED_BYTES:
        _fail("compressed_bundle_too_large", "Compressed transfer bundle exceeds the v1 limit")

    observed_bundle_digest = _sha256_digest_file(bundle_path)
    if expected_bundle_sha256 is not None:
        expected_bundle_digest = _validate_digest(expected_bundle_sha256, field="expected_bundle_sha256")
        if expected_bundle_digest != observed_bundle_digest:
            _fail("bundle_digest_mismatch", "Exact transfer bundle digest does not match the expected value")

    parent = Path(quarantine_parent).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    quarantine = Path(tempfile.mkdtemp(prefix=".post-once-transfer-quarantine-", dir=parent))
    quarantine.chmod(0o700)
    objects_root = quarantine / "objects" / "sha256"
    objects_root.mkdir(parents=True, mode=0o700)

    try:
        try:
            with tarfile.open(bundle_path, mode="r|gz") as archive:
                first = archive.next()
                if first is None:
                    _fail("archive_empty", "Transfer archive is empty")
                _safe_tar_member(first, expected_name=MANIFEST_NAME)
                if first.size > MAX_MANIFEST_BYTES:
                    _fail("manifest_too_large", "Transfer manifest exceeds the v1 size limit")
                manifest_stream = archive.extractfile(first)
                if manifest_stream is None:
                    _fail("manifest_unreadable", "Transfer manifest could not be read")
                manifest_raw = _read_limited(manifest_stream, MAX_MANIFEST_BYTES)
                if len(manifest_raw) != first.size:
                    _fail("manifest_size_mismatch", "Transfer manifest size does not match its tar header")
                manifest = parse_manifest_bytes(manifest_raw)
                canonical = canonical_manifest_bytes(manifest)
                if canonical != manifest_raw:
                    _fail("manifest_not_canonical", "Transfer manifest bytes are not canonical Post-Once JSON v1")

                _require_quarantine_space(parent, int(manifest["total_object_bytes"]))
                expected_members: dict[str, dict[str, Any]] = {
                    _object_path(row["digest"]): row for row in manifest["objects"]
                }
                seen: set[str] = set()

                while True:
                    member = archive.next()
                    if member is None:
                        break
                    _safe_tar_member(member)
                    descriptor = expected_members.get(member.name)
                    if descriptor is None:
                        _fail("unexpected_archive_member", f"Unexpected archive member: {member.name}")
                    if member.name in seen:
                        _fail("archive_member_duplicate", "Transfer archive repeats an object member")
                    if member.size != descriptor["size"]:
                        _fail("archive_member_size_mismatch", "Archive member size disagrees with manifest")
                    seen.add(member.name)
                    stream = archive.extractfile(member)
                    if stream is None:
                        _fail("archive_member_unreadable", "Transfer object could not be read")
                    destination = objects_root / descriptor["digest"].removeprefix("sha256:")
                    _write_verified_object(stream, descriptor=descriptor, destination=destination)
        except BundleError:
            raise
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise BundleError("archive_invalid", "Transfer archive could not be safely parsed") from exc

        missing = sorted(set(expected_members) - seen)
        if missing:
            _fail("archive_objects_missing", "Transfer archive is missing declared objects")

        manifest_path = quarantine / MANIFEST_NAME
        with manifest_path.open("wb") as handle:
            handle.write(manifest_raw)
            handle.flush()
            os.fsync(handle.fileno())
        manifest_path.chmod(0o600)

        manifest_digest = _sha256_digest_bytes(manifest_raw)
        verification = {
            "schema_version": 1,
            "status": "verified",
            "bundle_id": manifest["bundle_id"],
            "operation_id": manifest["operation_id"],
            "source_installation_id": manifest["source_installation_id"],
            "source_authority_generation": manifest["source_authority_generation"],
            "sealed_at": manifest["sealed_at"],
            "manifest_sha256": manifest_digest,
            "bundle_sha256": observed_bundle_digest,
            "archive_bytes": compressed_size,
            "object_count": manifest["object_count"],
            "total_object_bytes": manifest["total_object_bytes"],
            "integrity": "verified",
            "source_continuity": "not_established",
            "freshness": "not_established",
            "authenticity": "not_provided",
            "boundary": "Verified quarantine only. No live restore or authority transition was attempted.",
        }
        verification_path = quarantine / "VERIFICATION.json"
        raw = json.dumps(verification, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        with verification_path.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        verification_path.chmod(0o600)
        _fsync_dir(objects_root)
        _fsync_dir(objects_root.parent)
        _fsync_dir(quarantine)

        return {**verification, "quarantine": str(quarantine)}
    except Exception:
        shutil.rmtree(quarantine, ignore_errors=True)
        raise


def new_transform_provenance(
    *,
    logical_id: str,
    source_manifest_sha256: str,
    source_object_sha256: str,
    output_object_sha256: str,
    from_schema_version: int,
    to_schema_version: int,
    transformer_id: str,
    transformer_version: str,
    transformed_at: datetime | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """Build provenance for a future explicit schema transform without applying one."""

    logical = _validate_logical_id(logical_id)
    source_manifest = _validate_digest(source_manifest_sha256, field="source_manifest_sha256")
    source_object = _validate_digest(source_object_sha256, field="source_object_sha256")
    output_object = _validate_digest(output_object_sha256, field="output_object_sha256")
    source_version = _validate_schema_version(from_schema_version, field="from_schema_version")
    target_version = _validate_schema_version(to_schema_version, field="to_schema_version")
    if source_version == target_version:
        _fail("transform_schema_unchanged", "Schema transform must change the schema version")
    transform_id = _ascii(transformer_id, field="transformer_id", maximum=120)
    transform_version = _ascii(transformer_version, field="transformer_version", maximum=64)
    warning_rows = list(warnings)
    if len(warning_rows) > 50:
        _fail("transform_warnings_exceeded", "Too many transform warnings")
    safe_warnings = [_ascii(row, field="transform_warning", maximum=500) for row in warning_rows]

    return {
        "schema_version": 1,
        "logical_id": logical,
        "source_manifest_sha256": source_manifest,
        "source_object_sha256": source_object,
        "output_object_sha256": output_object,
        "from_schema_version": source_version,
        "to_schema_version": target_version,
        "transformer_id": transform_id,
        "transformer_version": transform_version,
        "transformed_at": _utc_iso(transformed_at),
        "warnings": safe_warnings,
        "boundary": "Provenance only. No implicit schema transformation was selected or applied.",
    }
