from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.setup_store import (
    DB_NAME,
    SetupStore,
    SetupStoreError,
    default_workspace,
    resolve_workspace,
)


class SetupStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            name: os.environ.get(name)
            for name in (
                "OCPF_POST_SETUP_STATE_DIR",
                "OCPF_POST_STATE_DIR",
                "OCPF_POST_CONFIG_DIR",
                "XDG_STATE_HOME",
            )
        }
        os.environ["OCPF_POST_STATE_DIR"] = str(self.root / "production-state")
        os.environ["OCPF_POST_CONFIG_DIR"] = str(self.root / "production-config")
        os.environ.pop("OCPF_POST_SETUP_STATE_DIR", None)
        os.environ["XDG_STATE_HOME"] = str(self.root / "xdg-state")

    def tearDown(self) -> None:
        for name, value in self.env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def workspace(self, name: str = "setup") -> Path:
        return self.root / name

    def test_default_workspace_is_bootstrap_specific_xdg_state(self) -> None:
        self.assertEqual(
            default_workspace(),
            (self.root / "xdg-state" / "oneclickpostfactory" / "post-once-bootstrap" / "setup").resolve(),
        )
        self.assertNotIn("/post-once/setup", str(default_workspace()))

    def test_relative_xdg_state_is_ignored(self) -> None:
        os.environ["XDG_STATE_HOME"] = "relative-state"
        os.environ.pop("OCPF_POST_SETUP_STATE_DIR", None)
        expected = (
            Path.home() / ".local" / "state" / "oneclickpostfactory" / "post-once-bootstrap" / "setup"
        ).resolve()
        self.assertEqual(default_workspace(), expected)

    def test_environment_override_must_be_absolute(self) -> None:
        os.environ["OCPF_POST_SETUP_STATE_DIR"] = "relative"
        with self.assertRaises(SetupStoreError) as caught:
            default_workspace()
        self.assertEqual(caught.exception.code, "setup.workspace.invalid")

    def test_production_state_or_config_overlap_is_rejected(self) -> None:
        with self.assertRaises(SetupStoreError) as caught:
            resolve_workspace(self.root / "production-state" / "bootstrap")
        self.assertEqual(caught.exception.code, "setup.workspace.production_collision")
        with self.assertRaises(SetupStoreError) as caught:
            resolve_workspace(self.root)
        self.assertEqual(caught.exception.code, "setup.workspace.production_collision")

    def test_writable_store_uses_durable_profile_and_private_permissions(self) -> None:
        store = SetupStore(self.workspace())
        with store.writable() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 3)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA trusted_schema").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(stat.S_IMODE(store.workspace.stat().st_mode) & 0o077, 0)
        self.assertEqual(stat.S_IMODE(store.db_path.stat().st_mode) & 0o077, 0)

    def test_known_remote_filesystem_fails_closed(self) -> None:
        store = SetupStore(self.workspace())
        with patch("ocpf_post.setup_store.linux_filesystem_type", return_value="nfs4"):
            with self.assertRaises(SetupStoreError) as caught:
                store.create_explore()
        self.assertEqual(caught.exception.code, "setup.workspace.remote_filesystem")

    def test_fresh_start_atomically_creates_operation_installation_and_session(self) -> None:
        store = SetupStore(self.workspace())
        created = store.create_fresh("Example Ltd", machine_label="fresh-host")
        operation = store.operation(created["operation"]["operation_id"])
        installation = store.installation_for_operation(operation["operation_id"])
        session = store.session(created["session"]["session_id"])
        events = store.events(session["session_id"])

        self.assertEqual(operation["status"], "prepared")
        self.assertEqual(installation["status"], "candidate")
        self.assertIsNone(installation["authority_generation"])
        self.assertEqual(session["stage"], "created")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "setup_started")

    def test_explore_start_creates_no_operation_or_installation(self) -> None:
        store = SetupStore(self.workspace())
        created = store.create_explore()
        self.assertIsNone(created["operation"])
        self.assertIsNone(created["installation"])
        with store.readonly() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM operations").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM installations").fetchone()[0], 0)

    def test_open_session_prevents_silent_second_setup(self) -> None:
        store = SetupStore(self.workspace())
        store.create_explore()
        with self.assertRaises(SetupStoreError) as caught:
            store.create_fresh("Another owner")
        self.assertEqual(caught.exception.code, "setup.session.open_exists")

    def test_transition_revision_conflict_fails_without_event(self) -> None:
        store = SetupStore(self.workspace())
        session = store.create_explore()["session"]
        first = store.transition(
            session["session_id"],
            expected_revision=1,
            next_stage="preflight_ready",
        )
        before = len(store.events(session["session_id"]))
        with self.assertRaises(SetupStoreError) as caught:
            store.transition(
                session["session_id"],
                expected_revision=1,
                next_stage="preflight_ready",
            )
        self.assertEqual(caught.exception.code, "setup.revision.conflict")
        self.assertEqual(store.session(session["session_id"])["revision"], first["revision"])
        self.assertEqual(len(store.events(session["session_id"])), before)

    def test_transition_and_event_roll_back_together_on_failure(self) -> None:
        store = SetupStore(self.workspace())
        session = store.create_explore()["session"]
        before = store.session(session["session_id"])
        before_events = list(store.events(session["session_id"]))
        with patch.object(store, "_insert_event", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                store.transition(
                    session["session_id"],
                    expected_revision=before["revision"],
                    next_stage="preflight_ready",
                )
        after = store.session(session["session_id"])
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["stage"], before["stage"])
        self.assertEqual(store.events(session["session_id"]), before_events)

    def test_configuration_and_transition_roll_back_together_on_failure(self) -> None:
        store = SetupStore(self.workspace())
        session = store.create_explore()["session"]
        session = store.transition(
            session["session_id"], expected_revision=1, next_stage="preflight_ready"
        )
        session = store.transition(
            session["session_id"], expected_revision=2, next_stage="operation_ready"
        )
        with patch.object(store, "_insert_event", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                store.configure_and_transition(
                    session["session_id"],
                    expected_revision=3,
                    timezone="Europe/London",
                    pace_profile="regular",
                    daily_originals=5,
                )
        self.assertEqual(store.session(session["session_id"])["stage"], "operation_ready")
        self.assertIsNone(store.config(session["session_id"]))

    def test_unknown_newer_store_schema_blocks(self) -> None:
        store = SetupStore(self.workspace())
        with store.writable():
            pass
        connection = sqlite3.connect(store.db_path)
        connection.execute("PRAGMA user_version=99")
        connection.commit()
        connection.close()
        with self.assertRaises(SetupStoreError) as caught:
            with store.writable():
                pass
        self.assertEqual(caught.exception.code, "setup.store.schema_newer")

    def test_health_checks_database_and_foreign_keys(self) -> None:
        store = SetupStore(self.workspace())
        store.create_explore()
        health = store.health()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["quick_check"], ["ok"])
        self.assertEqual(health["foreign_key_violation_count"], 0)
        self.assertEqual(health["journal_mode"], "delete")

    def test_status_path_does_not_create_missing_database(self) -> None:
        store = SetupStore(self.workspace("absent"))
        with self.assertRaises(SetupStoreError) as caught:
            store.latest_session()
        self.assertEqual(caught.exception.code, "setup.store.missing")
        self.assertFalse(store.workspace.exists())
        self.assertFalse((store.workspace / DB_NAME).exists())


if __name__ == "__main__":
    unittest.main()
