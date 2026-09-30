from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import (
    account_profiles,
    alert_delivery,
    bounded_http,
    business_outcomes,
    engagement,
    local_store,
    outcome_connectors,
)
from ocpf_post.providers.linkedin import (
    LinkedInProvider,
    USERINFO_URL,
    POSTS_URL,
)
from ocpf_post.state import append_receipt, provider_token_file, read_json, write_private_json

UTC = timezone.utc
NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)
MEMBER = "urn:li:person:Member123"
ORG = "urn:li:organization:12345"
ROOT_POST = "urn:li:share:9000000000000000001"
INCOMING = "urn:li:comment:(urn:li:share:9000000000000000001,7000000000000000001)"
OUTGOING = "urn:li:comment:(urn:li:share:9000000000000000001,7000000000000000002)"


class RuntimeFollowthroughTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {
            "OCPF_POST_CONFIG_DIR": str(self.root / "config"),
            "OCPF_POST_STATE_DIR": str(self.root / "state"),
        }, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def _linkedin_provider(self):
        credential_dir = self.root / "linkedin-page"
        write_private_json(credential_dir / "token.json", {
            "access_token": "page-token",
            "person_urn": MEMBER,
            "scope": "r_organization_social w_organization_social r_organization_social_feed w_organization_social_feed",
        })
        calls = []

        def transport(url, **kwargs):
            calls.append((url, kwargs))
            if url == USERINFO_URL:
                return 200, {}, {"sub": "Member123", "name": "Owner"}
            if url == POSTS_URL and kwargs.get("query", {}).get("q") == "author":
                self.assertEqual(kwargs["query"]["author"], ORG)
                self.assertEqual(kwargs["headers"]["X-RestLi-Method"], "FINDER")
                return 200, {}, {
                    "elements": [],
                    "paging": {"start": 0, "count": 1, "total": 0, "links": []},
                }
            if url == POSTS_URL and kwargs.get("method") == "POST":
                self.assertEqual(kwargs["json_body"]["author"], ORG)
                return 201, {"x-restli-id": ROOT_POST}, {}
            if url == POSTS_URL + "/" + __import__("urllib.parse").parse.quote(ROOT_POST, safe=""):
                return 200, {}, {"id": ROOT_POST, "author": ORG, "commentary": "Published copy"}
            if "/comments/7000000000000000001" in url:
                return 200, {}, {
                    "id": "7000000000000000001", "object": ROOT_POST,
                    "actor": "urn:li:person:Visitor", "message": {"text": "How does this work?"},
                }
            if "/comments/7000000000000000002" in url:
                return 200, {}, {
                    "id": "7000000000000000002", "object": ROOT_POST,
                    "parentComment": INCOMING, "actor": ORG,
                    "message": {"text": "It keeps the evidence bound to the exact effect."},
                }
            if "/comments" in url and kwargs.get("method") == "POST":
                body = kwargs["json_body"]
                self.assertEqual(body["actor"], ORG)
                self.assertEqual(body["parentComment"], INCOMING)
                self.assertEqual(body["object"], ROOT_POST)
                return 201, {"x-resourceidentity-urn": OUTGOING}, {
                    "commentUrn": OUTGOING, "id": "7000000000000000002",
                    "object": ROOT_POST, "parentComment": INCOMING,
                    "actor": ORG, "message": {"text": body["message"]["text"]},
                }
            if "/comments" in url:
                return 200, {}, {
                    "elements": [{
                        "id": "7000000000000000001", "object": ROOT_POST,
                        "actor": "urn:li:person:Visitor",
                        "message": {"text": "How does this work?"},
                    }],
                    "paging": {"start": 0, "count": 100, "total": 1, "links": []},
                }
            raise AssertionError("Unexpected LinkedIn request " + url)

        provider = LinkedInProvider(credential_dir=credential_dir, actor_urn=ORG)
        return provider, calls, transport

    def test_linkedin_page_actor_nested_collection_and_explicit_reply(self):
        self.assertEqual(account_profiles.key("linkedin", ORG), "linkedin:" + ORG)
        self.assertEqual(
            account_profiles.key("linkedin", "urn:li:organizationBrand:987"),
            "linkedin:urn:li:organizationBrand:987",
        )
        with self.assertRaises(ValueError):
            account_profiles.key("linkedin", "12345")

        provider, calls, transport = self._linkedin_provider()
        with patch("ocpf_post.providers.linkedin.request_json", side_effect=transport):
            account = provider.account()
            self.assertEqual(account.account_id, ORG)
            created = provider.publish("Published copy")
            self.assertEqual(created["id"], ROOT_POST)
            self.assertTrue(provider.verify_post(ROOT_POST, "Published copy"))

            rows, poll, more = engagement.fetch_page(
                provider, account, {}, {
                    ROOT_POST: {
                        "campaign": "TEST-LI", "published_at": engagement.stamp(NOW - timedelta(hours=2)),
                        "conversation_root_id": ROOT_POST, "depth": 0,
                    }
                }, NOW,
            )
            self.assertFalse(more)
            self.assertEqual(poll["root_count"], 1)
            context = engagement.normalise("linkedin", rows[0])
            self.assertEqual(context["parent_post_id"], ROOT_POST)
            self.assertEqual(context["post_id"], INCOMING)

            append_receipt({
                "campaign": "TEST-LI", "provider": "linkedin", "account_id": ORG,
                "post_id": ROOT_POST, "status": "published_verified",
                "readback_verified": True, "text_sha256": "a" * 64,
                "recorded_at": engagement.stamp(NOW - timedelta(hours=2)),
            })
            identity = engagement.digest(["linkedin", ORG, INCOMING])
            local_store.write(engagement.path(), {
                "schema_version": 1,
                "polls": {},
                "inbox": {
                    identity: {
                        "id": identity, "provider": "linkedin", "account_id": ORG,
                        "campaign": "TEST-LI", "context": context, "status": "pending",
                        "first_seen_at": engagement.stamp(NOW), "last_seen_at": engagement.stamp(NOW),
                        "conversation_root_id": ROOT_POST, "conversation_depth": 0,
                    }
                },
            })
            drafted = engagement.draft(
                identity, "It keeps the evidence bound to the exact effect.", now=NOW
            )
            with patch("ocpf_post.providers.for_account", return_value=provider):
                sent = engagement.send(
                    identity, expected_sha256=drafted["review_sha256"],
                    live=True, now=NOW + timedelta(minutes=1),
                )
            self.assertEqual(sent["result"], "published_verified")
            self.assertEqual(sent["post_id"], OUTGOING)
            self.assertTrue(engagement.read()["inbox"][identity]["readback_verified"])
            # LinkedIn replies use the receipt-backed root URL rather than
            # pretending a comment URN is a standalone feed-update post.
            self.assertEqual(sent["url"], provider.post_url(account, ROOT_POST))
        self.assertTrue(calls)

    def _publication_map(self):
        return {
            ("CAMPAIGN-1", "x", "123", "456"): {
                "receipt": {
                    "campaign": "CAMPAIGN-1", "provider": "x", "account_id": "123",
                    "post_id": "456", "status": "published_verified", "readback_verified": True,
                    "text_sha256": "b" * 64,
                },
                "at": NOW - timedelta(days=1),
                "effective_verified": True,
                "verification_basis": "receipt",
            }
        }

    def test_linkedin_page_can_reuse_default_member_credential_without_copying_secret(self):
        value = {
            "schema_version": 1, "provider": "linkedin", "account_id": ORG,
            "label": "Shared Member Page",
            "bindings": [{"project": "oneclickpostfactory", "alias": "linkedin-shared-page"}],
            "policy": {
                "daily_target": 3, "window_start": "08:00", "window_end": "18:00",
                "development_max": 1, "commercial_min": 1, "minimum_spacing_minutes": 60,
            },
        }
        preview = account_profiles.register(value)
        account_profiles.register(value, apply=True, expected_sha256=preview["input_sha256"])
        default = {
            "access_token": "default-member-token",
            "person_urn": MEMBER,
            "scope": "r_organization_social w_organization_social",
        }
        write_private_json(provider_token_file("linkedin"), default)

        def transport(url, **kwargs):
            if url == USERINFO_URL:
                self.assertEqual(
                    kwargs.get("headers", {}).get("Authorization"),
                    "Bearer default-member-token",
                )
                return 200, {}, {"sub": "Member123", "name": "Owner"}
            if url == POSTS_URL and kwargs.get("query", {}).get("q") == "author":
                self.assertEqual(kwargs["query"]["author"], ORG)
                return 200, {}, {
                    "elements": [],
                    "paging": {"start": 0, "count": 1, "total": 0, "links": []},
                }
            raise AssertionError("Unexpected LinkedIn request " + url)

        with patch("ocpf_post.providers.linkedin.request_json", side_effect=transport):
            connected = account_profiles.connect("linkedin", ORG, reuse_default=True)
            self.assertEqual(connected["credential_source"], "provider_default")
            scoped = read_json(account_profiles.directory("linkedin", ORG) / "token.json")
            self.assertEqual(scoped, {"credential_source": "provider_default"})
            self.assertNotIn("default-member-token", json.dumps(scoped))
            self.assertEqual(read_json(provider_token_file("linkedin")), default)
            row = account_profiles.profile("linkedin", ORG)
            self.assertTrue(account_profiles.credential_present(row))
            enable = account_profiles.activation("linkedin", ORG)
            applied = account_profiles.activation(
                "linkedin", ORG, apply=True, expected_sha256=enable["review_sha256"]
            )
        self.assertEqual(applied["result"], "enabled")
        self.assertTrue(account_profiles.profile("linkedin", ORG)["enabled"])

    def test_linkedin_page_profile_register_connect_and_enable_preserves_actor(self):
        value = {
            "schema_version": 1, "provider": "linkedin", "account_id": ORG,
            "label": "Product Page",
            "bindings": [{"project": "oneclickpostfactory", "alias": "linkedin-product-page"}],
            "policy": {
                "daily_target": 3, "window_start": "08:00", "window_end": "18:00",
                "development_max": 1, "commercial_min": 1, "minimum_spacing_minutes": 60,
            },
        }
        preview = account_profiles.register(value)
        account_profiles.register(value, apply=True, expected_sha256=preview["input_sha256"])
        secret = self.root / "linkedin-page-credential.json"
        write_private_json(secret, {
            "access_token": "page-token", "person_urn": MEMBER,
            "scope": "r_organization_social_feed w_organization_social_feed rw_organization_admin",
            "client_id": "client-id", "client_secret": "client-secret",
            "expires_at": 4102444800,
        })
        _provider, _calls, transport = self._linkedin_provider()
        with patch("ocpf_post.providers.linkedin.request_json", side_effect=transport):
            connected = account_profiles.connect("linkedin", ORG, credential_file=secret)
            self.assertEqual(connected["account_id"], ORG)
            enable = account_profiles.activation("linkedin", ORG)
            self.assertFalse(enable["account"]["enabled"])
            applied = account_profiles.activation(
                "linkedin", ORG, apply=True, expected_sha256=enable["review_sha256"]
            )
        self.assertEqual(applied["result"], "enabled")
        stored = account_profiles.profiles()["linkedin:" + ORG]
        self.assertTrue(stored["enabled"])
        token = read_json(account_profiles.directory("linkedin", ORG) / "token.json")
        self.assertEqual(token["person_urn"], MEMBER)
        self.assertEqual(token["scope"], "r_organization_social_feed w_organization_social_feed rw_organization_admin")

    def test_outcome_connector_ingests_idempotently_and_cursor_after_commit(self):
        spec = {
            "schema_version": 1, "id": "analytics-main",
            "endpoint": "https://analytics.example.com/outcomes",
            "source": "analytics_main", "auth": "none", "max_pages": 2,
        }
        preview = outcome_connectors.register(spec)
        outcome_connectors.register(spec, apply=True, expected_sha256=preview["review_sha256"])
        activation = outcome_connectors.activation("analytics-main")
        outcome_connectors.activation(
            "analytics-main", apply=True, expected_sha256=activation["review_sha256"]
        )
        event = {
            "event_id": "sale-1", "campaign": "CAMPAIGN-1", "provider": "x",
            "account_id": "123", "post_id": "456",
            "occurred_at": engagement.stamp(NOW - timedelta(hours=1)),
            "event_type": "sale", "evidence_reference": "analytics:event:sale-1",
            "revenue_minor": 2500, "currency": "GBP",
        }
        responses = [{
            "schema_version": 1, "source": "analytics_main", "events": [event],
            "next_cursor": "cursor-1", "has_more": True,
        }, {
            "schema_version": 1, "source": "analytics_main", "events": [],
            "next_cursor": None, "has_more": False,
        }]
        urls = []
        def read(url, **kwargs):
            urls.append(url)
            return responses.pop(0)

        with patch("ocpf_post.business_outcomes.publications", return_value=self._publication_map()), \
             patch.object(outcome_connectors, "json_request", side_effect=read):
            result = outcome_connectors.sync(apply=True, now=NOW)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["results"][0]["event_count"], 1)
        self.assertIn("cursor=cursor-1", urls[1])
        report = business_outcomes.report()
        self.assertEqual(report["event_counts"]["sale"], 1)
        self.assertEqual(report["revenue_minor_by_currency"]["GBP"], 2500)

        # A replayed event is idempotent and cannot be reassigned.
        replay = {
            "schema_version": 1, "source": "analytics_main", "events": [event],
            "next_cursor": None, "has_more": False,
        }
        with patch("ocpf_post.business_outcomes.publications", return_value=self._publication_map()), \
             patch.object(outcome_connectors, "json_request", return_value=replay):
            again = outcome_connectors.sync(apply=True, now=NOW + timedelta(minutes=15))
        self.assertEqual(again["status"], "observed")
        self.assertEqual(business_outcomes.report()["event_counts"]["sale"], 1)

    def test_outcome_connector_preserves_committed_cursor_when_later_page_fails(self):
        spec = {
            "schema_version": 1, "id": "analytics-failing",
            "endpoint": "https://analytics.example.com/outcomes",
            "source": "analytics_failing", "auth": "none", "max_pages": 3,
        }
        preview = outcome_connectors.register(spec)
        outcome_connectors.register(spec, apply=True, expected_sha256=preview["review_sha256"])
        activation = outcome_connectors.activation("analytics-failing")
        outcome_connectors.activation(
            "analytics-failing", apply=True, expected_sha256=activation["review_sha256"]
        )
        event = {
            "event_id": "lead-1", "campaign": "CAMPAIGN-1", "provider": "x",
            "account_id": "123", "post_id": "456",
            "occurred_at": engagement.stamp(NOW - timedelta(hours=1)),
            "event_type": "enquiry", "evidence_reference": "analytics:event:lead-1",
            "revenue_minor": None, "currency": None,
        }
        calls = 0
        def read(url, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "schema_version": 1, "source": "analytics_failing", "events": [event],
                    "next_cursor": "cursor-kept", "has_more": True,
                }
            raise ValueError("simulated later-page failure")
        with patch("ocpf_post.business_outcomes.publications", return_value=self._publication_map()), \
             patch.object(outcome_connectors, "json_request", side_effect=read):
            result = outcome_connectors.sync(apply=True, now=NOW)
        self.assertEqual(result["status"], "attention")
        state = local_store.read(outcome_connectors.state_path())["connectors"]["analytics-failing"]
        self.assertEqual(state["cursor"], "cursor-kept")
        self.assertEqual(state["status"], "unavailable")
        self.assertEqual(business_outcomes.report()["event_counts"]["enquiry"], 1)

    def test_alert_http_refuses_private_resolution_before_connect(self):
        private = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch("ocpf_post.bounded_http.socket.getaddrinfo", return_value=private), \
             patch("ocpf_post.bounded_http.socket.create_connection",
                   side_effect=AssertionError("private target must never connect")):
            with self.assertRaises(ValueError):
                bounded_http.post_json("https://alerts.example.com/post-once", {"schema_version": 1})

    def test_alert_delivery_deduplicates_and_reports_resolution_without_prose(self):
        spec = {
            "schema_version": 1, "endpoint": "https://alerts.example.com/post-once",
            "auth": "none", "include_resolved": True, "minimum_interval_seconds": 0,
        }
        preview = alert_delivery.configure(spec)
        alert_delivery.configure(spec, apply=True, expected_sha256=preview["review_sha256"])
        activation = alert_delivery.activation()
        alert_delivery.activation(apply=True, expected_sha256=activation["review_sha256"])
        local_store.write(self.root / "state" / "operating-incidents.json", {
            "schema_version": 1, "observed_at": engagement.stamp(NOW),
            "incidents": {
                "collector:metrics": {
                    "code": "attention", "stage": "metrics",
                    "action": "PRIVATE OPERATOR PROSE MUST NOT LEAVE HOST",
                    "first_seen_at": engagement.stamp(NOW),
                    "last_seen_at": engagement.stamp(NOW),
                }
            },
        })
        payloads = []
        with patch.object(alert_delivery, "post_json", side_effect=lambda url, payload, **kw: (204, payloads.append(payload) or {})):
            first = alert_delivery.deliver(apply=True, now=NOW)
            second = alert_delivery.deliver(apply=True, now=NOW + timedelta(minutes=1))
            local_store.write(self.root / "state" / "operating-incidents.json", {
                "schema_version": 1, "observed_at": engagement.stamp(NOW + timedelta(minutes=2)),
                "incidents": {},
            })
            resolved = alert_delivery.deliver(apply=True, now=NOW + timedelta(minutes=2))
        self.assertEqual(first["new_count"], 1)
        self.assertEqual(second["status"], "idle")
        self.assertEqual(resolved["resolved_count"], 1)
        self.assertEqual(len(payloads), 2)
        encoded = json.dumps(payloads)
        self.assertNotIn("PRIVATE OPERATOR PROSE", encoded)
        self.assertEqual(payloads[0]["new_incidents"][0]["incident_id"], "collector:metrics")
        self.assertEqual(payloads[1]["resolved_incident_ids"], ["collector:metrics"])

    def test_alert_without_resolution_messages_forgets_resolved_then_realerts_recurrence(self):
        spec = {
            "schema_version": 1, "endpoint": "https://alerts.example.com/post-once",
            "auth": "none", "include_resolved": False, "minimum_interval_seconds": 0,
        }
        preview = alert_delivery.configure(spec)
        alert_delivery.configure(spec, apply=True, expected_sha256=preview["review_sha256"])
        activation = alert_delivery.activation()
        alert_delivery.activation(apply=True, expected_sha256=activation["review_sha256"])
        incident_path = self.root / "state" / "operating-incidents.json"
        def write_incidents(incidents, when):
            local_store.write(incident_path, {
                "schema_version": 1, "observed_at": engagement.stamp(when),
                "incidents": incidents,
            })
        incident = {"code": "attention", "stage": "metrics",
                    "first_seen_at": engagement.stamp(NOW), "last_seen_at": engagement.stamp(NOW)}
        payloads = []
        with patch.object(alert_delivery, "post_json", side_effect=lambda url, payload, **kw: (204, payloads.append(payload) or {})):
            write_incidents({"collector:metrics": incident}, NOW)
            first = alert_delivery.deliver(apply=True, now=NOW)
            write_incidents({}, NOW + timedelta(minutes=1))
            quiet = alert_delivery.deliver(apply=True, now=NOW + timedelta(minutes=1))
            write_incidents({"collector:metrics": {**incident, "last_seen_at": engagement.stamp(NOW + timedelta(minutes=2))}}, NOW + timedelta(minutes=2))
            again = alert_delivery.deliver(apply=True, now=NOW + timedelta(minutes=2))
        self.assertEqual(first["new_count"], 1)
        self.assertEqual(quiet["status"], "idle")
        self.assertEqual(again["new_count"], 1)
        self.assertEqual(len(payloads), 2)


if __name__ == "__main__":
    unittest.main()
