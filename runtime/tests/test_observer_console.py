from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ocpf_post import dispatch, observer, observer_cli, observer_server


class ObserverProjectionTests(unittest.TestCase):
    def test_schedule_and_receipt_projection_redacts_copy_and_provider_prose(self):
        source = {
            "schedule_id": "sch_1", "status": "published_verified", "campaign": "TEST-1",
            "provider": "x", "account_id": "123", "recorded_at": "2026-09-17T12:00:00Z",
            "text": "private social copy", "detail": "raw provider response with secrets",
            "post_id": "9", "url": "https://x.com/example/status/9", "readback_verified": True,
        }
        schedule = observer._schedule_projection(source)
        receipt = observer._receipt_projection(source)
        event = observer._event_projection({**source, "event": "completed"})
        for value in (schedule, receipt, event):
            self.assertNotIn("text", value)
            self.assertNotIn("detail", value)
            self.assertNotIn("raw provider response", json.dumps(value))

    def test_build_snapshot_is_local_read_only_and_does_not_call_provider_factories(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = patch.dict(os.environ, {
            "OCPF_POST_CONFIG_DIR": str(root / "config"),
            "OCPF_POST_STATE_DIR": str(root / "state"),
        })
        env.start(); self.addCleanup(env.stop)

        from ocpf_post import providers
        before = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        with patch.object(providers, "get_provider", side_effect=AssertionError("provider call forbidden")), \
             patch.object(providers, "for_account", side_effect=AssertionError("provider call forbidden")), \
             patch.object(providers, "for_campaign", side_effect=AssertionError("provider call forbidden")), \
             patch.object(observer, "_local_git", return_value={"checkout_clean": True, "network_checked": False}), \
             patch.object(observer, "_timers", return_value={"status": "observed", "timers": []}):
            snapshot = observer.build_snapshot()
        after = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        self.assertEqual(before, after)
        self.assertEqual(snapshot["consequence"], "READ_ONLY")
        self.assertTrue(snapshot["local_only"])
        self.assertEqual(snapshot["streaming"], "server-sent-events")
        self.assertEqual(snapshot["runtime"]["network_checked"], False)
        self.assertEqual(snapshot["performance"]["network_checked"], False)
        self.assertFalse(snapshot["consistency"]["atomic"])
        self.assertEqual(snapshot["consistency"]["model"], "sequential_local_read_model")
        self.assertIn("not a transactional state generation", snapshot["consistency"]["meaning"])

    def test_replenishment_projection_exposes_supply_reserve_without_network_work(self):
        reserve = {
            "routes": [
                {
                    "project": "project-a",
                    "provider": "x",
                    "reserve_status": "emergency",
                    "reserve_available_items": 1,
                    "reserve_runway_days": 1.0,
                },
            ],
            "status_counts": {"emergency": 1},
            "api_fallback_enabled": True,
        }
        with patch("ocpf_post.campaigns.runtime_campaign_root", return_value=Path("/missing-runtime-campaigns")), \
             patch("ocpf_post.replenisher.source_state_file", return_value=Path("/missing-source-state")), \
             patch("ocpf_post.replenisher.replenisher_status", return_value={"supply_reserve": reserve}), \
             patch("ocpf_post.source_observations.load", return_value={"projects": {}}), \
             patch("ocpf_post.state.read_json", return_value={}):
            value = observer._replenishment()

        self.assertEqual(value["supply_reserve"], reserve)
        self.assertFalse(value["network_checked"])

    def test_campaign_origin_is_bounded_and_omits_source_identifier(self):
        manifest = {
            "campaign": "TEST-1",
            "project": "proof-and-state",
            "title": "Proof",
            "source": {
                "type": "owner_approved",
                "source_id": "internal-source-reference",
                "repository": "AyobamiH/proof-and-state",
                "path": "README.md",
            },
            "vault": {"id": "proof-and-state-gtm", "base_campaign": "PAS-1"},
            "allocation": {"lane": "proof"},
        }
        with patch("ocpf_post.campaigns.builtin_manifest", return_value=manifest):
            value = observer._campaign_origin("TEST-1")
        self.assertEqual(value["project"], "proof-and-state")
        self.assertEqual(value["vault_id"], "proof-and-state-gtm")
        self.assertEqual(value["source_repository"], "AyobamiH/proof-and-state")
        self.assertNotIn("source_id", value)

    def test_engagement_worker_projection_exposes_modes_not_account_settings(self):
        report = {
            "schema_version": 1,
            "counts": {"pending": 1},
            "polls": {},
            "linkedin": {},
            "boundary": "test",
            "items": [],
        }
        worker = {
            "enabled": True,
            "accounts": {
                "x:123": {"mode": "automatic"},
                "threads:456": {"mode": "review"},
            },
            "model_credential_present": True,
            "opt_out_count": 2,
        }
        with patch("ocpf_post.engagement.report", return_value=report), \
             patch("ocpf_post.reply_worker.report", return_value=worker):
            value = observer._engagement()

        self.assertEqual(
            value["worker"]["provider_mode_counts"],
            {"x:automatic": 1, "threads:review": 1},
        )
        self.assertTrue(value["worker"]["model_ready"])
        self.assertNotIn("accounts", value["worker"])

    def test_performance_projection_uses_stored_metrics_without_error_prose(self):
        from ocpf_post import performance
        row = {
            "campaign": "TEST-1", "project": "test", "provider": "x", "account_id": "123",
            "post_id": "9", "captured_at": "2026-09-17T12:00:00Z", "metrics": {"likes": 4, "impressions": 120},
            "availability": {"status": "available", "detail": "raw provider prose", "unavailable_metrics": ["bookmarks"]},
        }
        with patch.object(performance, "iter_snapshots", return_value=[row]), \
             patch.object(performance, "get_extended_provider", side_effect=AssertionError("capture forbidden")):
            value = observer._performance()
        self.assertEqual(value["snapshot_count"], 1)
        self.assertEqual(value["recent"][0]["metrics"]["impressions"], 120)
        self.assertNotIn("detail", value["recent"][0]["availability"])
        self.assertNotIn("raw provider prose", json.dumps(value))

    def test_activity_projects_receipt_summary_from_existing_scan(self):
        schedules = []
        receipts = [
            {"campaign": "A", "provider": "x", "status": "published_verified",
             "recorded_at": "2026-09-21T09:00:00Z", "readback_verified": True},
            {"campaign": "B", "provider": "linkedin", "status": "published_unverified",
             "recorded_at": "2026-09-21T09:01:00Z", "readback_verified": False},
        ]
        with patch("ocpf_post.scheduler.schedule_records", return_value=schedules), \
             patch("ocpf_post.scheduler.iter_schedule_events", return_value=[]), \
             patch("ocpf_post.state.iter_receipts", return_value=iter(receipts)), \
             patch("ocpf_post.performance_review.publications", return_value={}), \
             patch("ocpf_post.portfolio.load_policy", return_value={"timezone": "Europe/London"}):
            value = observer._activity()

        self.assertEqual(
            value["receipt_summary"],
            {"integrity": "observed", "published_effects": 2, "verified_effects": 1},
        )

    def test_revision_excludes_observation_timestamp(self):
        fixed = {
            "status": {"targets": {}},
            "plan": {"plan": [], "capacity": {}, "generated_at": "fixed"},
        }
        common = [
            patch.object(observer, "_local_git", return_value={"checkout_clean": True}),
            patch.object(observer, "_portfolio", return_value=fixed),
            patch.object(observer, "_activity", return_value={}),
            patch.object(observer, "_replenishment", return_value={}),
            patch.object(observer, "_performance", return_value={}),
            patch.object(observer, "_feedback", return_value={}),
            patch.object(observer, "_timers", return_value={}),
            patch("ocpf_post.capacity_experiment.report", return_value={}),
            patch("ocpf_post.engagement.report", return_value={}),
            patch("ocpf_post.queue_watch.report", return_value={}),
            patch("ocpf_post.capabilities.report", return_value={}),
            patch("ocpf_post.state_registry.verify", side_effect=AssertionError("full verify forbidden")),
            patch("ocpf_post.state_registry.verify_critical", return_value={"status": "observed", "invalid_count": 0, "critical_ledger_status": "observed"}),
            patch("ocpf_post.outcome_connectors.report", return_value={}),
            patch("ocpf_post.alert_delivery.report", return_value={}),
        ]
        for item in common:
            item.start(); self.addCleanup(item.stop)
        from datetime import datetime, timezone
        one = observer.build_snapshot(now=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc))
        two = observer.build_snapshot(now=datetime(2026, 9, 17, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(one["revision"], two["revision"])
        self.assertNotEqual(one["observed_at"], two["observed_at"])


class ObserverServerTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "schema_version": 1, "consequence": "READ_ONLY", "local_only": True,
            "revision": "abc", "observed_at": "2026-09-17T12:00:00Z",
        }
        self.build_patch = patch.object(observer_server, "build_snapshot", return_value=self.snapshot)
        self.build_patch.start(); self.addCleanup(self.build_patch.stop)
        self.server = observer_server.ConsoleServer((observer_server.HOST, 0), observer_server.ConsoleHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_server_cache_coalesces_browser_and_sse_snapshot_work(self):
        expanded = {
            **self.snapshot,
            "operator": {"stages": [{"id": "supply"}]},
            "execution": {"schema_version": 2},
        }
        with patch.object(observer_server, "build_execution_snapshot", return_value=expanded) as build:
            first = self.server.snapshot()
            second = self.server.snapshot()

        self.assertIs(first, second)
        self.assertEqual(first["operator"]["stages"][0]["id"], "supply")
        build.assert_called_once_with(base=self.snapshot)

    def test_snapshot_headers_and_body_are_read_only(self):
        with urlopen(self.base + "/api/snapshot", timeout=3) as response:
            value = json.loads(response.read())
            self.assertEqual(value["consequence"], "READ_ONLY")
            self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("connect-src 'self'", response.headers["Content-Security-Policy"])
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    def test_mutation_methods_are_not_exposed(self):
        request = Request(self.base + "/api/snapshot", data=b"{}", method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 405)
        self.assertEqual(caught.exception.headers["Allow"], "GET, HEAD")

    def test_sse_is_one_way_and_head_is_safe(self):
        request = Request(self.base + "/api/stream", method="HEAD")
        with urlopen(request, timeout=3) as response:
            self.assertTrue(response.headers["Content-Type"].startswith("text/event-stream"))
            self.assertEqual(response.read(), b"")
        self.assertEqual(observer_server.HOST, "127.0.0.1")

    def test_ui_contains_no_form_or_mutation_endpoint(self):
        with urlopen(self.base + "/", timeout=3) as response:
            body = response.read().decode("utf-8")
        self.assertIn("<h1>Post-Once</h1>", body)
        self.assertIn("Local publishing operations · evidence-backed and read only", body)
        self.assertNotIn("<form", body.lower())
        self.assertNotIn("method=\"post\"", body.lower())
        self.assertIn("new EventSource('/api/stream')", body)


class ObserverDispatchTests(unittest.TestCase):
    def test_console_dispatch_is_isolated(self):
        with patch.object(dispatch, "_bootstrap_x_runtime"), \
             patch("ocpf_post.observer_cli.main") as main, \
             patch.object(sys, "argv", ["ocpf-post", "console", "--snapshot"]):
            dispatch.main()
            routed_argv = list(sys.argv)
        main.assert_called_once_with()
        self.assertEqual(routed_argv[1:], ["--snapshot"])


    def test_console_snapshot_uses_same_expanded_operator_model_as_browser(self):
        expanded = {
            "schema_version": 1,
            "consequence": "READ_ONLY",
            "local_only": True,
            "operator": {
                "stages": [{"id": "supply"}, {"id": "automatic_schedule"}],
                "diagnosis": {"code": "supply_emergency"},
            },
            "execution": {"schema_version": 2},
        }
        output = io.StringIO()
        with patch.object(observer_cli, "build_execution_snapshot", return_value=expanded) as build, \
             patch.object(sys, "argv", ["ocpf-post console", "--snapshot"]), \
             redirect_stdout(output):
            observer_cli.main()

        build.assert_called_once_with()
        value = json.loads(output.getvalue())
        self.assertEqual(value["operator"]["diagnosis"]["code"], "supply_emergency")
        self.assertEqual(value["execution"]["schema_version"], 2)

    def test_user_service_scripts_parse_and_enforce_read_only_namespace(self):
        root = Path(__file__).resolve().parents[1]
        install = root / "scripts" / "install-user-console"
        uninstall = root / "scripts" / "uninstall-user-console"
        for script in (install, uninstall):
            result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        content = install.read_text(encoding="utf-8")
        self.assertIn("ProtectSystem=strict", content)
        self.assertIn("ProtectHome=read-only", content)
        self.assertIn("NoNewPrivileges=true", content)
        self.assertIn("/bin/sh $ROOT/poststeward console", content)


if __name__ == "__main__":
    unittest.main()
