from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ocpf_post import editorial_continuity as c

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc)


def workpack(*, runnable=1, reserved=0, intent=True):
    return {
        "schema_version": 1,
        "status": "observed",
        "coverage_observed_at": NOW.isoformat(),
        "coverage_sha256": "a" * 64,
        "project_count": 1,
        "route_count": 1,
        "physical_account_count": 1,
        "supply_reviews": [],
        "route_supply": [{
            "project": "example",
            "provider": "x",
            "account_id": "123",
            "aliases": ["x-brand"],
            "publishing_intent": intent,
            "runnable": runnable,
            "reserved": reserved,
            "source": {"status": "observed", "head_sha": "b" * 40},
            "vaults": [{"id": "example", "status": "current"}],
            "exclusion_counts": {},
        }],
        "accounts": [],
    }


class EditorialContinuityTests(unittest.TestCase):
    def setUp(self):
        # This suite exercises both legacy and steady policy fixtures. Never
        # inherit a policy saved by another test or the operator's live host.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        environment = patch.dict(os.environ, {
            'OCPF_POST_CONFIG_DIR': str(root / 'config'),
            'OCPF_POST_STATE_DIR': str(root / 'state'),
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_sufficient_supply_does_not_hide_unfilled_real_planner_opportunity(self):
        route = {**workpack(runnable=6)['route_supply'][0], 'unreserved_schedule_opportunities': 1}
        result = c.account_acceptance([route], now=NOW, policy_fn=lambda *_: 5)
        self.assertEqual(result['accounts'][0]['blocker_stage'], 'scheduling')
        self.assertEqual(result['accounts'][0]['completion'], 'B')
        # No immediate opportunity under the real planner is policy-compliant
        # waiting, not a demand to publish outside a window or beyond a ceiling.
        route['unreserved_schedule_opportunities'] = 0
        self.assertEqual(c.account_acceptance([route], now=NOW, policy_fn=lambda *_: 5)['status'], 'adequate')

    def test_unusable_approval_requires_new_copy_not_repeated_import(self):
        route = {**workpack(runnable=0)['route_supply'][0], 'vaults': [{
            'status': 'current', 'active_entries': 1, 'awaiting_import_entries': 0,
            'entry_state_counts': {'consumed_or_ambiguous': 1}}],
            'exclusion_counts': {'vault entry already consumed or ambiguous on this destination': 1}}
        result = c.account_acceptance([route], now=NOW, policy_fn=lambda *_: 5)
        self.assertEqual(result['accounts'][0]['blocker_stage'], 'generation')
        self.assertEqual(result['accounts'][0]['completion'], 'B')
        recovery = c.recovery_work(result, [{**route, 'request_id': 'r1'}])
        self.assertEqual(recovery[0]['next_action'], 'author_distinct_reviewed_copy_for_exact_account')
        self.assertEqual(recovery[0]['cadence_deficit'], 5)
        self.assertFalse(recovery[0]['permission_to_replay_or_activate'])
        route['vaults'][0]['awaiting_import_entries'] = 1
        self.assertEqual(c.account_acceptance([route], now=NOW)['accounts'][0]['blocker_stage'], 'admission')

    def test_recovery_first_serves_empty_physical_accounts_and_ignores_disabled(self):
        route = workpack(runnable=3)['route_supply'][0]
        routes = [route, {**route, 'account_id': 'empty', 'runnable': 0},
                  {**route, 'account_id': 'disabled', 'publishing_intent': False, 'runnable': 0}]
        result = c.account_acceptance(routes, now=NOW, policy_fn=lambda *_: 5)
        recovery = c.recovery_work(result, [{**r, 'request_id': r['account_id']} for r in routes])
        self.assertEqual([r['account_id'] for r in recovery], ['empty', '123'])
        self.assertEqual([r['cadence_deficit'] for r in recovery], [5, 2])

    def test_historical_manual_only_copy_does_not_block_new_authoring(self):
        route = {**workpack(runnable=0)['route_supply'][0],
                 'exclusion_counts': {'not opted into portfolio allocation': 40}}
        result = c.account_acceptance([route], now=NOW, policy_fn=lambda *_: 5)
        self.assertEqual(result['accounts'][0]['blocker_stage'], 'generation')

    def test_low_stock_and_expiry_are_proactive_not_publication_authority(self):
        key = ("example", "x", "123")
        expiries = {key: [NOW + timedelta(hours=12)]}
        result = c.demand(workpack(runnable=3), now=NOW, expiries=expiries, activity={},
                          policy_fn=lambda provider, account: 5)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(len(result["requests"]), 1)
        request = result["requests"][0]
        self.assertEqual(request["stock_floor"], 5)
        self.assertEqual(request["expiring_within_48h"], 1)
        self.assertEqual(request["surviving_after_48h"], 2)
        self.assertEqual(request["suggested_new_items"], 3)
        self.assertIn("below_editorial_cadence_floor", request["demand_reasons"])
        self.assertIn("expiry_horizon_would_break_floor", request["demand_reasons"])
        self.assertFalse(request["permission_to_replay_or_activate"])
        self.assertIn("not a posting quota", request["stock_floor_basis"])

    def test_reserved_work_does_not_hide_zero_unreserved_future_supply(self):
        result = c.demand(workpack(runnable=0, reserved=4), now=NOW, expiries={}, activity={},
                          policy_fn=lambda provider, account: 4)
        request = result["requests"][0]
        self.assertEqual(request["reserved"], 4)
        self.assertIn("no_unreserved_inventory", request["demand_reasons"])
        self.assertEqual(request["suggested_new_items"], 4)

    def test_no_publishing_intent_creates_no_request(self):
        result = c.demand(workpack(runnable=0, intent=False), now=NOW, expiries={}, activity={},
                          policy_fn=lambda provider, account: 4)
        self.assertEqual(result["requests"], [])

    def test_request_identity_survives_unchanged_cycles_then_resolves(self):
        state = {"schema_version": 1, "routes": {}}
        writes = []

        class Lock:
            def __enter__(self): return None
            def __exit__(self, *args): return False

        def read(_path):
            return deepcopy(state)

        def write(_path, value):
            state.clear(); state.update(deepcopy(value)); writes.append(deepcopy(value))

        with patch.object(c.local_store, "read", side_effect=read), \
             patch.object(c.local_store, "write", side_effect=write), \
             patch.object(c.local_store, "locked", return_value=Lock()):
            first = c.reconcile(workpack(runnable=0), now=NOW, apply=True, expiries={}, activity={},
                                policy_fn=lambda provider, account: 3)
            second = c.reconcile(workpack(runnable=0), now=NOW + timedelta(minutes=20), apply=True,
                                 expiries={}, activity={}, policy_fn=lambda provider, account: 3)
            recovered = c.reconcile(workpack(runnable=4), now=NOW + timedelta(minutes=40), apply=True,
                                    expiries={}, activity={}, policy_fn=lambda provider, account: 3)

        self.assertEqual(first["open_requests"][0]["request_id"], second["open_requests"][0]["request_id"])
        self.assertEqual(first["open_requests"][0]["generation"], 1)
        self.assertEqual(second["open_requests"][0]["generation"], 1)
        self.assertEqual(recovered["open_request_count"], 0)
        self.assertEqual(recovered["resolved_this_cycle"][0]["resolution"], "resolved_by_observed_supply")
        self.assertGreaterEqual(len(writes), 3)

    def test_market_cold_route_with_no_stock_becomes_editorial_demand(self):
        key = ("example", "x", "123")
        activity = {key: {
            "last_effect_at": (NOW - timedelta(hours=72)).isoformat(),
            "last_verified_publication_at": (NOW - timedelta(hours=72)).isoformat(),
            "next_scheduled_at": None,
            "scheduled_count": 0,
        }}
        result = c.demand(
            workpack(runnable=0), now=NOW, expiries={}, activity=activity,
            policy_fn=lambda provider, account: 2,
        )
        request = result["requests"][0]
        self.assertTrue(request["market_cold"])
        self.assertIn("market_presence_cold", request["demand_reasons"])
        self.assertEqual(request["hours_since_last_effect"], 72)
        conditions = {row["type"]: row for row in request["conditions"]}
        self.assertEqual(conditions["MarketContinuity"]["status"], "False")
        self.assertEqual(conditions["MarketContinuity"]["reason"], "MarketCold")
        self.assertFalse(request["permission_to_replay_or_activate"])

    def test_market_cold_signal_does_not_create_extra_copy_when_stock_is_healthy(self):
        key = ("example", "x", "123")
        activity = {key: {
            "last_effect_at": (NOW - timedelta(hours=72)).isoformat(),
            "last_verified_publication_at": (NOW - timedelta(hours=72)).isoformat(),
            "next_scheduled_at": None,
            "scheduled_count": 0,
        }}
        result = c.demand(
            workpack(runnable=4), now=NOW, expiries={}, activity=activity,
            policy_fn=lambda provider, account: 3,
        )
        self.assertEqual(result["requests"], [])
        route = result["routes"][0]
        self.assertTrue(route["market_cold"])
        self.assertGreaterEqual(route["editorial_runway_hours"], 4)
        self.assertNotIn("market_presence_cold", route["demand_reasons"])

    def test_observed_consumption_builds_reserve_before_immediate_floor_breaks(self):
        key = ("example", "x", "123")
        expiries = {key: [NOW + timedelta(days=10)] * 6}
        result = c.demand(
            workpack(runnable=6),
            now=NOW,
            expiries=expiries,
            activity={},
            rates={key: 1.0},
            policy_fn=lambda provider, account: 2,
        )
        request = result["requests"][0]
        self.assertIn("reserve_runway_below_target", request["demand_reasons"])
        self.assertEqual(request["reserve_status"], "watch")
        self.assertFalse(request["reserve_fallback_required"])
        self.assertEqual(request["reserve_target_items"], 7)
        self.assertEqual(request["reserve_fallback_trigger_items"], 5)
        self.assertEqual(request["reserve_runway_days"], 6.0)
        self.assertEqual(request["suggested_new_items"], 1)

    def test_portfolio_reserve_scans_shared_evidence_once_for_many_projects(self):
        profiles = {
            "project-a": {"providers": ["x"], "destinations": {"x": "x-a"}},
            "project-b": {"providers": ["x"], "destinations": {"x": "x-b"}},
        }
        candidates = [
            {"project": "project-a", "provider": "x", "account_id": "1",
             "expires_at": (NOW + timedelta(days=10)).isoformat()},
            {"project": "project-b", "provider": "x", "account_id": "2",
             "expires_at": (NOW + timedelta(days=10)).isoformat()},
        ]
        rates = {
            ("project-a", "x", "1"): 1.0,
            ("project-b", "x", "2"): 1.0,
        }

        def resolve(project, alias, *, expected_provider):
            self.assertEqual(expected_provider, "x")
            return {"account_id": "1" if project == "project-a" else "2"}

        with patch("ocpf_post.portfolio.delivery_candidates", return_value=candidates) as deliveries, \
             patch.object(c, "route_publication_rates", return_value=rates) as rate_scan, \
             patch("ocpf_post.scheduler.schedule_records", return_value=[]) as schedules, \
             patch("ocpf_post.registry.resolve_account", side_effect=resolve), \
             patch.object(c, "stock_floor", return_value=2):
            result = c.portfolio_reserve(profiles, now=NOW)

        self.assertEqual(len(result), 2)
        self.assertEqual({row["project"] for row in result}, {"project-a", "project-b"})
        deliveries.assert_called_once_with(now=NOW)
        rate_scan.assert_called_once_with(now=NOW)
        schedules.assert_called_once_with()

    def test_steady_reserve_uses_saved_account_pace_not_burst_history(self):
        steady = {
            "schema_version": 1,
            "timezone": "Europe/London",
            "providers": {
                "x": {
                    "daily_target": 20,
                    "flow_mode": "fixed",
                    "hard_daily_ceiling": 100,
                    "window_start": "07:00",
                    "window_end": "23:00",
                },
            },
            "release_pacing": {
                "schema_version": 1,
                "mode": "steady_originals",
                "unit": "logical_publications_per_account_per_local_day",
            },
        }
        keys = [("a", "x", "1"), ("b", "x", "1")]
        observed = {keys[0]: 9.0, keys[1]: 7.0}
        with patch("ocpf_post.portfolio.load_policy", return_value=steady), \
             patch.object(c, "_configured_account_route_counts", return_value=Counter({("x", "1"): 2})), \
             patch.object(c, "route_policy", return_value=steady["providers"]["x"]):
            plan = c.reserve_rate_plan(keys, observed)

        self.assertEqual(plan[keys[0]]["observed_daily_rate"], 9.0)
        self.assertEqual(plan[keys[1]]["observed_daily_rate"], 7.0)
        self.assertEqual(plan[keys[0]]["reserve_daily_rate"], 10.0)
        self.assertEqual(plan[keys[1]]["reserve_daily_rate"], 10.0)
        self.assertEqual(sum(row["reserve_daily_rate"] for row in plan.values()), 20.0)
        self.assertEqual(plan[keys[0]]["reserve_rate_basis"], "steady_saved_daily_share")

    def test_steady_route_floor_and_threshold_ignore_burst_history(self):
        rate = {
            "reserve_rate_basis": "steady_saved_daily_share",
            "reserve_daily_rate": 1.0,
        }
        with patch.object(c, "route_policy", return_value={
            "daily_target": 20, "window_start": "07:00", "window_end": "23:00",
        }):
            floor = c.reserve_stock_floor("x", "1", rate, 5)
        self.assertEqual(floor, 1)

        state = c.reserve_state(
            runnable=4,
            reserved=0,
            expiries=[NOW + timedelta(days=10)] * 4,
            daily_rate=9.0,
            reserve_daily_rate=1.0,
            reserve_rate_basis="steady_saved_daily_share",
            steady_account_daily_limit=20,
            steady_account_route_count=20,
            immediate_floor=floor,
            now=NOW,
        )
        self.assertEqual(state["observed_daily_rate"], 9.0)
        self.assertEqual(state["reserve_daily_rate"], 1.0)
        self.assertEqual(state["target_items"], 7)
        self.assertEqual(state["fallback_trigger_items"], 5)
        self.assertEqual(state["target_deficit"], 3)
        self.assertEqual(state["reserve_runway_days"], 4.0)

    def test_zero_steady_release_rate_does_not_create_fake_reserve_pressure(self):
        state = c.reserve_state(
            runnable=0,
            reserved=0,
            expiries=[],
            daily_rate=3.0,
            reserve_daily_rate=0.0,
            reserve_rate_basis="steady_saved_daily_share",
            steady_account_daily_limit=0,
            steady_account_route_count=1,
            immediate_floor=0,
            now=NOW,
        )
        self.assertEqual(state["status"], "inactive")
        self.assertEqual(state["target_items"], 0)
        self.assertFalse(state["fallback_required"])
        self.assertFalse(state["editorial_refill_required"])

    def test_reserve_state_exposes_named_status_contract(self):
        result = c.reserve_state(
            runnable=0,
            reserved=0,
            expiries=[],
            daily_rate=1.0,
            immediate_floor=2,
            now=NOW,
        )
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["reserve_status"], "empty")

    def test_api_fallback_threshold_arrives_before_inventory_is_empty(self):
        key = ("example", "x", "123")
        expiries = {key: [NOW + timedelta(days=10)] * 4}
        result = c.demand(
            workpack(runnable=4),
            now=NOW,
            expiries=expiries,
            activity={},
            rates={key: 1.0},
            policy_fn=lambda provider, account: 2,
        )
        request = result["requests"][0]
        self.assertEqual(request["reserve_status"], "fallback")
        self.assertTrue(request["reserve_fallback_required"])
        self.assertEqual(request["reserve_available_items"], 4)
        self.assertEqual(request["suggested_new_items"], 3)
        conditions = {row["type"]: row for row in request["conditions"]}
        self.assertEqual(conditions["SupplyReserve"]["status"], "False")

    def test_near_term_schedule_keeps_market_continuity_warm(self):
        key = ("example", "x", "123")
        activity = {key: {
            "last_effect_at": (NOW - timedelta(hours=72)).isoformat(),
            "last_verified_publication_at": (NOW - timedelta(hours=72)).isoformat(),
            "next_scheduled_at": (NOW + timedelta(hours=2)).isoformat(),
            "scheduled_count": 1,
        }}
        result = c.demand(
            workpack(runnable=0, reserved=1), now=NOW, expiries={}, activity=activity,
            policy_fn=lambda provider, account: 1,
        )
        route = result["routes"][0]
        self.assertFalse(route["market_cold"])
        conditions = {row["type"]: row for row in route["conditions"]}
        self.assertEqual(conditions["MarketContinuity"]["status"], "True")

    def test_stock_floor_uses_existing_capacity_without_raising_it(self):
        # X 20/day over a 16-hour posting window => five items for one 4h
        # editorial cadence. This is only a demand signal.
        floor = c.stock_floor("x", "123", policy={
            "daily_target": 20, "window_start": "07:00", "window_end": "23:00",
        })
        self.assertEqual(floor, 5)

    def test_assessment_is_exact_copy_bound_and_cannot_preclaim_response(self):
        text = "A useful exact reviewed message"
        packet = {
            "schema_version": 1,
            "reviewed_at": NOW.isoformat(),
            "batches": [{
                "project": "example", "account_id": "123",
                "entries": [{"campaign": "EX-1", "provider": "x", "approval_sha256": "c" * 64}],
                "editorial_reviews": [{
                    "campaign": "EX-1", "payload_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "audience_response": "not_observed", "decision": "approved_by_agent_editor",
                    "audience": "builders", "recognisable_situation": "a specific situation",
                    "useful_action": "do one thing", "product_connection": "truthful connection",
                    "novelty_rationale": "new lesson", "evidence_boundary": "bounded claim",
                }],
            }],
        }
        reviewed = {"status": "review_records_valid", "entries": [{
            "project": "example", "campaign": "EX-1", "provider": "x", "account_id": "123",
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }]}
        result = c.record_assessments(packet, reviewed, now=NOW)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["audience_response_at_review"], "not_observed")
        changed = deepcopy(packet)
        changed["batches"][0]["editorial_reviews"][0]["audience_response"] = "positive"
        with self.assertRaises(ValueError):
            c.record_assessments(changed, reviewed, now=NOW)

    def test_audience_projection_joins_exact_effect_without_raw_inbound_or_outcome_reference(self):
        publication = {"at": NOW - timedelta(hours=24), "effective_verified": True,
                       "receipt": {"campaign": "EX-1", "provider": "x", "account_id": "123",
                                   "post_id": "999", "text_sha256": "a" * 64,
                                   "status": "published_verified", "readback_verified": True}}
        snapshots = [{"campaign": "EX-1", "provider": "x", "account_id": "123", "post_id": "999",
                      "captured_at": NOW.isoformat(), "target_age_hours": 24,
                      "metrics": {"impressions": 140, "likes": 2},
                      "availability": {"status": "available", "detail": "PRIVATE"}}]
        engagement = {"schema_version": 1, "inbox": {"r1": {
            "campaign": "EX-1", "provider": "x", "account_id": "123",
            "conversation_root_id": "999", "status": "pending", "conversation_depth": 1,
            "context": {"text": "PRIVATE INBOUND"},
        }}, "polls": {}}
        outcomes = {"events": [{
            "campaign": "EX-1", "provider": "x", "account_id": "123", "post_id": "999",
            "event_type": "enquiry", "evidence_reference": "PRIVATE REF",
            "revenue_minor": None, "currency": None,
        }]}
        assessment_state = {"schema_version": 1, "records": {"a": {
            "campaign": "EX-1", "provider": "x", "account_id": "123",
            "payload_sha256": "a" * 64, "assessment_sha256": "b" * 64,
        }}}
        with patch("ocpf_post.performance_review.publications", return_value={
                ("EX-1", "x", "123", "999"): publication}), \
             patch("ocpf_post.performance.iter_snapshots", return_value=iter(snapshots)), \
             patch("ocpf_post.engagement.read", return_value=engagement), \
             patch("ocpf_post.business_outcomes.report", return_value=outcomes), \
             patch("ocpf_post.source_receipts.publication_inputs", return_value=({"EX-1": {"project": "example"}}, {}, {})), \
             patch.object(c.local_store, "read", return_value=assessment_state):
            result = c.audience_evidence(now=NOW)
        self.assertEqual(result["records"][0]["inbound"]["observed_count"], 1)
        self.assertEqual(result["records"][0]["business_outcomes"]["event_counts"], {"enquiry": 1})
        self.assertTrue(result["records"][0]["editorial_assessment_recorded"])
        encoded = json.dumps(result)
        self.assertNotIn("PRIVATE INBOUND", encoded)
        self.assertNotIn("PRIVATE REF", encoded)
        self.assertNotIn("PRIVATE", encoded)
        self.assertIn("not sentiment", result["boundary"].lower())


