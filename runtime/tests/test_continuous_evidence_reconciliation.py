from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ocpf_post import engagement as e, local_store, operating_cycles, publication_reconcile, reply_reconcile

NOW = datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc)


def reply_row(identity, post_id, account='123'):
    context = {'post_id': str(int(post_id) - 1), 'parent_post_id': str(int(post_id) - 2),
               'author': 'other', 'text': 'Incoming'}
    draft = {'inbox_id': identity, 'provider': 'x', 'account_id': account,
             'context': context, 'text': 'Reviewed response',
             'expires_at': (NOW + timedelta(hours=12)).isoformat()}
    return {'id': identity, 'provider': 'x', 'account_id': account,
            'campaign': 'TEST-1', 'status': 'published_unverified',
            'post_id': post_id, 'published_at': (NOW - timedelta(minutes=5)).isoformat(),
            'draft': draft, 'review_sha256': e.digest(draft), 'readback_verified': False}


class ContinuousEvidenceReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name,
                         OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start(); self.addCleanup(env.stop)

    def test_reply_provider_failure_backs_off_same_account_and_next_cycle(self):
        first, second = reply_row('one', '11'), reply_row('two', '21')
        local_store.write(e.path(), {'schema_version': 1, 'inbox': {'one': first, 'two': second}, 'polls': {}})
        calls = []
        client = SimpleNamespace(
            account=lambda: SimpleNamespace(account_id='123', username='owner'),
            _bearer=lambda *args, **kwargs: (calls.append(args[0]) or (500, {})),
        )
        with patch('ocpf_post.reply_reconcile.for_account', return_value=client):
            result = reply_reconcile.reconcile(apply=True, now=NOW)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'attention')
        self.assertEqual(result['results'][0]['status'], 'unavailable')
        self.assertEqual(result['results'][1]['status'], 'account_readback_deferred')
        stored = e.read()['inbox']
        self.assertTrue(all(row.get('readback_retry_at') for row in stored.values()))

        calls.clear()
        with patch('ocpf_post.reply_reconcile.for_account', return_value=client):
            deferred = reply_reconcile.reconcile(apply=True, now=NOW + timedelta(minutes=30))
        self.assertEqual(calls, [])
        self.assertEqual(deferred['status'], 'partial')
        self.assertTrue(all(row['status'] == 'readback_deferred' for row in deferred['results']))
        self.assertTrue(all(row['status'] == 'published_unverified' for row in e.read()['inbox'].values()))

    def test_reply_verification_history_is_bounded_and_never_sends(self):
        row = reply_row('one', '11')
        row['verification_history'] = [{'status': 'old', 'observed_at': NOW.isoformat()} for _ in range(25)]
        local_store.write(e.path(), {'schema_version': 1, 'inbox': {'one': row}, 'polls': {}})
        client = SimpleNamespace(account=lambda: SimpleNamespace(account_id='123', username='owner'))
        observed = {'post_id': '11', 'parent_post_id': '10', 'author': '123', 'text': 'Reviewed response'}
        with patch('ocpf_post.reply_reconcile.for_account', return_value=client), \
             patch.object(reply_reconcile, '_lookup_verification', return_value=observed), \
             patch.object(client, 'reply', create=True, side_effect=AssertionError('no reply consequence')):
            result = reply_reconcile.reconcile(apply=True, now=NOW)
        self.assertEqual(result['results'][0]['status'], 'verified')
        saved = e.read()['inbox']['one']
        self.assertEqual(saved['status'], 'published_verified')
        self.assertLessEqual(len(saved['verification_history']), reply_reconcile.MAX_HISTORY)

    def test_campaign_without_existing_id_is_partial_not_collector_failure(self):
        schedule = {'schedule_id': 'sch-1', 'campaign': 'TEST-1', 'provider': 'x',
                    'account_id': '123', 'post_id': None, 'status': 'ambiguous_effect',
                    'text': 'Frozen', 'text_sha256': hashlib.sha256(b'Frozen').hexdigest(),
                    'run_at': NOW.isoformat()}
        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, 'for_account', side_effect=AssertionError('missing ID must not read provider')):
            result = publication_reconcile.reconcile(apply=True, now=NOW)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['reads_attempted'], 0)
        self.assertEqual(result['results'][0]['status'], 'post_id_required')

    def test_linkedin_missing_recorded_read_scope_overrides_backoff_without_provider_get(self):
        schedule = {
            'schedule_id': 'sch-linkedin-scope',
            'campaign': 'GBF-AUTO-01I-79EB3E2',
            'provider': 'linkedin',
            'account_id': 'urn:li:person:owner',
            'post_id': 'urn:li:share:123456',
            'status': 'published_unverified',
            'text': 'Frozen LinkedIn copy',
            'text_sha256': hashlib.sha256(b'Frozen LinkedIn copy').hexdigest(),
            'run_at': (NOW - timedelta(hours=1)).isoformat(),
            'updated_at': NOW.isoformat(),
        }
        readback_key = publication_reconcile._key(
            {key: schedule.get(key) for key in publication_reconcile.KEYS}
        )
        retry_at = (NOW + timedelta(hours=1)).isoformat()
        local_store.write(
            Path(self.temp.name) / 'publication-readbacks.json',
            {
                'schema_version': 1,
                'observations': {
                    readback_key: {
                        **{key: schedule.get(key) for key in publication_reconcile.KEYS},
                        'original_status': 'published_unverified',
                        'status': 'unavailable',
                        'observed_at': NOW.isoformat(),
                        'http_status': 500,
                        'retry_at': retry_at,
                    }
                },
                'account_backoff': {
                    'linkedin:urn:li:person:owner': {
                        'http_status': 500,
                        'retry_at': retry_at,
                    }
                },
            },
        )
        permission = {'status': 'missing', 'required_scope': 'r_member_social', 'scope_recorded': True}
        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, '_linkedin_read_permission',
                          side_effect=lambda *args, **kwargs: dict(permission)), \
             patch.object(publication_reconcile, 'for_account',
                          side_effect=AssertionError('missing scope/backoff must block provider routing')), \
             patch.object(publication_reconcile, '_read',
                          side_effect=AssertionError('missing scope must block provider GET')):
            result = publication_reconcile.reconcile(apply=True, now=NOW)
            self.assertFalse(publication_reconcile.pending())

        row = result['results'][0]
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['reads_attempted'], 0)
        self.assertEqual(row['status'], 'readback_permission_required')
        self.assertEqual(row['required_scope'], 'r_member_social')
        self.assertTrue(row['scope_recorded'])
        self.assertFalse(row['automatic_retry'])
        self.assertEqual(row['next_action'], 'provider_approval_required_before_reauthorise')

        permission['status'] = 'granted'
        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, '_linkedin_read_permission',
                          side_effect=lambda *args, **kwargs: dict(permission)), \
             patch.object(publication_reconcile, 'for_account',
                          side_effect=AssertionError('pending scope inspection must remain provider-free')):
            self.assertTrue(publication_reconcile.pending())

    def _threads_ambiguous_schedule(self):
        return {
            'schedule_id': 'sch-threads-ambiguous',
            'campaign': 'AGENTPROOF-AUTO-02I-B062706',
            'provider': 'threads',
            'account_id': '123',
            'post_id': None,
            'status': 'ambiguous_effect',
            'text': 'Frozen AgentProof copy',
            'text_sha256': hashlib.sha256(b'Frozen AgentProof copy').hexdigest(),
            'run_at': (NOW - timedelta(minutes=1)).isoformat(),
            'updated_at': NOW.isoformat(),
        }

    def test_campaign_reconcile_filter_targets_only_requested_effect(self):
        wanted = self._threads_ambiguous_schedule()
        other = {**wanted, "schedule_id": "sch-other", "campaign": "OTHER-CAMPAIGN"}
        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [wanted, other], [])), \
             patch.object(publication_reconcile, 'for_account',
                          side_effect=AssertionError('preview must not read provider')):
            result = publication_reconcile.reconcile(
                apply=False,
                now=NOW,
                campaign=wanted["campaign"],
                provider="threads",
                schedule_id=wanted["schedule_id"],
            )
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["schedule_id"], wanted["schedule_id"])
        self.assertEqual(result["filters"]["campaign"], wanted["campaign"])
        self.assertEqual(result["filters"]["provider"], "threads")
        self.assertEqual(result["filters"]["schedule_id"], wanted["schedule_id"])

    def test_threads_no_id_ambiguity_preview_never_reads_provider(self):
        schedule = self._threads_ambiguous_schedule()
        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, 'for_account',
                          side_effect=AssertionError('preview must not read provider')):
            result = publication_reconcile.reconcile(apply=False, now=NOW)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['forensic_searches_attempted'], 0)
        self.assertEqual(result['results'][0]['status'], 'forensic_search_due')

    def test_threads_no_id_ambiguity_resolves_only_one_exact_candidate_and_never_sends(self):
        schedule = self._threads_ambiguous_schedule()
        calls = []
        client = SimpleNamespace(
            readonly_account=lambda: SimpleNamespace(account_id='123', username='owner'),
            recent_threads=lambda **kwargs: {
                'posts': [{
                    'id': '987654321',
                    'text': schedule['text'],
                    'permalink': 'https://www.threads.net/@owner/post/abc',
                    'username': 'owner',
                    'timestamp': NOW.isoformat(),
                }],
                'reads': 1,
                'truncated': False,
            },
            _readonly_bearer=lambda *args, **kwargs: (
                calls.append((args, kwargs))
                or (200, {}, {
                    'id': '987654321',
                    'text': schedule['text'],
                    'username': 'owner',
                    'permalink': 'https://www.threads.net/@owner/post/abc',
                    'timestamp': NOW.isoformat(),
                })
            ),
            publish=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('forensic reconciliation must never publish')
            ),
        )
        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, 'for_account', return_value=client):
            result = publication_reconcile.reconcile(apply=True, now=NOW)
            self.assertFalse(publication_reconcile.pending())
        self.assertEqual(result['status'], 'observed')
        row = result['results'][0]
        self.assertEqual(row['status'], 'verified_discovered')
        self.assertEqual(row['discovered_post_id'], '987654321')
        self.assertEqual(row['candidate_count'], 1)
        self.assertEqual(result['forensic_searches_attempted'], 1)
        self.assertEqual(len(calls), 1)
        saved = local_store.read(Path(self.temp.name) / 'publication-readbacks.json')
        self.assertEqual(next(iter(saved['observations'].values()))['status'], 'verified_discovered')

    def test_terminal_threads_forensic_no_match_stops_unattended_search_but_allows_targeted_review(self):
        schedule = self._threads_ambiguous_schedule()
        client = SimpleNamespace(
            readonly_account=lambda: SimpleNamespace(account_id='123', username='owner'),
            recent_threads=lambda **kwargs: {'posts': [], 'reads': 1, 'truncated': False},
            _readonly_bearer=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError('no candidate means no known-ID readback')
            ),
        )
        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, 'for_account', return_value=client):
            first = publication_reconcile.reconcile(apply=True, now=NOW)
            self.assertFalse(publication_reconcile.pending())

        row = first['results'][0]
        self.assertEqual(row['status'], 'forensic_no_match')
        self.assertEqual(row['candidate_count'], 0)
        self.assertFalse(row['automatic_retry'])
        self.assertEqual(row['next_action'], 'manual_review_no_resend')

        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, 'for_account',
                          side_effect=AssertionError('unattended preview must not read provider')):
            unattended = publication_reconcile.reconcile(apply=False, now=NOW + timedelta(minutes=5))
        cached = unattended['results'][0]
        self.assertEqual(cached['status'], 'forensic_review_required')
        self.assertEqual(cached['previous_status'], 'forensic_no_match')
        self.assertFalse(cached['automatic_retry'])
        self.assertEqual(cached['next_action'], 'targeted_manual_review_without_resend')
        self.assertTrue(cached['cached'])
        self.assertEqual(unattended['forensic_searches_attempted'], 0)

        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, 'for_account',
                          side_effect=AssertionError('targeted preview is GET-free until --apply')):
            targeted = publication_reconcile.reconcile(
                apply=False,
                now=NOW + timedelta(minutes=5),
                campaign=schedule['campaign'],
                provider=schedule['provider'],
                schedule_id=schedule['schedule_id'],
            )
        self.assertEqual(targeted['results'][0]['status'], 'forensic_search_due')
        self.assertEqual(targeted['forensic_searches_attempted'], 0)

    def test_threads_forensic_multiple_exact_candidates_stay_unresolved(self):
        schedule = self._threads_ambiguous_schedule()
        posts = [
            {
                'id': str(987654321 + index),
                'text': schedule['text'],
                'permalink': f'https://www.threads.net/@owner/post/{index}',
                'username': 'owner',
                'timestamp': (NOW + timedelta(seconds=index)).isoformat(),
            }
            for index in range(2)
        ]
        client = SimpleNamespace(
            readonly_account=lambda: SimpleNamespace(account_id='123', username='owner'),
            recent_threads=lambda **kwargs: {'posts': posts, 'reads': 1, 'truncated': False},
            _readonly_bearer=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError('multiple candidates must not be treated as known-ID evidence')
            ),
        )
        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, 'for_account', return_value=client):
            result = publication_reconcile.reconcile(apply=True, now=NOW)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['results'][0]['status'], 'forensic_multiple_matches')
        self.assertEqual(result['results'][0]['candidate_count'], 2)
        self.assertFalse(result['results'][0]['automatic_retry'])
        self.assertEqual(result['results'][0]['next_action'], 'manual_review_no_resend')

    def test_overlapping_collection_is_a_structured_no_op(self):
        from contextlib import contextmanager

        @contextmanager
        def busy(_path):
            yield False

        with patch.object(local_store, 'try_locked', busy), \
             patch.object(operating_cycles, 'stage', side_effect=AssertionError('busy invocation must do no work')):
            result = operating_cycles.collect()
        self.assertEqual(result['status'], 'already_running')
        self.assertEqual(result['stages'], [])
        self.assertEqual(operating_cycles.outcome(result)[0], 'idle')
        self.assertIn('performed no collection', result['boundary'])


    def test_collection_wakes_readback_only_for_unresolved_existing_effects(self):
        calls = []
        def fake_stage(name, args, seconds):
            calls.append((name, tuple(args), seconds))
            return {'stage': name, 'status': 'completed', 'exit_code': 0, 'outcome_counts': {}}
        with patch.object(operating_cycles, 'stage', side_effect=fake_stage), \
             patch.object(operating_cycles, '_campaign_readback_pending', return_value=True), \
             patch.object(operating_cycles, '_reply_readback_pending', return_value=True), \
             patch('ocpf_post.vault_sync.policies', return_value={}):
            operating_cycles.collect()
        names = [row[0] for row in calls]
        self.assertEqual(names, ['source-observation', 'campaign-readback', 'metrics',
                                 'performance-feedback', 'inbound-replies', 'reply-readback',
                                 'outcome-connectors', 'acceptance-views', 'operations'])
        self.assertLess(names.index('campaign-readback'), names.index('metrics'))
        self.assertGreater(names.index('reply-readback'), names.index('inbound-replies'))
        campaign_args = calls[names.index('campaign-readback')][1]
        self.assertIn('ocpf_post.open_work', campaign_args)
        self.assertIn('readback', campaign_args)
        self.assertIn('--apply', campaign_args)
        self.assertNotIn('publish', ' '.join(' '.join(row[1]) for row in calls))
        self.assertNotIn('run-due', ' '.join(' '.join(row[1]) for row in calls))

    def test_collection_skips_readback_workers_when_no_unresolved_effects(self):
        calls = []
        def fake_stage(name, args, seconds):
            calls.append(name)
            return {'stage': name, 'status': 'completed', 'exit_code': 0, 'outcome_counts': {}}
        with patch.object(operating_cycles, 'stage', side_effect=fake_stage), \
             patch.object(operating_cycles, '_campaign_readback_pending', return_value=False), \
             patch.object(operating_cycles, '_reply_readback_pending', return_value=False), \
             patch('ocpf_post.vault_sync.policies', return_value={}):
            operating_cycles.collect()
        self.assertNotIn('campaign-readback', calls)
        self.assertNotIn('reply-readback', calls)


if __name__ == '__main__':
    unittest.main()
