from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import replenisher as r
from ocpf_post.campaigns import builtin_manifest, builtin_text

UTC = timezone.utc


def linkedin_copy(seed: str) -> str:
    """Build provider-valid multi-paragraph LinkedIn test copy without shared filler vocabulary."""
    first = seed.strip()
    second = first
    while len(first) + 2 + len(second) < 360:
        second += " " + first
    return f"{first}\n\n{second}"


class AllowAllBudget:
    def admit(self, project, provider, account, **_kwargs):
        return {
            "admitted": True,
            "scope": f"{provider}:{account}",
            "project": project,
            "protected": False,
            "reasons": ["within_destination_budget"],
        }


class SelectiveBudget:
    def __init__(self, allowed):
        self.allowed = set(allowed)

    def admit(self, project, provider, account, **_kwargs):
        admitted = provider in self.allowed
        return {
            "admitted": admitted,
            "scope": f"{provider}:{account}",
            "project": project,
            "protected": False,
            "reasons": ["within_destination_budget"] if admitted else ["account_high_water"],
        }


class AccountSelectiveBudget:
    def __init__(self, allowed_accounts):
        self.allowed_accounts = set(allowed_accounts)

    def admit(self, project, provider, account, **_kwargs):
        admitted = str(account) in self.allowed_accounts
        return {
            "admitted": admitted,
            "scope": f"{provider}:{account}",
            "project": project,
            "protected": False,
            "reasons": (
                ["within_deep_inventory_safety_ceiling"]
                if admitted else ["account_unavailable"]
            ),
        }


class EvidenceGroundedGenerativeSupplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "OCPF_POST_STATE_DIR": self.tmp.name,
            "OCPF_POST_CONFIG_DIR": self.tmp.name,
            "OCPF_POST_GENERATIVE_SUPPLY_ENABLED": "1",
            "OCPF_POST_GENERATIVE_SUPPLY_MODE": "api",
            "OCPF_POST_GENERATIVE_DAILY_LIMIT": "3",
            "OCPF_POST_GENERATIVE_PROJECT": "example",
        }, clear=False)
        self.env.start()
        self.now = datetime(2026, 9, 19, 12, 30, tzinfo=UTC)
        self.profile = {
            "project": "example",
            "label": "Example",
            "repository": "AyobamiH/example",
            "campaign_prefix": "EX",
            "providers": ["x", "linkedin"],
            "destinations": {"x": "x-founder", "linkedin": "linkedin-founder"},
            "claim_boundary": "Source-backed framing only.",
            "inventory": [{
                "title": "Evidence boundaries",
                "lane": "commercial",
                "priority": 80,
                "hook": "A source change is not a production outcome.",
                "body": "Keep implementation, deployment and customer evidence separate.",
                "cta": "Which boundary does your status report collapse?",
            }],
        }

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def routes(self):
        def account(project, alias, expected_provider=None):
            return {"account_id": expected_provider + "-account"}
        candidates = [{
            "project": "example", "provider": "x", "account_id": "x-account",
        }]
        return patch("ocpf_post.portfolio.delivery_candidates", return_value=candidates), \
               patch("ocpf_post.registry.resolve_account", side_effect=account), \
               patch("ocpf_post.editorial_continuity.stock_floor", return_value=3)

    def test_work_mode_requests_editorial_refill_without_calling_paid_model(self):
        routes, accounts, floor = self.routes()
        with patch.dict(os.environ, {"OCPF_POST_GENERATIVE_SUPPLY_MODE": "work"}), \
             routes, accounts, floor, \
             patch.object(r, "_model_generate_supply",
                          side_effect=AssertionError("Work mode must not call paid model")):
            rows, status = r._generative_campaigns_for_profile(
                self.profile, source_sha="a" * 40, now=self.now, apply=True,
            )
        self.assertEqual(rows, [])
        self.assertEqual(status, "work_refill_requested")

    def test_invalid_mode_fails_safe_to_work_not_paid_api(self):
        routes, accounts, floor = self.routes()
        with patch.dict(os.environ, {"OCPF_POST_GENERATIVE_SUPPLY_MODE": "unexpected"}), \
             routes, accounts, floor, \
             patch.object(r, "_model_generate_supply",
                          side_effect=AssertionError("Invalid mode must not call paid model")):
            rows, status = r._generative_campaigns_for_profile(
                self.profile, source_sha="a" * 40, now=self.now, apply=True,
            )
        self.assertEqual(rows, [])
        self.assertEqual(status, "work_refill_requested")

    def test_preview_is_bounded_and_does_not_call_model(self):
        routes, accounts, floor = self.routes()
        with routes, accounts, floor, patch.object(r, "_model_generate_supply",
                                                    side_effect=AssertionError("preview called model")):
            rows, status = r._generative_campaigns_for_profile(
                self.profile, source_sha="a" * 40, now=self.now, apply=False,
            )
        self.assertEqual(status, "planned")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["providers"], ["linkedin", "x"])

    def test_preview_continues_after_existing_indices_in_same_bucket(self):
        # Owner-host emergency refill may run repeatedly before the four-hour bucket rolls.
        existing = [
            "EX-GEN-2026091912-01-FFFFFFF",
            "EX-GEN-2026091912-02-FFFFFFF",
        ]
        routes, accounts, floor = self.routes()
        with routes, accounts, floor, \
             patch.object(r, "_generative_project_usage_today", return_value=Counter({"example": 2})), \
             patch.object(r, "_generative_project_daily_cap", return_value=5), \
             patch.object(r, "_generative_portfolio_projects", return_value=["example"]), \
             patch.object(r, "_generative_routes", return_value=(["x", "linkedin"], 3)), \
             patch.object(r, "campaign_ids", return_value=existing), \
             patch.object(r, "_model_generate_supply", side_effect=AssertionError("preview called model")):
            rows, status = r._generative_campaigns_for_profile(
                self.profile, source_sha="f" * 40, now=self.now, apply=False,
            )
        self.assertEqual(status, "planned")
        self.assertEqual(
            [row["campaign"] for row in rows],
            [
                "EX-GEN-2026091912-03-FFFFFFF",
                "EX-GEN-2026091912-04-FFFFFFF",
                "EX-GEN-2026091912-05-FFFFFFF",
            ],
        )

    def test_apply_freezes_evidence_and_learning_attribution(self):
        generated = [{
            "title": "Boundary one",
            "comparison_variant": "baseline",
            "angle_family": "decision_test",
            "evidence_indexes": [0],
            "novelty_rationale": "Starts from the decision check.",
            "texts": {
                "x": "Before calling work complete, separate source evidence from runtime evidence.",
                "threads": "",
                "linkedin": linkedin_copy("Before calling work complete, separate source evidence from runtime evidence. Check deployment and customer outcomes independently."),
            },
        }]
        routes, accounts, floor = self.routes()
        with routes, accounts, floor, \
             patch.object(r, "_generative_angle_family", return_value="decision_test"), \
             patch.object(r, "_model_generate_supply", return_value=generated), \
             patch.object(r, "_generative_existing_texts", return_value={"linkedin": [], "x": []}):
            rows, status = r._generative_campaigns_for_profile(
                self.profile, source_sha="a" * 40, now=self.now, apply=True,
                admission_budget=AllowAllBudget(),
            )
        self.assertEqual(status, "created")
        self.assertEqual(len(rows), 1)
        manifest = builtin_manifest(rows[0]["campaign"])
        self.assertEqual(manifest["assistance"]["mode"], "evidence_grounded_llm")
        self.assertEqual(manifest["source"]["comparison_variant"], "baseline")
        self.assertEqual(manifest["source"]["angle_family"], "decision_test")
        self.assertEqual(manifest["source"]["evidence_indexes"], [0])
        self.assertTrue(manifest["payload_frozen"] if "payload_frozen" in manifest else manifest["runtime_generated"])
        self.assertIn("separate source evidence", builtin_text(rows[0]["campaign"], "x"))

        later, later_status = r._generative_campaigns_for_profile(
            self.profile, source_sha="b" * 40, now=self.now, apply=False,
        )
        self.assertEqual(later, [])
        self.assertIn(later_status, {"not_needed", "project_daily_share_reached"})

    def test_generated_supply_records_scoped_admission_and_filters_blocked_route(self):
        generated = [{
            "title": "Boundary one",
            "comparison_variant": "baseline",
            "angle_family": "decision_test",
            "evidence_indexes": [0],
            "novelty_rationale": "Admission boundary.",
            "texts": {
                "x": "Only admitted provider routes become allocatable.",
                "threads": "",
                "linkedin": linkedin_copy("A blocked destination must not ride inside an admitted generated campaign."),
            },
        }]
        admissions = []
        routes, accounts, floor = self.routes()
        with routes, accounts, floor, \
             patch.object(r, "_generative_angle_family", return_value="decision_test"), \
             patch.object(r, "_model_generate_supply", return_value=generated), \
             patch.object(r, "_generative_existing_texts", return_value={"linkedin": [], "x": []}):
            rows, status = r._generative_campaigns_for_profile(
                self.profile,
                source_sha="9" * 40,
                now=self.now,
                apply=True,
                admission_budget=SelectiveBudget({"x"}),
                admission_results=admissions,
            )
        self.assertEqual(status, "created_partial")
        self.assertEqual(rows[0]["providers"], ["x"])
        self.assertEqual(rows[0]["blocked_providers"], ["linkedin"])
        self.assertEqual(len(admissions), 2)
        manifest = builtin_manifest(rows[0]["campaign"])
        self.assertEqual(manifest["providers"], ["x"])
        self.assertEqual(manifest["source"]["path"], "README.md")
        self.assertTrue(manifest["payload_frozen"])
        self.assertEqual(manifest["admission"]["gate"], "scoped_admission")
        self.assertEqual(manifest["admission"]["providers"]["x"]["account_id"], "x-account")
        self.assertNotIn("linkedin", manifest["admission"]["providers"])
        self.assertIsNone(builtin_text(rows[0]["campaign"], "linkedin"))

    def test_unavailable_linkedin_page_preserves_independent_member_route(self):
        generated = [{
            "title": "Independent LinkedIn routing",
            "comparison_variant": "baseline",
            "angle_family": "decision_test",
            "evidence_indexes": [0],
            "novelty_rationale": "Tests Page/member authority separation.",
            "texts": {
                "x": "",
                "threads": "",
                "linkedin": linkedin_copy(
                    "An unavailable organisation Page must not strand an independently "
                    "authorised member distribution route."
                ),
            },
        }]
        admissions = []
        configured = [
            {
                "provider": "linkedin",
                "alias": "linkedin-founder",
                "account_id": "urn:li:person:member",
                "default": True,
                "organisation": False,
            },
            {
                "provider": "linkedin",
                "alias": "linkedin-page",
                "account_id": "urn:li:organization:999",
                "default": False,
                "organisation": True,
            },
        ]
        with patch.object(r, "_generative_project_usage_today", return_value=Counter()), \
             patch.object(r, "_generative_project_daily_cap", return_value=1), \
             patch.object(r, "_generative_portfolio_projects", return_value=["example"]), \
             patch.object(r, "_generative_routes", return_value=(["linkedin"], 1)), \
             patch.object(r, "_generative_angle_family", return_value="decision_test"), \
             patch.object(r, "_model_generate_supply", return_value=generated), \
             patch.object(r, "_generative_existing_texts", return_value={"linkedin": []}), \
             patch("ocpf_post.source_routes.routes", return_value=configured):
            rows, status = r._generative_campaigns_for_profile(
                self.profile,
                source_sha="7" * 40,
                now=self.now,
                apply=True,
                admission_budget=AccountSelectiveBudget({"urn:li:person:member"}),
                admission_results=admissions,
            )

        self.assertEqual(status, "created")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["providers"], ["linkedin"])
        manifest = builtin_manifest(rows[0]["campaign"])
        self.assertEqual(
            manifest["admission"]["providers"]["linkedin"]["account_id"],
            "urn:li:person:member",
        )
        self.assertEqual(manifest["destinations"]["linkedin"], "linkedin-founder")
        self.assertEqual(len(admissions), 2)
        page = next(
            row for row in admissions
            if row["account_id"] == "urn:li:organization:999"
        )
        self.assertFalse(page["admitted"])
        self.assertTrue(page["member_route_preserved"])

    def test_generated_supply_all_blocked_never_writes_runtime_campaign(self):
        generated = [{
            "title": "Blocked",
            "comparison_variant": "baseline",
            "angle_family": "decision_test",
            "evidence_indexes": [0],
            "novelty_rationale": "Admission denies both destinations.",
            "texts": {
                "x": "Blocked X candidate.",
                "threads": "",
                "linkedin": linkedin_copy("Blocked LinkedIn candidate."),
            },
        }]
        admissions = []
        routes, accounts, floor = self.routes()
        with routes, accounts, floor, \
             patch.object(r, "_generative_angle_family", return_value="decision_test"), \
             patch.object(r, "_model_generate_supply", return_value=generated), \
             patch.object(r, "_generative_existing_texts", return_value={"linkedin": [], "x": []}):
            rows, status = r._generative_campaigns_for_profile(
                self.profile,
                source_sha="8" * 40,
                now=self.now,
                apply=True,
                admission_budget=SelectiveBudget(set()),
                admission_results=admissions,
            )
        self.assertEqual(rows, [])
        self.assertEqual(status, "admission_paused")
        self.assertEqual(len(admissions), 2)
        self.assertEqual(list((Path(self.tmp.name) / "runtime-campaigns").glob("*"))
                         if (Path(self.tmp.name) / "runtime-campaigns").exists() else [], [])

    def test_repairable_novelty_failure_gets_one_bounded_retry(self):
        first = [{
            "title": "Too similar",
            "comparison_variant": "baseline",
            "angle_family": "decision_test",
            "evidence_indexes": [0],
            "novelty_rationale": "First attempt.",
            "texts": {
                "x": "Same exact published wording",
                "threads": "",
                "linkedin": linkedin_copy("Same exact published wording"),
            },
        }]
        second = [{
            "title": "Boundary repair",
            "comparison_variant": "baseline",
            "angle_family": "checklist",
            "evidence_indexes": [0],
            "novelty_rationale": "A different operational angle.",
            "texts": {
                "x": "Separate source evidence from runtime evidence before closing the work.",
                "threads": "",
                "linkedin": linkedin_copy("Separate source evidence from runtime evidence before closing the work. Keep deployment and customer evidence as independent checkpoints."),
            },
        }]
        routes, accounts, floor = self.routes()
        with routes, accounts, floor, \
             patch.object(r, "_generative_angle_family", side_effect=["decision_test", "checklist"]), \
             patch.object(r, "_model_generate_supply", side_effect=[first, second]) as model, \
             patch.object(r, "_generative_existing_texts", return_value={
                 "linkedin": [linkedin_copy("Same exact published wording")],
                 "x": ["Same exact published wording"],
             }):
            rows, status = r._generative_campaigns_for_profile(
                self.profile, source_sha="c" * 40, now=self.now, apply=True,
                admission_budget=AllowAllBudget(),
            )
        self.assertEqual(status, "created")
        self.assertEqual(len(rows), 1)
        self.assertEqual(model.call_count, 2)

        first_angle = model.call_args_list[0].kwargs["angle_family"]
        second_angle = model.call_args_list[1].kwargs["angle_family"]
        self.assertNotEqual(first_angle, second_angle)

    def test_linkedin_editorial_targets_do_not_block_generated_supply(self):
        base = {
            "title": "Editorial boundary",
            "comparison_variant": "baseline",
            "angle_family": "decision_test",
            "evidence_indexes": [0],
            "novelty_rationale": "Editorial guidance is not transport integrity.",
            "texts": {"x": "Distinct X copy.", "threads": "", "linkedin": ""},
        }
        dense = ("A complete intended LinkedIn payload can be one paragraph without being truncated. " * 7).strip()
        too_short_for_editorial_target = "Concise but complete LinkedIn payload."
        above_editorial_target_but_provider_valid = (
            "Longer complete LinkedIn copy remains transport-valid even when it exceeds the preferred editorial range. " * 12
        ).strip()

        with patch.object(r, "_generative_existing_texts", return_value={"x": [], "linkedin": []}):
            for text in (dense, too_short_for_editorial_target, above_editorial_target_but_provider_valid):
                with self.subTest(chars=len(text)):
                    row = {**base, "texts": {**base["texts"], "linkedin": text}}
                    accepted = r._validate_generated_supply([row], self.profile, ["x", "linkedin"])
                    self.assertEqual(accepted[0]["texts"]["linkedin"], text)

    def test_linkedin_provider_envelope_remains_hard_integrity_boundary(self):
        row = {
            "title": "Too large",
            "comparison_variant": "baseline",
            "angle_family": "decision_test",
            "evidence_indexes": [0],
            "novelty_rationale": "Provider envelope remains authoritative.",
            "texts": {
                "x": "Distinct X copy.",
                "threads": "",
                "linkedin": "x" * 3001,
            },
        }
        with patch.object(r, "_generative_existing_texts", return_value={"x": [], "linkedin": []}):
            with self.assertRaisesRegex(ValueError, "3000-character safety envelope"):
                r._validate_generated_supply([row], self.profile, ["x", "linkedin"])

    def test_one_bad_candidate_does_not_discard_other_project_supply(self):
        valid = {
            "title": "Useful survivor",
            "comparison_variant": "baseline",
            "angle_family": "decision_test",
            "evidence_indexes": [0],
            "novelty_rationale": "Different enough.",
            "texts": {
                "x": "Keep the source/runtime distinction explicit when reporting progress.",
                "threads": "",
                "linkedin": linkedin_copy("Keep the source/runtime distinction explicit when reporting progress. That avoids treating implementation evidence as deployment evidence."),
            },
        }
        routes, accounts, floor = self.routes()
        with routes, accounts, floor, \
             patch.object(r, "_generative_project_usage_today", return_value=Counter()), \
             patch.object(r, "_generative_project_daily_cap", return_value=2), \
             patch.object(r, "_generative_portfolio_projects", return_value=["example"]), \
             patch.object(r, "_generative_routes", return_value=(["x", "linkedin"], 2)), \
             patch.object(r, "_generative_angle_family", return_value="decision_test"), \
             patch.object(r, "_model_generate_supply", return_value=[{"placeholder": True}]) as model, \
             patch.object(
                 r,
                 "_validate_generated_supply",
                 side_effect=[
                     [valid],
                     ValueError("generative_text_not_novel"),
                     ValueError("generative_text_not_novel"),
                     ValueError("generative_text_not_novel"),
                 ],
             ):
            rows, status = r._generative_campaigns_for_profile(
                self.profile, source_sha="d" * 40, now=self.now, apply=True,
                admission_budget=AllowAllBudget(),
            )
        self.assertEqual(status, "created_partial")
        self.assertEqual(len(rows), 1)
        self.assertEqual(model.call_count, 4)

    def test_model_schema_bounds_evidence_indexes_to_actual_evidence(self):
        import json

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit):
                return json.dumps({
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({
                                "candidates": [{
                                    "title": "Bounded",
                                    "comparison_variant": "baseline",
                                    "angle_family": "checklist",
                                    "evidence_indexes": [1],
                                    "novelty_rationale": "Uses the second actual evidence row.",
                                    "texts": {
                                        "x": "One",
                                        "threads": "Two",
                                        "linkedin": "Three",
                                    },
                                }]
                            })
                        },
                    }]
                }).encode()

        captured = {}

        class Opener:
            def open(self, request, timeout):
                captured["request"] = request
                captured["timeout"] = timeout
                return Response()

        profile = {
            **self.profile,
            "inventory": [
                self.profile["inventory"][0],
                "ignored",
                {
                    "title": "Second",
                    "lane": "evergreen",
                    "hook": "Second hook",
                    "body": "Second body",
                    "cta": "Second CTA",
                },
            ],
        }

        with patch("ocpf_post.reply_model.key", return_value="test-key"), \
             patch("urllib.request.build_opener", return_value=Opener()), \
             patch.object(r, "_generative_existing_texts", return_value={"x": []}):
            rows = r._model_generate_supply(
                profile,
                providers=["x"],
                count=1,
                source_sha="e" * 40,
                bucket="2026092100",
                angle_family="checklist",
            )

        self.assertEqual(rows[0]["evidence_indexes"], [1])
        request = json.loads(captured["request"].data)
        user_payload = json.loads(request["messages"][1]["content"])
        self.assertEqual([row["index"] for row in user_payload["evidence"]], [0, 1])

        schema = request["response_format"]["json_schema"]["schema"]
        evidence_schema = (
            schema["properties"]["candidates"]["items"]["properties"]
            ["evidence_indexes"]["items"]
        )
        self.assertEqual(evidence_schema["minimum"], 0)
        self.assertEqual(evidence_schema["maximum"], 1)

        angle_schema = (
            schema["properties"]["candidates"]["items"]["properties"]
            ["angle_family"]
        )
        self.assertEqual(angle_schema["enum"], ["checklist"])
        self.assertEqual(user_payload["required_angle_family"], "checklist")
        self.assertTrue(user_payload["angle_instruction"])

    def test_angle_family_rotation_avoids_recent_and_current_angles(self):
        with patch.object(
            r,
            "_generative_recent_angle_families",
            return_value=["failure_mode", "checklist"],
        ):
            first = r._generative_angle_family(
                self.profile,
                now=self.now,
                candidate_index=1,
                attempt=0,
                excluded=set(),
            )
            second = r._generative_angle_family(
                self.profile,
                now=self.now,
                candidate_index=1,
                attempt=1,
                excluded={first},
            )
        self.assertNotIn(first, {"failure_mode", "checklist"})
        self.assertNotIn(second, {"failure_mode", "checklist"})
        self.assertNotEqual(first, second)

    def test_evidence_grounded_generation_fails_closed_when_readme_revision_changes(self):
        from ocpf_post import source_guard, source_observations
        from ocpf_post.state import write_private_json

        write_private_json(source_observations.path(), {
            "schema_version": 1,
            "projects": {
                "example": {
                    "repository": "AyobamiH/example",
                    "source_ok": True,
                    "readme_sha": "b" * 40,
                    "pending": [],
                }
            },
        })
        manifest = {
            "project": "example",
            "runtime_generated": True,
            "payload_frozen": True,
            "source": {
                "type": "evidence_grounded_generation",
                "repository": "AyobamiH/example",
                "source_sha": "a" * 40,
            },
            "allocation": {
                "enabled": True,
                "expires_at": "2026-09-26T00:00:00Z",
            },
        }
        self.assertEqual(
            source_guard.guard(manifest, now=self.now),
            "Generated campaign README revision was superseded",
        )

    def test_quarantined_unpublished_generated_copy_does_not_block_replacement_novelty(self):
        manifests = {
            "EX-GEN-OLD": {
                "campaign": "EX-GEN-OLD",
                "project": "example",
                "source": {"type": "evidence_grounded_generation"},
            },
            "EX-GEN-NEW": {
                "campaign": "EX-GEN-NEW",
                "project": "example",
                "source": {"type": "evidence_grounded_generation"},
            },
        }
        texts = {
            ("EX-GEN-OLD", "x"): "Quarantined wording that should not block a replacement.",
            ("EX-GEN-NEW", "x"): "Current attested wording remains part of novelty protection.",
        }
        def guard(manifest, provider, account_id, text):
            return "Generated route lacks scoped admission attestation" if manifest["campaign"] == "EX-GEN-OLD" else None

        with patch.object(r, "campaign_ids", return_value=["EX-GEN-OLD", "EX-GEN-NEW"]), \
             patch("ocpf_post.campaigns.builtin_manifest", side_effect=lambda cid: manifests[cid]), \
             patch("ocpf_post.campaigns.builtin_text", side_effect=lambda cid, provider: texts.get((cid, provider))), \
             patch("ocpf_post.campaigns.destination_binding", return_value={"account_id": "x-account"}), \
             patch("ocpf_post.generated_supply_guard.delivery_guard", side_effect=guard), \
             patch("ocpf_post.scheduler.schedule_records", return_value=[]), \
             patch("ocpf_post.state.iter_receipts", return_value=[]):
            existing = r._generative_existing_texts(self.profile, ["x"])

        self.assertEqual(existing["x"], ["Current attested wording remains part of novelty protection."])

    def test_terminal_generated_effect_stays_in_novelty_even_if_campaign_is_now_quarantined(self):
        manifest = {
            "campaign": "EX-GEN-PUBLISHED",
            "project": "example",
            "source": {"type": "evidence_grounded_generation"},
        }
        text = "Already published generated wording must remain duplicate pressure."
        with patch.object(r, "campaign_ids", return_value=["EX-GEN-PUBLISHED"]), \
             patch("ocpf_post.campaigns.builtin_manifest", return_value=manifest), \
             patch("ocpf_post.campaigns.builtin_text", return_value=text), \
             patch("ocpf_post.generated_supply_guard.delivery_guard", side_effect=AssertionError("terminal route must bypass current delivery guard")), \
             patch("ocpf_post.scheduler.schedule_records", return_value=[]), \
             patch("ocpf_post.state.iter_receipts", return_value=[{
                 "campaign": "EX-GEN-PUBLISHED",
                 "provider": "x",
                 "status": "published_unverified",
             }]):
            existing = r._generative_existing_texts(self.profile, ["x"])

        self.assertEqual(existing["x"], [text])

    def test_daily_api_allowance_still_counts_quarantined_generation_artifacts(self):
        campaigns = [
            "EX-GEN-20260919-01-AAAAAAA",
            "EX-GEN-20260919-02-BBBBBBB",
        ]
        with patch.object(r, "campaign_ids", return_value=campaigns), \
             patch.dict(os.environ, {"OCPF_POST_GENERATIVE_DAILY_LIMIT": "3"}):
            self.assertEqual(r._generative_daily_remaining(self.now), 1)

    def test_invalid_daily_limit_fails_to_bounded_default(self):
        with patch.dict(os.environ, {"OCPF_POST_GENERATIVE_DAILY_LIMIT": "unbounded"}):
            self.assertEqual(r._generative_daily_limit(), 3)
        with patch.dict(os.environ, {"OCPF_POST_GENERATIVE_DAILY_LIMIT": "999"}):
            self.assertEqual(r._generative_daily_limit(), 60)

    def test_similarity_guard_rejects_template_paraphrase(self):
        row = {
            "title": "Duplicate",
            "comparison_variant": "baseline",
            "angle_family": "decision_test",
            "evidence_indexes": [0],
            "novelty_rationale": "Not actually novel.",
            "texts": {"x": "Same exact published wording", "threads": "",
                      "linkedin": linkedin_copy("Same exact published wording")},
        }
        with patch.object(r, "_generative_existing_texts",
                          return_value={"x": ["Same exact published wording"],
                                        "linkedin": [linkedin_copy("Same exact published wording")]}):
            with self.assertRaisesRegex(ValueError, "not_novel"):
                r._validate_generated_supply([row], self.profile, ["x", "linkedin"])


if __name__ == "__main__":
    unittest.main()
