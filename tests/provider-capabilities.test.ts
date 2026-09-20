import assert from "node:assert/strict";
import test from "node:test";
import {
  negotiatedProviderCapabilities,
  providerApplicationCapabilities,
} from "../src/provider-capabilities.ts";
import {
  ProviderOAuthConnections,
  oauthConfiguration,
} from "../src/provider-oauth.ts";
import { harness, owner } from "./helpers.ts";

const xScopes = ["tweet.read", "tweet.write", "users.read", "offline.access"];

test("provider application capabilities separate configuration from connection and external approval", () => {
  const x = providerApplicationCapabilities("x", true, {
    linkedinMemberReadbackApproved: false,
  });
  assert.equal(x.publish.state, "connection_required");
  assert.deepEqual(x.refresh.requiredScopes, ["offline.access"]);
  assert.deepEqual(x.metrics.requiredScopes, ["tweet.read"]);

  const linkedin = providerApplicationCapabilities("linkedin", true, {
    linkedinMemberReadbackApproved: false,
  });
  assert.equal(linkedin.publish.state, "connection_required");
  assert.equal(linkedin.readback.state, "external_approval_required");
  assert.equal(
    linkedin.readback.reason,
    "r_member_social_closed_to_new_requests",
  );
});

test("LinkedIn organization actors use organization publish/read scopes rather than member scopes", () => {
  const app = providerApplicationCapabilities("linkedin", true, {
    linkedinMemberReadbackApproved: false,
    linkedinOrganizationActor: true,
  });
  assert.deepEqual(app.publish.requiredScopes, ["w_organization_social"]);
  assert.deepEqual(app.readback.requiredScopes, ["r_organization_social"]);
  assert.equal(app.readback.state, "connection_required");

  const negotiated = negotiatedProviderCapabilities(
    "linkedin",
    ["w_organization_social", "r_organization_social"],
    {
      refreshable: false,
      scopeEvidence: "provider",
      identityVerified: true,
      linkedinOrganizationActor: true,
    },
  );
  assert.equal(negotiated.publish.state, "available");
  assert.equal(negotiated.readback.state, "available");
  assert.equal(negotiated.metrics.reason, "organization_analytics_not_enabled");
});

test("LinkedIn Community Management credentials never imply member OpenID authority", () => {
  const h = harness();
  h.env.LINKEDIN_ORGANIZATION_OAUTH_CLIENT_ID = "linkedin-organization-client";
  h.env.LINKEDIN_ORGANIZATION_OAUTH_CLIENT_SECRET =
    "linkedin-organization-secret-value";
  const configured = oauthConfiguration(h.env);
  assert.equal(configured.linkedin.available, true);
  assert.equal(configured.linkedin.memberAvailable, false);
  assert.equal(configured.linkedin.organizationAvailable, true);
  assert.deepEqual(configured.linkedin.organizationConnection, {
    model: "poststeward_managed",
    customerAppRequired: false,
    customerRequirement: "eligible_page_role",
    actorSelection: "page_id_or_urn",
  });
  assert.equal(configured.linkedin.capabilities.identity.state, "unavailable");
  assert.deepEqual(configured.linkedin.organizationScopes, [
    "w_organization_social",
    "r_organization_social",
  ]);
  assert.ok(configured.linkedin.organizationCapabilities);
  assert.deepEqual(
    configured.linkedin.organizationCapabilities.identity.requiredScopes,
    ["r_organization_social"],
  );
});

test("negotiation never invents optional LinkedIn readback or Threads insights from an omitted scope response", () => {
  const linkedin = negotiatedProviderCapabilities(
    "linkedin",
    ["openid", "profile", "w_member_social"],
    {
      refreshable: false,
      scopeEvidence: "request_assumed",
      identityVerified: true,
    },
  );
  assert.equal(linkedin.publish.state, "unknown");
  assert.equal(linkedin.readback.state, "external_approval_required");

  const threads = negotiatedProviderCapabilities(
    "threads",
    ["threads_basic", "threads_content_publish"],
    {
      refreshable: true,
      scopeEvidence: "request_assumed",
      identityVerified: true,
    },
  );
  assert.equal(threads.publish.state, "unknown");
  assert.equal(threads.readback.state, "unknown");
  assert.equal(threads.metrics.state, "unavailable");
});

test("OAuth status exposes negotiated evidence while account compatibility flags remain bounded", async () => {
  const h = harness();
  h.env.X_OAUTH_CLIENT_ID = "x-client";
  h.env.X_OAUTH_CLIENT_SECRET = "x-secret-value";
  h.env.THREADS_OAUTH_CLIENT_ID = "threads-client";
  h.env.THREADS_OAUTH_CLIENT_SECRET = "threads-secret-value";
  h.env.LINKEDIN_OAUTH_CLIENT_ID = "linkedin-client";
  h.env.LINKEDIN_OAUTH_CLIENT_SECRET = "linkedin-secret-value";
  h.env.LINKEDIN_ORGANIZATION_OAUTH_CLIENT_ID = "linkedin-organization-client";
  h.env.LINKEDIN_ORGANIZATION_OAUTH_CLIENT_SECRET =
    "linkedin-organization-secret-value";

  const configured = oauthConfiguration(h.env);
  assert.deepEqual(configured.x.requiredScopes, xScopes);
  assert.deepEqual(configured.threads.optionalScopes, [
    "threads_manage_insights",
  ]);
  assert.deepEqual(configured.linkedin.optionalScopes, []);
  assert.equal(
    configured.linkedin.capabilities.readback.state,
    "external_approval_required",
  );
  assert.deepEqual(configured.linkedin.organizationScopes, [
    "w_organization_social",
    "r_organization_social",
  ]);
  assert.equal(configured.linkedin.memberAvailable, true);
  assert.equal(configured.linkedin.organizationAvailable, true);
  assert.ok(configured.linkedin.organizationCapabilities);
  assert.equal(
    configured.linkedin.organizationCapabilities.readback.state,
    "connection_required",
  );

  const oauth = new ProviderOAuthConnections(h.store, h.env, h.provider, h.now);
  const result: any = await oauth.connect(owner, {
    alias: "linkedin",
    token: {
      provider: "linkedin",
      accessToken: "linkedin-access-private-001",
      expiresAt: h.now() + 3600000,
      scopes: ["openid", "profile", "w_member_social"],
      scopeEvidence: "provider",
      obtainedAt: h.now(),
    },
  });
  assert.deepEqual(result.account.capabilities, {
    oauth: true,
    refresh: false,
    readback: false,
  });
  assert.equal(
    result.oauth.capabilities.readback.state,
    "external_approval_required",
  );
  assert.equal(result.oauth.scopeEvidence, "provider");
});
