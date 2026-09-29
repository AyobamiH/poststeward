from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.campaign_explain import explain_campaign
from ocpf_post.campaigns import builtin_text, runtime_campaign_root
from ocpf_post.onboarding import import_project
from ocpf_post.portfolio import apply_refill, daily_capacity, delivery_candidates, DEFAULT_POLICY
from ocpf_post.portfolio_cross_platform import plan_refill
from ocpf_post.replenisher import _static_campaigns_for_profile
from ocpf_post.scheduler import create_schedule, run_due, cancel_schedule
from ocpf_post.state import append_receipt
from test_onboarding import NOW, project_input
from test_runtime_sources import policy
from test_scheduler import FakeProvider


class CopyExplanationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            'OCPF_POST_CONFIG_DIR': str(self.root / 'config'),
            'OCPF_POST_STATE_DIR': str(self.root / 'state'),
            'POST_ONCE_CONFIG_DIR': str(self.root / 'config'),
            'POST_ONCE_STATE_DIR': str(self.root / 'state'),
        })
        self.env.start(); self.addCleanup(self.env.stop)
        path = self.root / 'project.json'; path.write_text(json.dumps(project_input()))
        preview = import_project(path)
        import_project(path, apply=True, expected_sha256=preview['input_sha256'])
        self.profile = policy()
        self.old = self.generate('a')
        self.cid = self.old[0]['campaign']
        self.text = builtin_text(self.cid, 'x')
        self.digest = hashlib.sha256(self.text.encode()).hexdigest()

    def generate(self, sha):
        return _static_campaigns_for_profile(self.profile, source_sha=sha * 40, now=NOW, apply=True)

    def candidates(self):
        return [r for r in delivery_candidates(now=NOW) if r['project'] == 'runtime-test']

    def receipt(self, **kwargs):
        row = {'campaign': self.cid, 'provider': 'x', 'account_id': '12345', 'text_sha256': self.digest,
               'status': 'published_verified', 'recorded_at': NOW.isoformat()}
        append_receipt({**row, **kwargs})

    def test_identical_variants_and_readme_regeneration_share_one_slot(self):
        self.generate('b')
        rows = self.candidates()
        texts = [builtin_text(r['campaign'], 'x') for r in rows]
        self.assertEqual(len(rows), 2)  # no CTA => insight == question, practical distinct
        self.assertEqual(len(set(texts)), len(texts))
        explanation = explain_campaign('RTEST-AUTO-01I-BBBBBBB', 'x', now=NOW)
        self.assertIn('represented', explanation['allocation']['exclusions'][0]['reason'])

    def test_all_terminal_outcomes_block_new_id_exact_copy(self):
        self.generate('b')
        for status in ('published_verified', 'published_unverified', 'ambiguous_effect'):
            with self.subTest(status=status), patch('ocpf_post.portfolio.iter_receipts', return_value=[{
                'campaign': self.cid, 'provider': 'x', 'account_id': '12345', 'text_sha256': self.digest, 'status': status}]):
                self.assertFalse(any(builtin_text(r['campaign'], 'x') == self.text for r in self.candidates()))

    def test_account_provider_and_missing_hash_do_not_block_unrelated_copy(self):
        for edits in ({'account_id': 'other'}, {'provider': 'threads'}, {'text_sha256': None}, {'status': 'failed'}):
            with self.subTest(edits=edits), patch('ocpf_post.portfolio.iter_receipts', return_value=[{
                'campaign': 'DIFFERENT-ID', 'provider': 'x', 'account_id': '12345', 'text_sha256': self.digest,
                'status': 'published_verified', **edits}]):
                self.assertTrue(any(builtin_text(r['campaign'], 'x') == self.text for r in self.candidates()))

    def test_cancelled_reservation_releases_copy_without_changing_campaigns(self):
        provider = FakeProvider(account_id='12345')
        record = create_schedule(campaign=self.cid, provider='x', at=(NOW + timedelta(minutes=10)).isoformat(), now=NOW, provider_factory=lambda _: provider)
        self.generate('b')
        self.assertFalse(any(builtin_text(r['campaign'], 'x') == self.text for r in self.candidates()))
        cancel_schedule(record['schedule_id'], now=NOW)
        self.assertTrue(any(builtin_text(r['campaign'], 'x') == self.text for r in self.candidates()))
        self.assertEqual(builtin_text(self.cid, 'x'), self.text)

    def test_existing_schedule_executes_then_new_readme_copy_stays_excluded(self):
        provider = FakeProvider(account_id='12345')
        create_schedule(campaign=self.cid, provider='x', at=(NOW + timedelta(minutes=10)).isoformat(), now=NOW, provider_factory=lambda _: provider)
        result = run_due(now=NOW + timedelta(minutes=11), provider_factory=lambda _: provider)
        self.assertEqual(result[0]['status'], 'published_verified')
        self.generate('b')
        self.assertFalse(any(builtin_text(r['campaign'], 'x') == self.text for r in self.candidates()))
        explanation = explain_campaign(self.cid, 'x', now=NOW)
        self.assertEqual(explanation['receipts'][-1]['status'], 'published_verified')
        self.assertEqual(explanation['project_evidence']['runtime_behaviour'], 'not_assessed')
        self.assertEqual(len(provider.published), 1)

    def test_identical_copy_from_changed_source_does_not_hide_distinct_new_copy(self):
        self.receipt()
        self.profile['inventory'][0]['body'] = 'A distinct approved lesson.'
        self.generate('b')
        self.assertTrue(any('distinct approved lesson' in builtin_text(r['campaign'], 'x') for r in self.candidates()))

    def test_daily_budget_calendar_reset_and_uncertain_effect_count(self):
        effective = deepcopy(DEFAULT_POLICY)
        effective['providers']['x']['daily_target'] = 1
        self.receipt(status='ambiguous_effect')
        today = daily_capacity('x', now=NOW, policy=effective)
        tomorrow = daily_capacity('x', now=NOW + timedelta(days=1), policy=effective)
        self.assertEqual(today['daily_budget_used'], 1)
        self.assertTrue(today['daily_target_met'])
        self.assertFalse(today['daily_ceiling_met'])
        self.assertEqual(today['legacy_target_remaining'], 0)
        self.assertEqual(tomorrow['legacy_target_remaining'], 1)
        self.assertEqual(tomorrow['daily_budget_remaining'], 100)
        self.assertTrue(self.candidates())  # remaining distinct content is preserved

    def test_planner_counts_budget_and_no_same_payload_twice(self):
        self.generate('b')
        effective = deepcopy(DEFAULT_POLICY)
        effective['providers'] = {'x': {'daily_target': 3, 'window_start': '11:01', 'window_end': '12:30', 'development_max': 0, 'commercial_min': 0}}
        effective['minimum_lead_minutes'] = 1
        with patch('ocpf_post.portfolio._campaign_ids', return_value=[r['campaign'] for r in self.old] + [r['campaign'] for r in self.generate('c')]):
            result = plan_refill(now=NOW, horizon_minutes=120, policy=effective)
        texts = [builtin_text(r['campaign'], 'x') for r in result['plan']]
        self.assertEqual(len(set(texts)), len(texts))
        self.assertEqual(result['capacity']['x']['daily_budget_remaining'], 3)

    def test_explanation_is_local_read_only_and_exposes_expected_binding(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            result = explain_campaign(self.cid, 'x', now=NOW)
        after = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result['destination']['expected_binding']['account_id'], '12345')
        self.assertFalse(result['destination']['live_identity_verified'])
        self.assertIn('README is a guard', result['copy']['generation'])
        cli = subprocess.run(['sh', './post-once', 'campaign', 'explain', '--campaign', self.cid, '--json'], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(cli.stdout)['campaign'], self.cid)

    def test_reservation_rechecks_ledger_after_plan(self):
        item = {'campaign': self.cid, 'provider': 'x', 'run_at': (NOW + timedelta(minutes=10)).isoformat()}
        def moved_plan(**kwargs):
            self.receipt()
            return {'plan': [item], 'timezone': 'Europe/London'}
        with patch('ocpf_post.portfolio.plan_refill', side_effect=moved_plan):
            result = apply_refill(now=NOW, schedule_creator=lambda **kw: self.fail('must not reserve'))
        self.assertEqual(result['scheduled'], [])
        self.assertIn('after planning', result['errors'][0]['error'])

    def test_generative_explanation_reports_llm_provenance_without_template_misclassification(self):
        text = "Generated LinkedIn evidence copy.\n\nA second paragraph preserves the source and provider boundary."
        digest = hashlib.sha256(text.encode()).hexdigest()
        manifest = {
            "campaign": "OCPF-GEN-TEST",
            "project": "oneclickpostfactory",
            "providers": ["linkedin"],
            "runtime_generated": True,
            "payload_frozen": True,
            "payload_sha256": {"linkedin": digest},
            "source": {
                "type": "evidence_grounded_generation",
                "source_id": "oneclickpostfactory-GENERATIVE-test",
                "repository": "OneClickPostFactory/oneclickpostfactory",
                "path": "README.md",
                "source_sha": "a" * 40,
                "observed_at": NOW.isoformat(),
                "evidence_indexes": [0, 2],
                "template_version": "evidence-grounded-v1",
                "comparison_variant": "baseline",
                "angle_family": "trade_off",
                "novelty_rationale": "Distinct test framing.",
            },
            "assistance": {"mode": "evidence_grounded_llm", "model": "test-model"},
            "admission": {"schema_version": 1, "gate": "scoped_admission"},
            "allocation": {"enabled": True, "lane": "commercial", "priority": 84},
        }
        policy_value = deepcopy(DEFAULT_POLICY)
        with patch("ocpf_post.campaign_explain.builtin_manifest", return_value=manifest), \
             patch("ocpf_post.campaign_explain.builtin_text", return_value=text), \
             patch("ocpf_post.campaign_explain.destination_binding", return_value={"account_id": "urn:li:person:test"}), \
             patch("ocpf_post.campaign_explain.portfolio.delivery_candidates", return_value=[]), \
             patch("ocpf_post.campaign_explain.schedule_records", return_value=[]), \
             patch("ocpf_post.campaign_explain.iter_receipts", return_value=[]), \
             patch("ocpf_post.campaign_explain.read_json", return_value={"repositories": {
                 "OneClickPostFactory/oneclickpostfactory": {
                     "head_sha": "b" * 40, "readme_sha": "a" * 40, "observed_at": NOW.isoformat(),
                 }
             }}), \
             patch("ocpf_post.campaign_explain.portfolio.load_policy", return_value=policy_value), \
             patch("ocpf_post.campaign_explain.portfolio.daily_capacity", return_value={}):
            result = explain_campaign("OCPF-GEN-TEST", "linkedin", now=NOW)
        self.assertIn("Model-authored", result["copy"]["generation"])
        self.assertNotIn("commit title", result["copy"]["generation"])
        self.assertEqual(result["copy"]["paragraphs"], 2)
        self.assertFalse(result["copy"]["editorial_advisories_authoritative"])
        self.assertIn("linkedin_below_editorial_target", result["copy"]["editorial_advisories"])
        self.assertEqual(result["source"]["evidence_indexes"], [0, 2])
        self.assertEqual(result["source"]["angle_family"], "trade_off")
        self.assertEqual(result["assistance"]["mode"], "evidence_grounded_llm")
        self.assertEqual(result["admission"]["gate"], "scoped_admission")

    def test_unknown_campaign_is_an_error(self):
        with self.assertRaises(ValueError):
            explain_campaign('UNKNOWN-001', 'x', now=NOW)
