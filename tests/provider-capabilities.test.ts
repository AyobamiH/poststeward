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

  const configured = oauthConfiguration(h.env);
  assert.deepEqual(configured.x.requiredScopes, xScopes);
  assert.deepEqual(configured.threads.optionalScopes, ["threads_manage_insights"]);
  assert.deepEqual(configured.linkedin.optionalScopes, []);
  assert.equal(
    configured.linkedin.capabilities.readback.state,
    "external_approval_required",
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
