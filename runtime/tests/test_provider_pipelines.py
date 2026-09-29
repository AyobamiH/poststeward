from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpf_post.provider_import import import_linkedin, import_threads
from ocpf_post.providers.base import AmbiguousProviderEffect, ProviderRejected
from ocpf_post.providers.linkedin import LinkedInProvider
from ocpf_post.providers.threads import ThreadsProvider
from ocpf_post.state import provider_settings_file, provider_token_file, write_private_json


class ProviderPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cfg = os.environ.get("OCPF_POST_CONFIG_DIR")
        os.environ["OCPF_POST_CONFIG_DIR"] = self.tmp.name

    def tearDown(self) -> None:
        if self.old_cfg is None:
            os.environ.pop("OCPF_POST_CONFIG_DIR", None)
        else:
            os.environ["OCPF_POST_CONFIG_DIR"] = self.old_cfg
        self.tmp.cleanup()

    def test_threads_import_and_account(self) -> None:
        import_threads("tok")
        with patch(
            "ocpf_post.providers.threads.request_json",
            return_value=(
                200,
                {},
                {
                    "id": "1",
                    "username": "factory",
                    "name": "Factory",
                    "threads_biography": "Web design, automation and products we build.",
                },
            ),
        ):
            account = ThreadsProvider().account()
        self.assertEqual(account.username, "factory")
        self.assertEqual(account.account_id, "1")
        self.assertEqual(
            account.biography,
            "Web design, automation and products we build.",
        )

    def test_threads_auto_publish_is_one_post_and_readback_is_separate(self) -> None:
        import_threads("tok")
        with patch("ocpf_post.providers.threads.request_json", return_value=(200, {}, {"id": "123"})) as request:
            provider = ThreadsProvider()
            result = provider.publish("hello")
        self.assertEqual(result, {"id": "123", "publishing_method": "auto_publish_text"})
        self.assertEqual(request.call_count, 1)
        self.assertTrue(request.call_args.args[0].endswith('/me/threads'))
        self.assertEqual(request.call_args.kwargs['query'],
                         {'text': 'hello', 'media_type': 'TEXT', 'auto_publish_text': 'true'})
        with patch.object(provider, '_bearer', return_value=(200, {},
                {'id': '123', 'text': 'hello', 'permalink': 'https://www.threads.net/@factory/post/abc'})):
            self.assertTrue(provider.verify_post('123', 'hello'))
        self.assertEqual(provider.post_url(type('Account', (), {'username':'factory'})(), '123'),
                         'https://www.threads.net/@factory/post/abc')

    def test_threads_reply_uses_native_reply_to_id(self) -> None:
        import_threads("tok")
        with patch("ocpf_post.providers.threads.request_json", return_value=(200, {}, {"id": "456"})) as request:
            result = ThreadsProvider().reply("continuation", "123")
        self.assertEqual(result["id"], "456")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(
            request.call_args.kwargs["query"],
            {
                "media_type": "TEXT",
                "text": "continuation",
                "auto_publish_text": "true",
                "reply_to_id": "123",
            },
        )

    def test_threads_forensic_listing_is_get_only_and_never_refreshes(self) -> None:
        import_threads("tok")
        payload = {
            "data": [{
                "id": "123",
                "text": "Frozen",
                "permalink": "https://www.threads.net/@factory/post/abc",
                "username": "factory",
                "timestamp": "2026-09-11T18:38:00+0000",
            }],
            "paging": {"cursors": {}},
        }
        provider = ThreadsProvider()
        with patch.object(provider, "refresh", side_effect=AssertionError("forensic GET must not refresh")), \
             patch("ocpf_post.providers.threads.request_json", return_value=(200, {}, payload)) as request:
            result = provider.recent_threads(
                since_epoch=1789151280,
                until_epoch=1789152480,
                limit=50,
                max_pages=3,
            )
        self.assertEqual(result["reads"], 1)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["posts"][0]["id"], "123")
        self.assertEqual(request.call_args.kwargs["method"], "GET")
        self.assertEqual(request.call_args.args[0], "https://graph.threads.net/v1.0/me/threads")
        self.assertEqual(request.call_args.kwargs["query"]["since"], "1789151280")
        self.assertEqual(request.call_args.kwargs["query"]["until"], "1789152480")

    def test_threads_auto_publish_transport_failure_never_falls_back(self) -> None:
        from ocpf_post.providers.http import TransportError
        import_threads("tok")
        with patch("ocpf_post.providers.threads.request_json", side_effect=TransportError('private request URL')) as request:
            with self.assertRaises(AmbiguousProviderEffect) as error:
                ThreadsProvider().publish('hello')
        self.assertEqual(request.call_count, 1)
        self.assertNotIn('private request URL', str(error.exception))

    def test_threads_missing_or_invalid_response_never_becomes_safe_to_retry(self) -> None:
        import_threads("tok")
        for status, payload in [(503, {'error': {'message':'unknown'}}), (200, {}),
                                (200, {'id':None}), (200, {'id':True}), (200, {'id':{}}),
                                (200, {'id':'123', 'error': {'message':'contradictory'}}),
                                (400, {'id':'123', 'error':{'message':'contradictory'}}), (400, {'raw':'opaque response'}), (408, {'error':{'message':'timeout'}}), (302, {})]:
            with self.subTest(status=status, payload=payload):
                with patch("ocpf_post.providers.threads.request_json", return_value=(status, {}, payload)) as request:
                    with self.assertRaises(AmbiguousProviderEffect): ThreadsProvider().publish('hello')
                self.assertEqual(request.call_count, 1)

    def test_threads_explicit_rejection_and_empty_text_do_not_repeat_post(self) -> None:
        import_threads("tok")
        for status in (400, 401, 403, 429):
            with self.subTest(status=status), patch("ocpf_post.providers.threads.request_json",
                    return_value=(status, {}, {'error':{'message':'provider rejected'}})) as request:
                with self.assertRaises(ProviderRejected): ThreadsProvider().publish('hello')
                self.assertEqual(request.call_count, 1)
        with patch("ocpf_post.providers.threads.request_json") as request:
            with self.assertRaises(ProviderRejected): ThreadsProvider().publish('  ')
            request.assert_not_called()

    def test_threads_real_adapter_scheduler_records_before_readback_and_never_replays(self) -> None:
        from ocpf_post.scheduler import create_schedule, run_due, get_schedule
        from ocpf_post.state import iter_receipts
        from ocpf_post.providers.http import TransportError
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        for outcome in ('verified', 'unverified', 'ambiguous'):
            with self.subTest(outcome=outcome), patch.dict(os.environ,
                    {'OCPF_POST_STATE_DIR': str(Path(self.tmp.name)/outcome)}):
                provider = ThreadsProvider(); posts = []
                def bearer(url, *, method='GET', query=None):
                    if url.endswith('/me'):
                        return 200, {}, {'id':'25914281681582868', 'username':'tailwaggingwebdesigns'}
                    if method == 'POST':
                        self.assertTrue(url.endswith('/me/threads'))
                        self.assertEqual(query['auto_publish_text'], 'true')
                        posts.append(query['text'])
                        if outcome == 'ambiguous': raise TransportError('timeout')
                        return 200, {}, {'id':'123'}
                    provisional = list(iter_receipts())[-1]
                    self.assertEqual(provisional['status'], 'published_unverified')
                    self.assertEqual(provisional['post_id'], '123')
                    if outcome == 'unverified': return 403, {}, {'error':{'message':'unavailable'}}
                    return 200, {}, {'id':'123', 'text':posts[0], 'permalink':'https://www.threads.net/@factory/post/abc'}
                with patch.object(provider, '_bearer', side_effect=bearer), patch('ocpf_post.providers.threads.time.sleep'):
                    schedule = create_schedule(campaign='OCPF-002', provider='threads',
                        at=(now+timedelta(minutes=2)).isoformat(), now=now, provider_factory=lambda _:provider)
                    self.assertEqual(posts, [])
                    run_due(now=now+timedelta(minutes=3), provider_factory=lambda _:provider)
                    expected = {'verified':'published_verified', 'unverified':'published_unverified', 'ambiguous':'ambiguous_effect'}[outcome]
                    self.assertEqual(get_schedule(schedule['schedule_id'])['status'], expected)
                    self.assertEqual(list(iter_receipts())[-1]['status'], expected)
                    self.assertEqual(run_due(now=now+timedelta(minutes=4), provider_factory=lambda _:provider), [])
                    self.assertEqual(len(posts), 1)

    def test_linkedin_account_derives_person_urn_from_userinfo(self) -> None:
        import_linkedin("tok")
        with patch(
            "ocpf_post.providers.linkedin.request_json",
            return_value=(200, {}, {"sub": "abc123", "name": "John"}),
        ):
            account = LinkedInProvider().account()
        self.assertEqual(account.account_id, "urn:li:person:abc123")
        self.assertEqual(account.name, "John")

    def test_linkedin_recorded_read_permissions_are_purpose_and_actor_specific(self) -> None:
        write_private_json(
            provider_token_file("linkedin"),
            {
                "access_token": "tok",
                "scope": "openid profile email w_member_social r_member_social",
            },
        )
        provider = LinkedInProvider()
        member_posts = provider.read_permission("posts", "urn:li:person:abc123")
        member_comments = provider.read_permission("comments", "urn:li:person:abc123")
        org_posts = provider.read_permission("posts", "urn:li:organization:123")
        org_comments = provider.read_permission("comments", "urn:li:organization:123")

        self.assertEqual(member_posts["status"], "granted")
        self.assertEqual(member_posts["required_scope"], "r_member_social")
        self.assertEqual(member_comments["status"], "missing")
        self.assertEqual(member_comments["required_scope"], "r_member_social_feed")
        self.assertEqual(org_posts["status"], "missing")
        self.assertEqual(org_posts["required_scope"], "r_organization_social")
        self.assertEqual(org_comments["status"], "missing")
        self.assertEqual(org_comments["required_scope"], "r_organization_social_feed")
        self.assertTrue(member_posts["scope_recorded"])

        write_private_json(provider_token_file("linkedin"), {"access_token": "tok"})
        self.assertEqual(
            LinkedInProvider().read_permission("posts", "urn:li:person:abc123")["status"],
            "unknown",
        )

    def test_linkedin_database_identity_uses_active_token_introspection_when_openid_is_absent(self) -> None:
        import_linkedin(
            "tok",
            client_id="client-id",
            client_secret="client-secret",
        )
        write_private_json(
            provider_settings_file("linkedin"),
            {
                "version": "202608",
                "client_id": "client-id",
                "person_urn": "urn:li:person:abc123",
                "verification_status": "verified",
                "imported_from": "ocpf_supabase",
            },
        )
        responses = [
            (403, {}, {"message": "insufficient_scope"}),
            (
                200,
                {},
                {
                    "active": True,
                    "status": "active",
                    "client_id": "client-id",
                    "scope": "w_member_social",
                },
            ),
        ]
        with patch("ocpf_post.providers.linkedin.request_json", side_effect=responses):
            account = LinkedInProvider().account()
        self.assertEqual(account.account_id, "urn:li:person:abc123")

    def test_linkedin_live_userinfo_must_match_database_person_urn(self) -> None:
        import_linkedin("tok")
        write_private_json(
            provider_settings_file("linkedin"),
            {"version": "202608", "person_urn": "urn:li:person:other"},
        )
        with patch(
            "ocpf_post.providers.linkedin.request_json",
            return_value=(200, {}, {"sub": "abc123", "name": "John"}),
        ):
            with self.assertRaisesRegex(Exception, "identity mismatch"):
                LinkedInProvider().account()

    def test_linkedin_publish_uses_restli_id(self) -> None:
        import_linkedin("tok")
        responses = [
            (200, {}, {"sub": "abc123", "name": "John"}),
            (201, {"x-restli-id": "urn:li:share:999"}, {}),
        ]
        with patch("ocpf_post.providers.linkedin.request_json", side_effect=responses):
            result = LinkedInProvider().publish("hello")
        self.assertEqual(result["id"], "urn:li:share:999")

    def test_import_files_are_private_on_posix(self) -> None:
        import_threads("tok")
        mode = Path(self.tmp.name, "threads-token.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
