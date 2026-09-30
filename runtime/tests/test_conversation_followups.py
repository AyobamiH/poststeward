from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch, Mock
import unittest
import test_engagement as f
from ocpf_post import engagement as e


class ConversationFollowupTests(unittest.TestCase):
    setUp = f.EngagementTests.setUp
    collect = f.EngagementTests.collect
    drafted = f.EngagementTests.drafted

    def replied(self):
        identity, sha = self.drafted()
        e.send(identity, expected_sha256=sha, live=True, now=self.now, factory=lambda _: self.client)
        return identity

    def test_nested_x_reply_is_collected_with_ancestor_context_and_can_be_sent(self):
        first = self.replied()
        second = {'id': '401', 'author_id': '999', 'text': 'What if that times out?',
                  'referenced_tweets': [{'type': 'replied_to', 'id': '301'}]}
        self.client.rows = [second]; self.client.context = second; self.client.meta = {'newest_id': '401'}
        later = self.now + timedelta(minutes=16)
        e.sync(apply=True, now=later, factory=lambda _: self.client)
        identity = e.digest(['x', e.ACCOUNTS['x'], '401'])
        conversation = e.conversation(identity, now=later)
        self.assertEqual(conversation['depth'], 1)
        self.assertEqual(conversation['root_post_id'], '101')
        self.assertEqual(conversation['history'][1]['text'], e.read()['inbox'][first]['draft']['text'])
        draft = e.draft(identity, 'Keep the outcome unknown until provider evidence resolves it.', now=later)
        original_lookup = e.lookup
        def lookup(client, pid):
            if pid == '501':
                return {'post_id':'501', 'parent_post_id':'401', 'author': e.ACCOUNTS['x'], 'text':draft['draft']['text']}
            return original_lookup(client, pid)
        with patch.object(self.client, 'reply', return_value={'id':'501'}) as send, patch.object(e, 'lookup', side_effect=lookup):
            result = e.send(identity, expected_sha256=draft['review_sha256'], live=True, now=later, factory=lambda _:self.client)
        self.assertEqual(result['result'], 'published_verified')
        self.assertEqual(send.call_args.args[1], '401')
        self.assertEqual(e.conversation_targets('x', e.ACCOUNTS['x'], later)['501']['depth'], 2)

    def test_unverified_wrong_account_or_unrelated_reply_cannot_extend_scope(self):
        identity = self.replied(); data = e.read()
        for change in ({'status':'published_unverified'}, {'account_id':'9999'}, {'campaign':'WRONG'}):
            changed = deepcopy(data); changed['inbox'][identity].update(change)
            self.assertNotIn('301', e.conversation_targets('x', e.ACCOUNTS['x'], self.now, changed))

    def test_recent_verified_response_keeps_older_root_in_scope_without_unbounded_history(self):
        identity = self.replied(); data = e.read()
        with patch.object(e, 'own_publications', return_value={'101': {'campaign':'TEST-1','published_at':e.stamp(self.now-timedelta(days=9))}}):
            self.assertIn('301', e.conversation_targets('x', e.ACCOUNTS['x'], self.now, data))
            data['inbox'][identity]['published_at'] = e.stamp(self.now-timedelta(days=8))
            self.assertEqual(e.conversation_targets('x', e.ACCOUNTS['x'], self.now, data), {})

    def test_threads_scans_verified_reply_ids_with_existing_rotating_cursor(self):
        identity = self.replied(); data = e.read(); row = data['inbox'][identity]
        row.update(provider='threads', account_id=e.ACCOUNTS['threads'])
        row['id'] = e.digest(['threads', e.ACCOUNTS['threads'], '201'])
        row['draft'].update(provider='threads', account_id=e.ACCOUNTS['threads'], inbox_id=row['id'])
        data['inbox'] = {row['id']: row}
        with patch.object(e, 'own_publications', return_value={'101':{'campaign':'TEST-1','published_at':e.stamp(self.now)}}):
            targets = e.conversation_targets('threads', e.ACCOUNTS['threads'], self.now, data)
        self.assertIn('301', targets)
        client = Mock(name='client'); client.name = 'threads'
        client._bearer.return_value = (200, {}, {'data': []})
        e.fetch_page(client, Mock(), {'root_scans':{'101':e.stamp(self.now)}}, targets, self.now)
        self.assertTrue(client._bearer.call_args.args[0].endswith('/301/replies'))