class AccountAcceptanceTests(unittest.TestCase):
    @staticmethod
    def route(provider, account, *, runnable=0, reserved=0, intent=True, available=True, project='example'):
        return {
            'project': project, 'provider': provider, 'account_id': account,
            'publishing_intent': intent, 'destination_available': available,
            'destination_unavailable_reason': None if available else 'Additional account is inactive or has no private credentials',
            'runnable': runnable, 'reserved': reserved, 'scheduled_count': reserved,
            'source': {'status': 'observed'}, 'vaults': [], 'exclusion_counts': {},
            'last_effect_at': None, 'last_verified_publication_at': None, 'next_scheduled_at': None,
        }

    def test_one_healthy_account_does_not_hide_an_empty_enabled_account(self):
        result = c.account_acceptance([
            self.route('x', 'healthy', runnable=3),
            self.route('threads', 'empty'),
        ], now=NOW, policy_fn=lambda *_: 2)
        rows = {(row['provider'], row['account_id']): row for row in result['accounts']}
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(rows[('x', 'healthy')]['completion'], 'A')
        self.assertEqual(rows[('threads', 'empty')]['completion'], 'B')
        self.assertEqual(rows[('threads', 'empty')]['blocker_stage'], 'generation')

    def test_linkedin_supply_does_not_satisfy_threads(self):
        result = c.account_acceptance([
            self.route('linkedin', 'brand', runnable=8),
            self.route('threads', 'brand'),
        ], now=NOW, policy_fn=lambda *_: 2)
        rows = {row['provider']: row for row in result['accounts']}
        self.assertEqual(rows['linkedin']['status'], 'adequate')
        self.assertEqual(rows['threads']['status'], 'blocked')

    def test_immediate_floor_is_operational_while_deep_reserve_recovers(self):
        route = self.route('x', 'thin', runnable=25, reserved=1)
        route.update(reserve_available_items=1, reserve_target_items=180)
        result = c.account_acceptance([route], now=NOW, policy_fn=lambda *_: 6)
        row = result['accounts'][0]
        self.assertEqual(result['status'], 'recovering')
        self.assertEqual(row['status'], 'recovering')
        self.assertEqual(row['completion'], 'R')
        self.assertEqual(row['cadence_available_items'], 26)
        self.assertEqual(row['operational_buffer_target_items'], 12)
        self.assertEqual(row['operational_buffer_deficit'], 0)
        self.assertEqual(row['available_items'], 1)
        self.assertEqual(row['required_items'], 180)
        self.assertEqual(row['blocker_stage'], 'generation')
        self.assertEqual(row['blocker_reason'], 'cadence_ready_reserve_rebuilding')
        recovery = c.recovery_work(result, [{**route, 'request_id': 'r-thin'}])
        self.assertEqual(recovery[0]['reserve_deficit'], 179)
        self.assertEqual(recovery[0]['cadence_deficit'], 0)
        self.assertEqual(recovery[0]['operational_buffer_target_items'], 12)
        self.assertEqual(recovery[0]['operational_buffer_deficit'], 0)

    def test_one_item_operational_gap_remains_blocked(self):
        route = self.route('x', 'brand', runnable=3, reserved=1)
        route.update(reserve_available_items=4, reserve_target_items=140)
        result = c.account_acceptance([route], now=NOW, policy_fn=lambda *_: 5)
        row = result['accounts'][0]
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(row['cadence_available_items'], 4)
        self.assertEqual(row['operational_buffer_target_items'], 10)
        self.assertEqual(row['operational_buffer_deficit'], 6)
        recovery = c.recovery_work(result, [{**route, 'request_id': 'r-brand'}])
        self.assertEqual(recovery[0]['cadence_deficit'], 1)
        self.assertEqual(recovery[0]['operational_buffer_deficit'], 6)

    def test_recent_publication_is_evidence_not_current_supply(self):
        route = self.route('x', 'recent')
        route['last_effect_at'] = (NOW - timedelta(minutes=5)).isoformat()
        route['last_verified_publication_at'] = route['last_effect_at']
        result = c.account_acceptance([route], now=NOW, policy_fn=lambda *_: 2)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['accounts'][0]['available_items'], 0)

    def test_disabled_destination_stays_disabled(self):
        result = c.account_acceptance([
            self.route('threads', 'disabled', runnable=12, intent=False),
        ], now=NOW, policy_fn=lambda *_: 2)
        self.assertEqual(result['status'], 'no_enabled_destinations')
        self.assertEqual(result['accounts'][0]['status'], 'disabled')
        self.assertEqual(result['accounts'][0]['stock_floor'], 0)
        self.assertEqual(result['accounts'][0]['required_items'], 0)

    def test_unavailable_destination_names_credentials_stage(self):
        result = c.account_acceptance([
            self.route('threads', 'offline', runnable=4, available=False),
        ], now=NOW, policy_fn=lambda *_: 2)
        row = result['accounts'][0]
        self.assertEqual(row['status'], 'blocked')
        self.assertEqual(row['blocker_stage'], 'credentials')



class HandoffContinuityProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("editorial_handoff_continuity", ROOT / "scripts/editorial-handoff.py")
        cls.h = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.h)

    def test_handoff_exports_durable_request_and_safe_audience_summary(self):
        h = self.h
        pack = workpack(runnable=0)
        pack["accounts"] = [{"provider": "x", "account_id": "123", "projects": ["example"],
                             "runnable": 0, "reserved": 0, "verified_24h": 1,
                             "unverified_retained": 0, "publishing_intent": True,
                             "empty_inventory": True, "no_verified_post_24h": False}]
        request = {"project": "example", "provider": "x", "account_id": "123", "aliases": ["x-brand"],
                   "request_id": "edr-abc-1", "route_key": "abc", "generation": 1,
                   "first_requested_at": NOW.isoformat(), "last_observed_at": NOW.isoformat(),
                   "demand_reasons": ["below_editorial_cadence_floor"], "runnable": 1, "reserved": 0,
                   "stock_floor": 5, "expiring_within_48h": 1, "surviving_after_48h": 0,
                   "suggested_new_items": 5, "request": "reconcile_existing_batch_then_author_or_resolve_eligibility",
                   "stock_floor_basis": "not a posting quota", "permission_to_replay_or_activate": False,
                   "source": {"status": "observed", "raw": "PRIVATE"}, "vaults": [], "exclusion_counts": {}}
        pack["continuity"] = {"status": "observed", "observed_at": NOW.isoformat(),
                              "open_request_count": 1, "open_requests": [request],
                              "resolved_this_cycle": [], "boundary": "local bookkeeping"}
        pack["publication_evidence"] = {"status": "observed", "lookback_days": 7, "total_matched": 1,
                                        "omitted_count": 0, "unresolved_count": 0,
                                        "boundary": "receipt projection", "records": []}
        pack["audience_evidence"] = {"schema_version": 1, "status": "observed", "observed_at": NOW.isoformat(),
                                     "omitted_count": 0, "records": [{
            "project": "example", "campaign": "EX-1", "provider": "x", "account_id": "123",
            "post_id": "999", "text_sha256": "a" * 64, "published_at": NOW.isoformat(),
            "status": "response_evidence_observed", "editorial_assessment_recorded": True,
            "assessment_sha256": "b" * 64,
            "performance": [{"captured_at": NOW.isoformat(), "target_age_hours": 24,
                             "availability": "available", "metrics": {"impressions": 140},
                             "private": "PRIVATE"}],
            "inbound": {"observed_count": 1, "status_counts": {"pending": 1},
                        "max_conversation_depth": 1, "text": "PRIVATE"},
            "business_outcomes": {"event_counts": {"enquiry": 1},
                                  "revenue_minor_by_currency": {}, "evidence": "PRIVATE"},
        }]}
        snapshot = h.make_snapshot(pack, "b" * 40, NOW)
        self.assertEqual(snapshot["supply_reviews"][0]["request_id"], "edr-abc-1")
        self.assertEqual(snapshot["request_reconciliation"]["open_request_count"], 1)
        self.assertEqual(snapshot["audience_evidence"]["records"][0]["performance"][0]["metrics"]["impressions"], 140)
        encoded = h.canonical(snapshot)
        self.assertNotIn("PRIVATE", encoded)
        self.assertFalse(snapshot["supply_reviews"][0]["permission_to_replay_or_activate"])


if __name__ == "__main__":
    unittest.main()
