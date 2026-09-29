from copy import deepcopy
from datetime import timedelta
import json
import unittest

import test_engagement as f
from ocpf_post import engagement as e, local_store, reply_model, reply_work, reply_worker as w
from ocpf_post.state import config_dir


class ReplyWorkTests(unittest.TestCase):
    setUp = f.EngagementTests.setUp
    collect = f.EngagementTests.collect

    def enable_worker(self):
        settings = deepcopy(w.DEFAULT)
        settings.update(
            enabled=True,
            accounts={
                'x:' + e.ACCOUNTS['x']: {
                    'mode': 'automatic',
                    'x_approval_reference': 'owner-approved-test',
                }
            },
        )
        local_store.write(config_dir() / 'reply-worker-policy.json', settings)

    def work_document(self, entry=None, *, modified_at=None):
        entries = [] if entry is None else [entry]
        return {
            'document_id': 'replyWorkDoc12345',
            'version': '7',
            'modified_at': modified_at or self.now.isoformat(),
            'text': (
                reply_work.BEGIN + '\n'
                + json.dumps({'schema_version': 1, 'entries': entries}, ensure_ascii=False, indent=2)
                + '\n' + reply_work.END
            ),
        }

    def candidate(self, request, text='Keep the receipt tied to the exact provider effect.', status='CANDIDATE'):
        entry = {
            'inbox_id': request['inbox_id'],
            'provider': request['provider'],
            'account_id': request['account_id'],
            'campaign': request['campaign'],
            'context_sha256': request['context_sha256'],
            'incoming_post_id': request['incoming_post_id'],
            'parent_post_id': request['parent_post_id'],
            'reply_text': text,
            'status': status,
            'candidate_sha256': '',
        }
        entry['candidate_sha256'] = reply_work.candidate_digest(entry)
        return entry

    def test_work_is_primary_authoring_plane_and_reviewed_candidate_sends_only_under_local_policy(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()

        # The normal reply timer must wait for Work rather than spending an API call.
        first = w.run(
            apply=True,
            now=self.now,
            model=lambda *a, **kw: self.fail('local model must not run while Work mode is enabled'),
            sender=lambda *a, **kw: self.fail('nothing is locally authorised yet'),
        )
        identity = next(iter(e.read()['inbox']))
        self.assertEqual(first['items'][identity]['status'], 'awaiting_work_reply')
        self.assertEqual(sum(first['usage'].values()), 0)
        self.assertFalse(self.client.sent)

        request = reply_work.requests(now=self.now)['requests'][0]
        entry = self.candidate(request)
        synced = reply_work.sync(
            apply=True,
            reader=lambda _document_id: self.work_document(entry),
            now=self.now,
        )
        self.assertEqual(synced['status'], 'synced')
        self.assertEqual(synced['applied'][0]['result'], 'stored_candidate')
        self.assertNotIn('draft', e.read()['inbox'][identity])
        self.assertEqual(w.state()['items'][identity]['status'], 'awaiting_work_reply')
        w.admit_work_candidates(now=self.now)
        self.assertEqual(e.read()['inbox'][identity]['draft']['text'], entry['reply_text'])
        self.assertEqual(w.state()['items'][identity]['model'], 'chatgpt-work')
        self.assertEqual(w.state()['items'][identity]['local_authority_reference'], 'owner-approved-test')
        self.assertEqual(w.state()['items'][identity]['work_candidate_sha256'], entry['candidate_sha256'])
        self.assertNotIn('work_approval_sha256', w.state()['items'][identity])

        sent = w.run(
            apply=True,
            now=self.now + timedelta(minutes=1),
            model=lambda *a, **kw: self.fail('reviewed Work candidate must be reused exactly'),
            sender=lambda reply_id, **kw: e.send(
                reply_id, **kw, factory=lambda _: self.client
            ),
        )
        self.assertEqual(sent['items'][identity]['status'], 'published_verified')
        self.assertEqual(sum(sent['usage'].values()), 0)
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.client.sent[0][0], entry['reply_text'])

    def test_reviewed_candidate_does_not_become_sendable_without_local_automatic_policy(self):
        settings = deepcopy(w.DEFAULT)
        settings.update(
            enabled=True,
            accounts={
                'x:' + e.ACCOUNTS['x']: {
                    'mode': 'review',
                    'x_approval_reference': '',
                }
            },
        )
        local_store.write(config_dir() / 'reply-worker-policy.json', settings)
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()

        request = reply_work.requests(now=self.now)['requests'][0]
        entry = self.candidate(request)
        synced = reply_work.sync(
            apply=True,
            reader=lambda _document_id: self.work_document(entry),
            now=self.now,
        )
        identity = request['inbox_id']
        self.assertEqual(synced['candidate_count'], 1)
        self.assertEqual(synced['applied'][0]['result'], 'stored_candidate')
        self.assertNotIn('draft', e.read()['inbox'][identity])
        admitted = w.admit_work_candidates(now=self.now)
        self.assertEqual(admitted[0]['authority'], 'none')
        self.assertEqual(w.state()['items'][identity]['reason'], 'account_requires_explicit_send')
        self.assertNotIn('local_authority_reference', w.state()['items'][identity])

        later = w.run(
            apply=True,
            now=self.now + timedelta(minutes=1),
            model=lambda *a, **kw: self.fail('review-mode candidate must not call local model'),
            sender=lambda *a, **kw: self.fail('review-mode candidate must not send'),
        )
        self.assertEqual(later['items'][identity]['status'], 'review_required')
        self.assertFalse(self.client.sent)

    def test_legacy_approved_entry_shape_is_rejected(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        request = reply_work.requests(now=self.now)['requests'][0]
        entry = self.candidate(request)
        entry['status'] = 'APPROVED'
        entry['approval_sha256'] = entry.pop('candidate_sha256')
        with self.assertRaisesRegex(ValueError, 'entry shape|status'):
            reply_work.prepare(self.work_document(entry), now=self.now)

    def test_legacy_machine_approval_section_is_fail_closed_during_migration(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        request = reply_work.requests(now=self.now)['requests'][0]
        legacy = {
            'inbox_id': request['inbox_id'],
            'provider': request['provider'],
            'account_id': request['account_id'],
            'campaign': request['campaign'],
            'context_sha256': request['context_sha256'],
            'incoming_post_id': request['incoming_post_id'],
            'parent_post_id': request['parent_post_id'],
            'reply_text': 'Legacy text must not import.',
            'status': 'APPROVED',
            'approval_sha256': '0' * 64,
        }
        document = self.work_document()
        document['text'] = (
            reply_work.LEGACY_BEGIN + '\n'
            + json.dumps({'schema_version': 1, 'entries': [legacy]}, ensure_ascii=False, indent=2)
            + '\n' + reply_work.LEGACY_END
        )
        result = reply_work.sync(
            apply=True,
            reader=lambda _document_id: document,
            now=self.now,
        )
        self.assertEqual(result['decision_count'], 0)
        self.assertEqual(result['candidate_count'], 0)
        self.assertEqual(e.read()['inbox'][request['inbox_id']]['status'], 'pending')

    def test_work_skip_becomes_durable_no_response_and_does_not_requeue(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        request = reply_work.requests(now=self.now)['requests'][0]
        entry = self.candidate(request, text='', status='SKIP')

        result = reply_work.sync(
            apply=True,
            reader=lambda _document_id: self.work_document(entry),
            now=self.now,
        )
        identity = request['inbox_id']
        self.assertEqual(result['skip_count'], 1)
        self.assertEqual(result['applied'][0]['result'], 'stored_candidate')
        w.admit_work_candidates(now=self.now)
        self.assertEqual(w.state()['items'][identity]['reason'], 'work_skip')
        self.assertEqual(reply_work.requests(now=self.now)['count'], 0)
        later = w.run(
            apply=True,
            now=self.now + timedelta(minutes=16),
            model=lambda *a, **kw: self.fail('Work SKIP must not call the local model'),
            sender=lambda *a, **kw: self.fail('Work SKIP must never send'),
        )
        self.assertEqual(later['items'][identity]['status'], 'no_response_needed')
        self.assertFalse(self.client.sent)

    def test_work_hold_stays_review_required_without_requeue_or_send(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        request = reply_work.requests(now=self.now)['requests'][0]
        entry = self.candidate(request, text='', status='HOLD')

        result = reply_work.sync(
            apply=True,
            reader=lambda _document_id: self.work_document(entry),
            now=self.now,
        )
        identity = request['inbox_id']
        self.assertEqual(result['hold_count'], 1)
        self.assertEqual(result['applied'][0]['result'], 'stored_candidate')
        w.admit_work_candidates(now=self.now)
        self.assertEqual(w.state()['items'][identity]['reason'], 'work_hold')
        self.assertEqual(reply_work.requests(now=self.now)['count'], 0)
        later = w.run(
            apply=True,
            now=self.now + timedelta(minutes=16),
            model=lambda *a, **kw: self.fail('Work HOLD must not call the local model'),
            sender=lambda *a, **kw: self.fail('Work HOLD must never send'),
        )
        self.assertEqual(later['items'][identity]['status'], 'review_required')
        self.assertFalse(self.client.sent)

    def test_work_decision_never_overrides_unowned_manual_draft(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        request = reply_work.requests(now=self.now)['requests'][0]
        identity = request['inbox_id']
        manual = e.draft(identity, 'Keep the human-authored draft.', now=self.now)
        entry = self.candidate(request, text='', status='SKIP')

        result = reply_work.sync(
            apply=True,
            reader=lambda _document_id: self.work_document(entry),
            now=self.now,
        )
        self.assertEqual(result['applied'][0]['result'], 'stored_candidate')
        self.assertEqual(w.admit_work_candidates(now=self.now)[0]['result'], 'manual_draft_preserved')
        self.assertEqual(e.read()['inbox'][identity]['draft'], manual['draft'])
        self.assertNotIn(identity, w.state()['items'])

    def test_expired_draft_never_consumes_work_capacity(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        identity = next(iter(e.read()['inbox']))
        e.draft(
            identity,
            'This draft is deliberately old.',
            now=self.now - timedelta(days=2),
        )

        self.assertEqual(reply_work.requests(now=self.now)['count'], 0)
        result = w.run(
            apply=True,
            now=self.now,
            model=lambda *a, **kw: self.fail('expired draft must not call local model'),
            sender=lambda *a, **kw: self.fail('expired draft must not send'),
        )
        self.assertEqual(result['items'][identity]['status'], 'review_required')
        self.assertEqual(result['items'][identity]['reason'], 'draft_expired')
        self.assertEqual(sum(result['usage'].values()), 0)
        self.assertFalse(self.client.sent)

    def test_old_pending_reply_expires_locally_without_work_or_model(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        identity = next(iter(e.read()['inbox']))
        data = e.read()
        data['inbox'][identity]['first_seen_at'] = e.stamp(self.now - timedelta(days=2))
        data['inbox'][identity]['last_seen_at'] = e.stamp(self.now - timedelta(days=2))
        local_store.write(e.path(), data)

        self.assertEqual(reply_work.requests(now=self.now)['count'], 0)
        result = w.run(
            apply=True,
            now=self.now,
            model=lambda *a, **kw: self.fail('stale pending reply must not call local model'),
            sender=lambda *a, **kw: self.fail('stale pending reply must not send'),
        )
        self.assertEqual(result['items'][identity]['status'], 'no_response_needed')
        self.assertEqual(result['items'][identity]['reason'], 'reply_window_expired')
        self.assertEqual(sum(result['usage'].values()), 0)
        self.assertFalse(self.client.sent)

    def test_newest_fresh_reply_is_projected_first(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        first_identity = next(iter(e.read()['inbox']))

        data = e.read()
        first = data['inbox'][first_identity]
        second_context = {
            'post_id': '202',
            'parent_post_id': first['context']['parent_post_id'],
            'author': '998',
            'text': 'A newer useful question.',
        }
        second_identity = e.digest(['x', e.ACCOUNTS['x'], second_context['post_id']])
        data['inbox'][first_identity]['first_seen_at'] = e.stamp(self.now - timedelta(minutes=10))
        data['inbox'][second_identity] = {
            **{k: first[k] for k in (
                'provider', 'account_id', 'campaign', 'conversation_root_id', 'conversation_depth'
            )},
            'id': second_identity,
            'context': second_context,
            'status': 'pending',
            'first_seen_at': e.stamp(self.now - timedelta(minutes=1)),
            'last_seen_at': e.stamp(self.now - timedelta(minutes=1)),
        }
        local_store.write(e.path(), data)

        projected = reply_work.requests(now=self.now, limit=1)
        self.assertEqual(projected['count'], 1)
        self.assertEqual(projected['requests'][0]['inbox_id'], second_identity)

    def test_context_change_blocks_work_approval_without_mutating_draft(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        request = reply_work.requests(now=self.now)['requests'][0]
        entry = self.candidate(request)

        data = e.read()
        identity = request['inbox_id']
        data['inbox'][identity]['context']['text'] = 'The incoming comment changed.'
        local_store.write(e.path(), data)

        result = reply_work.sync(
            apply=True,
            reader=lambda _document_id: self.work_document(entry),
            now=self.now,
        )
        self.assertEqual(result['candidate_count'], 0)
        self.assertEqual(result['skipped'][0]['reason'], 'context_or_identity_changed')
        self.assertEqual(e.read()['inbox'][identity]['status'], 'pending')

    def test_stale_work_document_never_creates_sendable_reply(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True, max_age_minutes=60)
        self.collect()
        request = reply_work.requests(now=self.now)['requests'][0]
        entry = self.candidate(request)

        result = reply_work.sync(
            apply=True,
            reader=lambda _document_id: self.work_document(
                entry, modified_at=(self.now - timedelta(hours=2)).isoformat()
            ),
            now=self.now,
        )
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(e.read()['inbox'][request['inbox_id']]['status'], 'pending')


    def import_candidate(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        request = reply_work.requests(now=self.now)['requests'][0]
        entry = self.candidate(request)
        reply_work.sync(apply=True, reader=lambda _: self.work_document(entry), now=self.now)
        return request['inbox_id'], entry

    def test_import_never_mutates_engagement_worker_or_authority(self):
        self.enable_worker()
        reply_work.configure('replyWorkDoc12345', apply=True)
        self.collect()
        before = (deepcopy(e.read()), deepcopy(w.state()), deepcopy(w.policy()))
        entry = self.candidate(reply_work.requests(now=self.now)['requests'][0])
        reply_work.sync(apply=True, reader=lambda _: self.work_document(entry), now=self.now)
        self.assertEqual(before, (e.read(), w.state(), w.policy()))
        self.assertEqual(local_store.read(reply_work.candidates_path())['entries'], [entry])

    def test_repeated_import_does_not_extend_draft_or_replay_send(self):
        identity, entry = self.import_candidate()
        w.admit_work_candidates(now=self.now)
        draft = deepcopy(e.read()['inbox'][identity]['draft'])
        reply_work.sync(apply=True, reader=lambda _: self.work_document(entry), now=self.now + timedelta(minutes=2))
        w.admit_work_candidates(now=self.now + timedelta(minutes=2))
        self.assertEqual(e.read()['inbox'][identity]['draft'], draft)
        for minute in (3, 4):
            w.run(apply=True, now=self.now + timedelta(minutes=minute),
                  model=lambda *a, **kw: self.fail('no API fallback'),
                  sender=lambda reply_id, **kw: e.send(reply_id, **kw, factory=lambda _: self.client))
        self.assertEqual(len(self.client.sent), 1)

    def test_revocation_between_import_and_worker_blocks_send(self):
        identity, _ = self.import_candidate()
        settings = w.policy()
        settings['accounts']['x:' + e.ACCOUNTS['x']]['mode'] = 'review'
        local_store.write(config_dir() / 'reply-worker-policy.json', settings)
        w.run(apply=True, now=self.now,
              model=lambda *a, **kw: self.fail('no API fallback'),
              sender=lambda *a, **kw: self.fail('revoked authority'))
        self.assertEqual(w.state()['items'][identity]['status'], 'review_required')

    def test_context_changed_after_import_is_not_admitted(self):
        identity, _ = self.import_candidate()
        data = e.read()
        data['inbox'][identity]['context']['text'] = 'Changed after import'
        local_store.write(e.path(), data)
        self.assertEqual(w.admit_work_candidates(now=self.now), [])
        self.assertNotIn('draft', e.read()['inbox'][identity])

    def test_expired_import_is_not_admitted(self):
        identity, _ = self.import_candidate()
        self.assertEqual(w.admit_work_candidates(now=self.now + timedelta(hours=3)), [])
        self.assertNotIn('draft', e.read()['inbox'][identity])

    def test_legacy_reviewed_status_rejected(self):
        identity, entry = self.import_candidate()
        entry['status'] = 'REVIEWED'
        entry['candidate_sha256'] = reply_work.candidate_digest(entry)
        with self.assertRaisesRegex(ValueError, 'status'):
            reply_work.prepare(self.work_document(entry), now=self.now)

    def test_no_authority_fields_exported(self):
        self.import_candidate()
        request = reply_work.requests(now=self.now)['requests'][0]
        self.assertNotIn('x_approval_reference', request)
        self.assertNotIn('reply_mode', request)

    def test_ready_candidate_with_broken_draft_never_calls_api(self):
        identity, _ = self.import_candidate()
        w.admit_work_candidates(now=self.now)
        data = e.read()
        data['inbox'][identity]['status'] = 'pending'
        data['inbox'][identity].pop('draft', None)
        local_store.write(e.path(), data)
        w.run(apply=True, now=self.now,
              model=lambda *a, **kw: self.fail('no API fallback'),
              sender=lambda *a, **kw: self.fail('broken draft must not send'))
        self.assertEqual(sum(w.state()['usage'].values()), 0)
        self.assertFalse(self.client.sent)


if __name__ == '__main__':
    unittest.main()

