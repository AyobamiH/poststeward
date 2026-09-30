from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from ocpf_post import execution_observer


class ExecutionProjectionTests(unittest.TestCase):
    def test_admission_projection_uses_scoped_gate_and_keeps_backlog_diagnostic_separate(self):
        persisted = {
            "schema_version": 1,
            "mode": "paused",
            "observed_at": "2026-09-12T14:51:29Z",
            "reasons": ["hysteresis_recovery_not_reached"],
        }
        metrics = {
            "eligible_unreserved": 6,
            "aged_count": 0,
            "expiry_risk_count": 0,
            "by_provider": {"x": 6, "threads": 0, "linkedin": 0},
            "queue_observation": "observed",
        }
        evaluated = {
            "schema_version": 1,
            "admitted": True,
            "mode": "open",
            "reasons": ["within_admission_budget"],
            "metrics": metrics,
            "projected_metrics": metrics,
            "observed_at": "2026-09-21T17:26:44Z",
        }
        policy = {
            "global_high_water": 300,
            "global_recovery_water": 180,
            "provider_high_water": {"x": 160, "threads": 160, "linkedin": 60},
            "provider_recovery_water": {"x": 100, "threads": 100, "linkedin": 36},
        }
        registry = {
            "projects": {
                "p1": {
                    "accounts": {
                        "x-owner": {"provider": "x", "account_id": "1"},
                        "li-owner": {"provider": "linkedin", "account_id": "2"},
                    }
                }
            }
        }

        class FakeBudget:
            init_count = 0

            def __init__(self, _now, _apply):
                type(self).init_count += 1

            def admit(self, project, provider, account_id):
                admitted = provider == "x"
                return {
                    "admitted": admitted,
                    "scope": f"{provider}:{account_id}",
                    "project": project,
                    "protected": False,
                    "reasons": ["within_destination_budget"] if admitted else ["account_high_water"],
                }

        with patch("ocpf_post.local_store.read", return_value=persisted), \
             patch("ocpf_post.admission.decide", return_value=evaluated) as decide, \
             patch("ocpf_post.admission.load_policy", return_value=policy), \
             patch("ocpf_post.admission.state_file", return_value=Path("/tmp/never-written")), \
             patch("ocpf_post.registry.load_registry", return_value=registry), \
             patch("ocpf_post.scoped_admission.Budget", FakeBudget):
            value = execution_observer._admission()

        decide.assert_called_once_with(persist=False)
        self.assertEqual(value["active_gate"], "scoped_admission")
        self.assertEqual(value["mode"], "partial")
        self.assertEqual(value["metrics"]["route_count"], 2)
        self.assertEqual(FakeBudget.init_count, 1)
        self.assertEqual(value["metrics"]["open_route_count"], 1)
        self.assertEqual(value["metrics"]["paused_route_count"], 1)
        self.assertEqual(value["metrics"]["eligible_unreserved"], 6)
        self.assertEqual(value["backlog_pressure"]["mode"], "open")
        self.assertEqual(value["backlog_pressure"]["metrics"]["eligible_unreserved"], 6)
        self.assertTrue(value["backlog_pressure"]["diagnostic_only"])
        self.assertEqual(value["backlog_pressure"]["persisted_state"]["mode"], "paused")
        self.assertFalse(value["backlog_pressure"]["persisted_state"]["matches_current_evaluation"])
        self.assertFalse(value["network_checked"])
        self.assertIn("active scoped source-admission gate", value["boundary"])


    def test_acceptance_projection_reads_persisted_state_without_recompute(self):
        persisted = {
            "schema_version": 1,
            "status": "open",
            "observed_at": "2026-09-17T22:00:00Z",
            "sections": {
                "pc01_editorial_supply": {"status": "open", "open_requests": []},
                "pc02_source_vault": {"status": "observed", "sources": [], "vaults": []},
                "pc03_publication_readback": {"status": "open", "results": []},
                "pc04_learning": {"status": "open", "targets": []},
                "pc05_effect_reconciliation": {"status": "open"},
                "pc06_inbound_coverage": {"status": "partial", "scopes": []},
            },
        }
        with patch("ocpf_post.local_store.read", return_value=persisted), \
             patch("ocpf_post.acceptance_views.build", side_effect=AssertionError("recompute forbidden")):
            value = execution_observer._acceptance()
        self.assertEqual(value["status"], "open")
        self.assertEqual(value["sections"]["pc04_learning"]["status"], "open")
        self.assertFalse(value["network_checked"])
        self.assertFalse(value["recomputed"])

    def test_execution_model_separates_state_intent_and_evidence(self):
        base = {
            "portfolio": {
                "status": {"eligible_deliveries": 9},
                "plan": {
                    "plan": [{"campaign": "C-1", "project": "p", "provider": "x", "lane": "evergreen", "run_at": "2026-09-17T13:00:00Z"}],
                    "decisions": [{"provider": "x", "run_at": "2026-09-17T13:00:00Z", "campaign": "C-1", "reason": "bounded_aged_service"}],
                },
            },
            "replenishment": {
                "source_observations": {
                    "p": {"status": "observed", "observed_at": "2026-09-17T11:00:00Z", "pending_count": 3}
                }
            },
            "activity": {"active_schedules": [], "recent_receipts": []},
            "engagement": {"counts": {}},
            "performance": {"recent": []},
            "feedback": {},
            "machine": {"flows": []},
        }
        admission = {"mode": "paused", "status": "observed", "observed_at": "2026-09-17T11:01:00Z", "metrics": {"eligible_unreserved": 9}, "reasons": ["global_high_water"]}
        collection = {"status": "observed", "completed_at": "2026-09-17T11:02:00Z", "stages": [{"stage": "source-observation", "status": "completed"}]}
        value = execution_observer._execution_projection(base, admission, collection)
        node_ids = {row["id"] for row in value["nodes"]}
        self.assertTrue({"observer", "buffer", "admission", "inventory", "service", "run_due", "measure_24", "measure_72", "measure_168"}.issubset(node_ids))
        node_map = {row["id"]: row for row in value["nodes"]}
        self.assertEqual(node_map["admission"]["label"], "Source admission")
        self.assertIn("scoped routes", node_map["admission"]["metric"])
        self.assertEqual(node_map["inventory"]["label"], "Eligible scheduling pool")
        self.assertEqual(node_map["x"]["metric"], "verified in recent receipt sample")
        edge_modes = {(row["source"], row["target"]): row["mode"] for row in value["edges"]}
        self.assertEqual(edge_modes[("inventory", "service")], "intent")
        self.assertEqual(edge_modes[("buffer", "admission")], "state")
        kinds = {row["kind"] for row in value["events"]}
        self.assertIn("source_observed", kinds)
        self.assertIn("evidence_buffered", kinds)
        self.assertIn("admission_state", kinds)
        self.assertFalse(any(row["kind"] == "schedule_persisted" for row in value["events"]))
        self.assertEqual(value["intent"]["planned_count"], 1)
        self.assertIn("never publication claims", value["boundary"])

    def test_operator_model_explains_automatic_scheduling_and_supply_pressure(self):
        base = {
            "replenishment": {
                "supply_reserve": {
                    "routes": [
                        {
                            "project": "project-a",
                            "provider": "x",
                            "reserve_status": "emergency",
                            "reserve_available_items": 1,
                            "reserve_runway_days": 1.2,
                        },
                        {
                            "project": "project-a",
                            "provider": "threads",
                            "reserve_status": "fallback",
                            "reserve_available_items": 3,
                            "reserve_runway_days": 3.2,
                        },
                    ],
                },
            },
            "activity": {"active_schedules": [], "recent_receipts": []},
        }
        admission = {"mode": "open", "metrics": {"eligible_unreserved": 0}}
        execution = {
            "nodes": [
                {"id": "inventory", "value": 0},
                {"id": "scheduler", "value": 0},
                {"id": "run_due", "value": 0},
                {"id": "readback", "value": 0},
            ],
            "intent": {"planned_count": 0},
        }

        value = execution_observer._operator_model(base, admission, execution)

        self.assertEqual(
            [row["id"] for row in value["stages"]],
            ["supply", "admission", "automatic_schedule", "execute", "provider", "verify"],
        )
        self.assertEqual(value["diagnosis"]["code"], "supply_emergency")
        self.assertEqual(value["reserve"]["status_counts"]["emergency"], 1)
        self.assertEqual(value["reserve"]["status_counts"]["fallback"], 1)
        self.assertEqual(value["reserve"]["available_items"], 4)
        schedule = next(row for row in value["stages"] if row["id"] == "automatic_schedule")
        self.assertEqual(schedule["status"], "waiting_for_inventory")
        self.assertIn("durable", schedule["detail"])
        self.assertIn("100/day", value["diagnosis"]["detail"])
        self.assertIn("does not schedule", value["boundary"])

    def test_empty_protected_reserve_does_not_hide_current_schedulable_work(self):
        base = {
            "replenishment": {
                "supply_reserve": {
                    "routes": [{
                        "project": "project-a",
                        "provider": "x",
                        "reserve_status": "empty",
                        "reserve_available_items": 0,
                        "reserve_runway_days": 0.0,
                        "reserve_rate_basis": "steady_saved_daily_share",
                    }],
                },
            },
            "activity": {"active_schedules": [], "recent_receipts": []},
        }
        admission = {"mode": "open", "metrics": {"eligible_unreserved": 2}}
        execution = {
            "nodes": [
                {"id": "inventory", "value": 2},
                {"id": "scheduler", "value": 0},
                {"id": "run_due", "value": 0},
                {"id": "readback", "value": 0},
            ],
            "intent": {"planned_count": 1},
        }

        value = execution_observer._operator_model(base, admission, execution)

        self.assertEqual(value["diagnosis"]["code"], "protected_reserve_empty")
        self.assertEqual(value["diagnosis"]["tone"], "warn")
        self.assertIn("current eligible", value["diagnosis"]["detail"])
        self.assertIn("not permission to publish more today", value["diagnosis"]["detail"])
        self.assertEqual(value["reserve"]["eligible_now"], 2)
        self.assertEqual(value["reserve"]["planned_now"], 1)
        self.assertEqual(
            value["reserve"]["rate_basis_counts"],
            {"steady_saved_daily_share": 1},
        )

    def test_empty_protected_reserve_and_no_current_work_are_distinct(self):
        base = {
            "replenishment": {
                "supply_reserve": {
                    "routes": [{
                        "project": "project-a",
                        "provider": "x",
                        "reserve_status": "empty",
                        "reserve_available_items": 0,
                        "reserve_runway_days": 0.0,
                    }],
                },
            },
            "activity": {"active_schedules": [], "recent_receipts": []},
        }
        admission = {"mode": "open", "metrics": {"eligible_unreserved": 0}}
        execution = {
            "nodes": [
                {"id": "inventory", "value": 0},
                {"id": "scheduler", "value": 0},
                {"id": "run_due", "value": 0},
                {"id": "readback", "value": 0},
            ],
            "intent": {"planned_count": 0},
        }

        value = execution_observer._operator_model(base, admission, execution)

        self.assertEqual(value["diagnosis"]["code"], "protected_reserve_and_inventory_empty")
        self.assertIn("no eligible, planned or scheduled work", value["diagnosis"]["detail"])

    def test_operator_model_surfaces_recent_provider_failure_classes(self):
        base = {
            "replenishment": {"supply_reserve": {"routes": []}},
            "activity": {
                "recent_receipts": [],
                "recent_schedules": [
                    {
                        "schedule_id": "sch-rate",
                        "provider": "x",
                        "status": "failed",
                        "failure_class": "provider_rate_limited",
                    },
                    {
                        "schedule_id": "sch-auth",
                        "provider": "x",
                        "status": "failed",
                        "failure_class": "provider_auth_rejected",
                    },
                ],
            },
        }
        admission = {"mode": "open", "metrics": {"eligible_unreserved": 2}}
        execution = {
            "nodes": [
                {"id": "inventory", "value": 2},
                {"id": "scheduler", "value": 0},
                {"id": "run_due", "value": 0},
                {"id": "readback", "value": 0},
            ],
            "intent": {"planned_count": 0},
        }
        value = execution_observer._operator_model(base, admission, execution)
        self.assertEqual(value["provider_failures"]["count"], 2)
        self.assertEqual(value["provider_failures"]["by_class"]["provider_rate_limited"], 1)
        self.assertEqual(value["provider_failures"]["by_provider"]["x"], 2)
        provider = next(row for row in value["stages"] if row["id"] == "provider")
        self.assertEqual(provider["status"], "attention")
        self.assertIn("2 failed schedules", provider["detail"])
        self.assertFalse(value["provider_failures"]["automatic_retry_authority"])

    def test_measurement_events_preserve_distinct_age_windows(self):
        base = {
            "portfolio": {"status": {}, "plan": {}},
            "replenishment": {"source_observations": {}},
            "activity": {"active_schedules": [], "recent_receipts": []},
            "engagement": {"counts": {}},
            "performance": {"recent": [
                {"campaign": "C-24", "provider": "x", "post_id": "1", "captured_at": "2026-09-17T12:00:00Z", "target_age_hours": 24, "availability": {"status": "available"}},
                {"campaign": "C-72", "provider": "x", "post_id": "2", "captured_at": "2026-09-17T12:01:00Z", "target_age_hours": 72, "availability": {"status": "available"}},
                {"campaign": "C-168", "provider": "threads", "post_id": "3", "captured_at": "2026-09-17T12:02:00Z", "target_age_hours": 168, "availability": {"status": "available"}},
            ]},
            "feedback": {},
            "machine": {"flows": []},
        }
        value = execution_observer._execution_projection(base, {"metrics": {}}, {"stages": []})
        routes = {(row["target"], row.get("target_age_hours")) for row in value["events"] if row["kind"] == "metrics_stored"}
        self.assertEqual(routes, {("measure_24", 24), ("measure_72", 72), ("measure_168", 168)})


class ExecutionUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "src" / "ocpf_post" / "ui" / "execution.html").read_text(encoding="utf-8")

    def test_owner_operations_home_prioritises_direct_questions(self):
        for element_id in (
            "ownerHome", "ownerTodayList", "ownerScheduleList", "ownerPacing",
            "ownerSupply", "ownerConversations", "ownerAttention", "ownerSystem",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        for phrase in (
            "Owner operations", "Scheduled next 24 hours", "Pacing", "Supply",
            "Conversations", "Needs attention", "System", "Pipeline forensic view",
        ):
            self.assertIn(phrase, self.html)
        self.assertIn("function renderOwnerHome()", self.html)
        self.assertIn("activity.today", self.html)
        self.assertNotIn("Living machine", self.html)
        self.assertNotIn("filter:url(#edgeGlow)", self.html)
        self.assertNotIn("filter:url(#tokenGlow)", self.html)

    def test_ui_has_deeper_machine_and_deterministic_narrative_clock(self):
        # Runtime projection supplies labels; keep topology and evidence semantics.
        for node_id in ("buffer", "admission", "service", "run_due", "measure_24", "measure_72", "measure_168"):
            self.assertIn(f"{node_id}:[", self.html)
        for phrase in (
            "Backpressure and hysteresis", "Dry fair-service selection", "The consequential boundary",
            "24-hour performance cohort", "distinct 72-hour performance cohort", "distinct 168-hour performance cohort",
        ):
            self.assertIn(phrase, self.html)
        self.assertIn("function buildReplayTimeline()", self.html)
        self.assertIn('id="timeline" type="range"', self.html)
        self.assertIn("Deterministic replay", self.html)
        self.assertIn("Focus journey", self.html)

    def test_acceptance_watch_surfaces_exact_read_models_without_controls(self):
        self.assertIn("Acceptance watch", self.html)
        for pc in ("pc01Acceptance", "pc02Acceptance", "pc03Acceptance", "pc04Acceptance", "pc05Acceptance", "pc06Acceptance"):
            self.assertIn(f'id="{pc}"', self.html)
        self.assertIn("function renderAcceptance()", self.html)
        self.assertIn("exact evidence projections · no controls", self.html)
        self.assertIn("qualifying", self.html)
        self.assertIn("roots pending", self.html)
        self.assertNotIn("/api/acceptance/", self.html.lower())

    def test_motion_is_semantic_and_browser_only(self):
        for phrase in ("e.kind==='ambiguous_effect'", "e.kind==='execution_blocked'", "e.kind==='metrics_stored'",
                       "new EventSource('/api/stream')", "Replay changes only the picture"):
            self.assertIn(phrase, self.html)
        for forbidden in ("<form", 'method="post"', "/api/publish", "/api/retry"):
            self.assertNotIn(forbidden, self.html.lower())

    def test_operations_surface_consolidates_without_runtime_authority(self):
        for phrase in ('<h1>Post-Once</h1>', 'id="systemDrawer"', 'id="systemButton"', 'id="futureStrip"',
                       'id="contextPanel"', 'id="detailsToggle"', "Stage &amp; evidence",
                       "Root conditions and supporting evidence"):
            self.assertIn(phrase, self.html)
        self.assertIn("function attentionRoots(snapshot)", self.html)
        self.assertIn("function renderCommandBar()", self.html)
        self.assertIn("COMMITTED ·", self.html)
        self.assertIn("PROJECTED ·", self.html)
        for forbidden in ("<form", "/api/publish", "/api/retry"):
            self.assertNotIn(forbidden, self.html.lower())

    def test_operator_flow_explains_automatic_schedule_and_current_volume_limiter(self):
        for phrase in ("1 · Supply", "2 · Admission", "3 · Automatic schedule", "4 · Execute",
                       "5 · Provider", "6 · Verify", "Why volume is low", "Supply reserve"):
            self.assertIn(phrase, self.html)
        for element_id in ("opStageSupply", "opStageAdmission", "opStageAutomaticSchedule", "opStageExecute",
                           "opStageProvider", "opStageVerify", "volumeDiagnosis"):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("function renderOperatorModel()", self.html)
        self.assertIn("model.snapshot?.operator", self.html)
        self.assertIn("renderOperatorModel();renderStateRail()", self.html)
        self.assertNotIn("/api/refill", self.html.lower())
        self.assertNotIn("/api/schedule", self.html.lower())

    def test_header_explains_product_and_local_read_only_context(self):
        self.assertIn("Post-Once · autonomous social publishing operations", self.html)
        self.assertIn('<h1>Post-Once</h1>', self.html)
        self.assertIn("Local publishing operations · evidence-backed and read only", self.html)
        self.assertIn('<span class="badge good">read only</span>', self.html)
        self.assertIn('<span class="badge">127.0.0.1</span>', self.html)

    def test_forensic_canvas_explains_reading_and_overview_in_product_language(self):
        self.assertIn('<h2 id="heading">Pipeline forensic view</h2>', self.html)
        for phrase in ("Overview", "Read labels", "Go to stage", "Focus canvas", "live evidence"):
            self.assertIn(phrase, self.html)
        self.assertIn("Drag or scroll to pan", self.html)
        self.assertIn("0 to fit", self.html)

    def test_live_truth_badge_and_reconciliation_labels_do_not_overclaim(self):
        self.assertIn("streamOpen:false", self.html)
        self.assertIn("function renderLiveModeBadge()", self.html)
        self.assertIn("age<=20000", self.html)
        self.assertIn("stream reconnecting", self.html)
        self.assertIn("Composite local read model; component evidence timestamps may differ.", self.html)
        self.assertIn("automatic reconciliation work remaining", self.html)
        self.assertIn("provider permission-gated effects", self.html)
        self.assertIn("manual-review campaign effects", self.html)
        self.assertIn("retained historical failed schedules", self.html)

    def test_canvas_keeps_history_available_without_permanent_height_cost(self):
        # Pixel dimensions and clipping are tested in tests/browser, not inferred from CSS strings.
        self.assertIn('id="operationsCockpit" class="cockpit"', self.html)
        self.assertIn('id="historyPanel" class="timeline" hidden', self.html)
        self.assertIn('aria-controls="historyPanel" aria-expanded="false"', self.html)
        self.assertIn("function setHistory(open)", self.html)
        self.assertLess(self.html.index('id="map"'), self.html.index('id="historyPanel"'))
        self.assertLess(self.html.index('id="historyPanel"'), self.html.index('<div class="evidence-below">'))
        self.assertIn(".map-scroll #map{display:block;width:100%;height:100%;min-width:0", self.html)

    def test_graph_controls_live_with_graph_not_product_copy(self):
        self.assertIn('class="stage-tools"', self.html)
        stage_top = self.html[self.html.index('class="stage-top"'):self.html.index('<div class="map-scroll">')]
        for control in ("zoomOut", "zoomIn", "fitView", "readableView", "stageJump"):
            self.assertIn(f'id="{control}"', stage_top)
        hero_head = self.html[self.html.index('<div class="hero-head">'):self.html.index('<div class="layout">')]
        self.assertIn('id="fullScreen"', hero_head)

    def test_topology_is_stable_while_snapshot_state_is_patched(self):
        self.assertIn("function topologySignature()", self.html)
        self.assertIn("function patchGraph()", self.html)
        self.assertIn("if(model.topologyKey!==signature)renderGraph();else patchGraph();", self.html)
        self.assertIn("model.topologyKey=topologySignature()", self.html)

    def test_live_context_follows_real_evidence_and_motion_is_bounded(self):
        self.assertIn("function renderCurrentEvent(e,concurrent=1)", self.html)
        self.assertIn("function eventPriority(e)", self.html)
        self.assertIn(".slice(0,4);", self.html)
        self.assertIn("model.lastLiveEvent=e", self.html)
        self.assertIn("renderCurrentEvent(e,Math.max(1,model.livePulses.length))", self.html)

    def test_node_state_and_evidence_throughput_are_distinct(self):
        self.assertIn("class:'value'", self.html)
        self.assertIn("val.textContent=n.value??0", self.html)
        self.assertIn("class:'activity-count'", self.html)
        self.assertIn("persisted evidence movements touching this stage", self.html)
        self.assertIn("${beats} stage beats", self.html)
        self.assertIn("function evidenceCounts(events)", self.html)
        self.assertIn("function renderNodeEvidenceCounts(", self.html)

    def test_replay_evidence_counts_stack_with_narrative_clock(self):
        self.assertIn("function replayEvidenceCounts()", self.html)
        self.assertIn("if(model.playhead>=beat.start)add(e.source)", self.html)
        self.assertIn("model.playhead>=beat.end", self.html)
        self.assertIn("renderNodeEvidenceCounts(replayEvidenceCounts())", self.html)

    def test_live_evidence_bursts_are_accounted_without_rewriting_state(self):
        self.assertIn("const bursts=wasInitialised?evidenceCounts(newEvents):new Map()", self.html)
        self.assertIn("renderNodeEvidenceCounts(evidenceCounts(model.events),bursts)", self.html)
        self.assertIn("activityBurstTimer=setTimeout", self.html)
        self.assertIn("↻ ${count}${burst?", self.html)
        self.assertIn("value.textContent=n.value??0", self.html)

    def test_initial_snapshot_is_not_misrepresented_as_new_live_motion(self):
        self.assertIn("wasInitialised=model.initialised", self.html)
        self.assertIn("if(model.mode==='live'&&wasInitialised)", self.html)
        self.assertIn("model.initialised=true", self.html)

    def test_map_supports_semantic_zoom_without_affecting_runtime(self):
        for control in ("zoomOut", "zoomIn", "fitView", "fullScreen", "zoomSelection"):
            self.assertIn(f'id="{control}"', self.html)
        for fn in ("function applyZoom()", "function fitView()", "function zoomSelection()", "function toggleFullscreen()"):
            self.assertIn(fn, self.html)
        self.assertIn("Replay changes only the picture", self.html)

    def test_feedback_canvas_uses_ordered_lanes_and_direction_aware_routes(self):
        for phrase in ("VERIFY · MEASURE · LEARN", "CONVERSATION LOOP", "learning:[470,440]",
                       "measure_24:[780,315]", "measure_72:[780,440]", "measure_168:[780,565]",
                       "readback:[1090,440]", "const right=dx>=0", "const down=dy>=0",
                       "a==='learning'&&b==='service'", "(a==='x'||a==='threads')&&b==='engagement'",
                       "a==='reply_worker'&&(b==='x'||b==='threads')"):
            self.assertIn(phrase, self.html)

    def test_replay_context_and_hidden_health_drawer_remain_operator_safe(self):
        self.assertIn('role="dialog" aria-modal="true" aria-labelledby="systemHeading" inert', self.html)
        self.assertIn("d.removeAttribute('inert')", self.html)
        self.assertIn("d.setAttribute('inert','')", self.html)
        self.assertIn("if(model.mode==='live'){", self.html)
        self.assertIn("renderNow();}else renderReplayFrame();", self.html)

    def test_full_evidence_layer_remains_visible_below_the_machine(self):
        for element_id in ("stateRail", "intentPanel", "acceptancePanel", "platformPanel"):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("Planning & collection evidence", self.html)
        self.assertIn("Acceptance watch · PC-01 → PC-06", self.html)
        self.assertIn("Platform readiness", self.html)
        self.assertLess(self.html.index('id="acceptancePanel"'), self.html.index('id="systemDrawerWrap"'))
        self.assertLess(self.html.index('id="platformPanel"'), self.html.index('id="systemDrawerWrap"'))

    def test_system_health_drawer_links_to_persistent_evidence_surfaces(self):
        for control in ("jumpIntent", "jumpAcceptance", "jumpPlatform"):
            self.assertIn(f'id="{control}"', self.html)
        self.assertIn("scrollIntoView({behavior:'smooth',block:'start'})", self.html)
        self.assertIn("full acceptance, intent and readiness surfaces remain continuously visible below the machine", self.html)

    def test_machine_canvas_has_explicit_overview_and_reading_scales(self):
        self.assertIn("html,body{overflow:auto}", self.html)
        self.assertIn("function viewportSize()", self.html)
        self.assertIn("function overviewScale()", self.html)
        self.assertIn("function fitView(){camera.mode='overview';model.zoom=overviewScale();", self.html)
        self.assertIn("function readableView(){setZoom(Math.max(1.25,overviewScale()));}", self.html)
        self.assertIn("new ResizeObserver", self.html)
        self.assertIn("function constrainCentre(", self.html)

    def test_nodes_have_readable_card_geometry_and_wrapped_labels(self):
        self.assertIn("const SIZE={w:200,h:96}", self.html)
        self.assertIn('viewBox="0 0 1740 840"', self.html)
        self.assertIn("function nodeLabelLines(label)", self.html)
        self.assertIn("function setNodeName(el,label)", self.html)
        self.assertIn("if(name)setNodeName(name,n.label||n.id)", self.html)
        self.assertIn(".node .name{font-size:17px}", self.html)
        self.assertIn(".node .metric{font-size:12px}", self.html)

    def test_nodes_have_expanded_horizontal_spacing(self):
        for position in (
            "sources:[130,120]", "observer:[370,120]", "buffer:[610,120]", "admission:[860,120]",
            "inventory:[1110,120]", "service:[1360,120]", "scheduler:[1610,120]",
            "engagement:[995,700]", "reply_worker:[1265,700]",
        ):
            self.assertIn(position, self.html)

    def test_classic_console_remains_available_as_fallback(self):
        self.assertIn('href="/classic"', self.html)


if __name__ == "__main__":
    unittest.main()
