from __future__ import annotations

from datetime import datetime, timezone
import errno
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from ocpf_post.transfer_bundle import (
    BundleError,
    MANIFEST_NAME,
    OBJECT_PREFIX,
    PreparedObject,
    canonical_manifest_bytes,
    new_transform_provenance,
    parse_manifest_bytes,
    seal_bundle,
    verify_to_quarantine,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)
REVISION = "a" * 40
FORMAT_CHECKER = FormatChecker()
ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas" / "setup-recovery" / "v1"


def sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def schema(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def fixed_ids() -> tuple[str, str, str]:
    return (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    )


def descriptor(logical_id: str, payload: bytes, *, media_type: str = "application/json") -> dict:
    return {
        "logical_id": logical_id,
        "restore_class": "historical",
        "media_type": media_type,
        "digest": sha(payload),
        "size": len(payload),
        "schema_version": 1,
    }


def manifest_for(objects: list[dict]) -> dict:
    bundle_id, operation_id, installation_id = fixed_ids()
    rows = sorted(objects, key=lambda row: row["logical_id"])
    return {
        "bundle_format_version": 1,
        "bundle_id": bundle_id,
        "canonicalization": "post-once-canonical-json-v1",
        "digest_algorithm": "sha256",
        "transport": "ustar+gzip",
        "sealed_at": "2026-09-25T11:00:00Z",
        "operation_id": operation_id,
        "source_installation_id": installation_id,
        "source_authority_generation": 4,
        "source_post_once_version": "0.28.29",
        "source_revision": REVISION,
        "source_state_registry_version": 1,
        "object_count": len(rows),
        "total_object_bytes": sum(row["size"] for row in rows),
        "objects": rows,
    }


def add_regular(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))


def write_archive(path: Path, manifest: dict, members: list[tuple[str, bytes, str]]) -> None:
    raw_manifest = canonical_manifest_bytes(manifest)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                add_regular(archive, MANIFEST_NAME, raw_manifest)
                for name, payload, kind in members:
                    if kind == "regular":
                        add_regular(archive, name, payload)
                    elif kind == "symlink":
                        info = tarfile.TarInfo(name)
                        info.type = tarfile.SYMTYPE
                        info.linkname = "../escape"
                        info.mode = 0o600
                        info.mtime = 0
                        archive.addfile(info)
                    else:
                        raise AssertionError(kind)


class TransferBundleRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sources = self.root / "prepared"
        self.sources.mkdir()
        self.quarantines = self.root / "quarantine"
        self.quarantines.mkdir()
        self.bundle_id, self.operation_id, self.installation_id = fixed_ids()

    def tearDown(self):
        self.tmp.cleanup()

    def _seal(self, output: Path | None = None):
        receipt = self.sources / "receipts.jsonl"
        receipt.write_text('{"status":"published_verified"}\n', encoding="utf-8")
        policy = self.sources / "policy.json"
        policy.write_text('{"daily":20}\n', encoding="utf-8")
        return seal_bundle(
            output or (self.root / "transfer.tar.gz"),
            [
                PreparedObject(
                    "historical/publish-receipts",
                    "historical",
                    "application/x-ndjson",
                    receipt,
                    1,
                ),
                PreparedObject(
                    "configuration/portfolio-policy",
                    "configuration",
                    "application/json",
                    policy,
                    1,
                ),
            ],
            operation_id=self.operation_id,
            source_installation_id=self.installation_id,
            source_authority_generation=4,
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            source_state_registry_version=1,
            bundle_id=self.bundle_id,
            sealed_at=NOW,
        )

    def test_seal_and_stream_verify_round_trip(self):
        sealed = self._seal()
        self.assertEqual(sealed["status"], "sealed")
        self.assertEqual(sealed["source_continuity"], "not_established")
        self.assertEqual(sealed["freshness"], "not_established")
        self.assertEqual(sealed["authenticity"], "not_provided")
        self.assertRegex(sealed["manifest_sha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(sealed["bundle_sha256"], r"^sha256:[0-9a-f]{64}$")

        verified = verify_to_quarantine(
            sealed["output"],
            self.quarantines,
            expected_bundle_sha256=sealed["bundle_sha256"],
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["manifest_sha256"], sealed["manifest_sha256"])
        self.assertEqual(verified["bundle_sha256"], sealed["bundle_sha256"])
        self.assertEqual(verified["integrity"], "verified")
        self.assertEqual(verified["source_continuity"], "not_established")
        self.assertEqual(verified["freshness"], "not_established")
        self.assertEqual(verified["authenticity"], "not_provided")

        quarantine = Path(verified["quarantine"])
        self.assertTrue((quarantine / "manifest.json").is_file())
        self.assertTrue((quarantine / "VERIFICATION.json").is_file())
        objects = sorted((quarantine / "objects" / "sha256").iterdir())
        self.assertEqual(len(objects), 2)
        self.assertTrue(all(path.is_file() and len(path.name) == 64 for path in objects))
        self.assertFalse(any(path.is_symlink() for path in quarantine.rglob("*")))

    def test_archive_namespace_is_fixed_and_content_addressed(self):
        sealed = self._seal()
        with tarfile.open(sealed["output"], "r:gz") as archive:
            names = archive.getnames()
            members = archive.getmembers()
        self.assertEqual(names[0], "manifest.json")
        self.assertTrue(all(name == "manifest.json" or name.startswith(OBJECT_PREFIX) for name in names))
        self.assertTrue(all(member.isfile() for member in members))
        self.assertTrue(all(member.mode == 0o600 for member in members))
        self.assertTrue(all(member.uid == 0 and member.gid == 0 for member in members))
        self.assertTrue(all(member.mtime == 0 for member in members))

    def test_seal_is_no_overwrite(self):
        output = self.root / "immutable.tar.gz"
        first = self._seal(output)
        original_digest = sha(output.read_bytes())
        with self.assertRaises(BundleError) as caught:
            self._seal(output)
        self.assertEqual(caught.exception.code, "bundle_output_exists")
        self.assertEqual(sha(output.read_bytes()), original_digest)
        self.assertEqual(first["bundle_sha256"], original_digest)

    def test_unsupported_no_overwrite_commit_fails_closed(self):
        output = self.root / "unsupported.tar.gz"
        with patch("ocpf_post.transfer_bundle.os.link", side_effect=OSError(errno.EXDEV, "cross-device")):
            with self.assertRaises(BundleError) as caught:
                self._seal(output)
        self.assertEqual(caught.exception.code, "immutable_commit_unsupported")
        self.assertFalse(output.exists())


class CanonicalManifestTests(unittest.TestCase):
    def test_canonical_bytes_are_independent_of_dict_insertion_order(self):
        value = manifest_for([descriptor("historical/a", b"a")])
        reversed_value = dict(reversed(list(value.items())))
        self.assertEqual(canonical_manifest_bytes(value), canonical_manifest_bytes(reversed_value))

    def test_manifest_rejects_float_unicode_and_duplicate_json_keys(self):
        value = manifest_for([descriptor("historical/a", b"a")])
        value["source_authority_generation"] = 1.5
        with self.assertRaises(BundleError) as caught:
            canonical_manifest_bytes(value)
        self.assertEqual(caught.exception.code, "manifest_schema_invalid")

        value = manifest_for([descriptor("historical/a", b"a")])
        value["source_post_once_version"] = "vé"
        with self.assertRaises(BundleError) as caught:
            canonical_manifest_bytes(value)
        self.assertEqual(caught.exception.code, "manifest_schema_invalid")

        duplicate = b'{"bundle_format_version":1,"bundle_format_version":1}'
        with self.assertRaises(BundleError) as caught:
            parse_manifest_bytes(duplicate)
        self.assertEqual(caught.exception.code, "manifest_duplicate_key")

    def test_manifest_schema_accepts_runtime_canonical_contract(self):
        value = manifest_for([descriptor("historical/a", b"a")])
        parsed = parse_manifest_bytes(canonical_manifest_bytes(value))
        Draft202012Validator(
            schema("transfer-manifest.schema.json"),
            format_checker=FORMAT_CHECKER,
        ).validate(parsed)


class MaliciousArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.quarantine = self.root / "quarantine"
        self.quarantine.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _verify_failure(self, archive: Path, expected_code: str):
        before = set(self.quarantine.iterdir())
        with self.assertRaises(BundleError) as caught:
            verify_to_quarantine(archive, self.quarantine)
        self.assertEqual(caught.exception.code, expected_code)
        self.assertEqual(set(self.quarantine.iterdir()), before)

    def test_symlink_and_path_traversal_members_are_never_materialized(self):
        payload = b"x"
        row = descriptor("historical/a", payload)
        object_name = OBJECT_PREFIX + row["digest"].split(":", 1)[1]

        symlink_archive = self.root / "symlink.tar.gz"
        write_archive(symlink_archive, manifest_for([row]), [(object_name, b"", "symlink")])
        self._verify_failure(symlink_archive, "archive_member_not_regular")

        traversal_archive = self.root / "traversal.tar.gz"
        write_archive(traversal_archive, manifest_for([row]), [("../escape", payload, "regular")])
        self._verify_failure(traversal_archive, "unexpected_archive_member")
        self.assertFalse((self.root / "escape").exists())

    def test_digest_mismatch_and_missing_objects_fail_closed(self):
        payload = b"x"
        row = descriptor("historical/a", payload)
        object_name = OBJECT_PREFIX + row["digest"].split(":", 1)[1]

        wrong = self.root / "wrong.tar.gz"
        write_archive(wrong, manifest_for([row]), [(object_name, b"y", "regular")])
        self._verify_failure(wrong, "object_digest_mismatch")

        missing = self.root / "missing.tar.gz"
        write_archive(missing, manifest_for([row]), [])
        self._verify_failure(missing, "archive_objects_missing")

    def test_extra_member_and_exact_bundle_digest_mismatch_fail_closed(self):
        payload = b"x"
        row = descriptor("historical/a", payload)
        object_name = OBJECT_PREFIX + row["digest"].split(":", 1)[1]
        archive = self.root / "extra.tar.gz"
        write_archive(
            archive,
            manifest_for([row]),
            [(object_name, payload, "regular"), ("evil", b"extra", "regular")],
        )
        self._verify_failure(archive, "unexpected_archive_member")

        good = self.root / "good.tar.gz"
        write_archive(good, manifest_for([row]), [(object_name, payload, "regular")])
        with self.assertRaises(BundleError) as caught:
            verify_to_quarantine(good, self.quarantine, expected_bundle_sha256="sha256:" + ("0" * 64))
        self.assertEqual(caught.exception.code, "bundle_digest_mismatch")


class TransformProvenanceTests(unittest.TestCase):
    def test_transform_provenance_is_explicit_and_schema_valid(self):
        record = new_transform_provenance(
            logical_id="configuration/portfolio-policy",
            source_manifest_sha256="sha256:" + ("1" * 64),
            source_object_sha256="sha256:" + ("2" * 64),
            output_object_sha256="sha256:" + ("3" * 64),
            from_schema_version=1,
            to_schema_version=2,
            transformer_id="portfolio-policy-v1-to-v2",
            transformer_version="1.0.0",
            transformed_at=NOW,
            warnings=("field x is intentionally omitted",),
        )
        self.assertIn("No implicit schema transformation", record["boundary"])
        Draft202012Validator(
            schema("transform-provenance.schema.json"),
            format_checker=FORMAT_CHECKER,
        ).validate(record)

    def test_transform_provenance_refuses_noop_version_change(self):
        with self.assertRaises(BundleError) as caught:
            new_transform_provenance(
                logical_id="configuration/portfolio-policy",
                source_manifest_sha256="sha256:" + ("1" * 64),
                source_object_sha256="sha256:" + ("2" * 64),
                output_object_sha256="sha256:" + ("3" * 64),
                from_schema_version=1,
                to_schema_version=1,
                transformer_id="noop",
                transformer_version="1",
                transformed_at=NOW,
            )
        self.assertEqual(caught.exception.code, "transform_schema_unchanged")


if __name__ == "__main__":
    unittest.main()
