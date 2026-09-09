import assert from "node:assert/strict";
import test from "node:test";
import { unseal } from "../src/crypto.ts";
import { ProviderOAuthConnections } from "../src/provider-oauth.ts";
import { harness, owner } from "./helpers.ts";
import type { Account } from "../src/types.ts";
import type { Credential } from "../src/providers.ts";

const xScopes = ["tweet.read", "tweet.write", "users.read", "offline.access"];

function configuredHarness() {
  const h = harness();
  h.env.X_OAUTH_CLIENT_ID = "x-client";
  h.env.X_OAUTH_CLIENT_SECRET = "x-secret-value";
  h.env.THREADS_OAUTH_CLIENT_ID = "threads-client";
  h.env.THREADS_OAUTH_CLIENT_SECRET = "threads-secret-value";
  h.env.LINKEDIN_OAUTH_CLIENT_ID = "linkedin-client";
  h.env.LINKEDIN_OAUTH_CLIENT_SECRET = "linkedin-secret-value";
  return h;
}

test("OAuth connection encrypts access and refresh material and exposes only capability metadata", async () => {
  const h = configuredHarness();
  const oauth = new ProviderOAuthConnections(h.store, h.env, h.provider, h.now);
  const result: any = await oauth.connect(owner, {
    alias: "social",
    token: {
      provider: "x",
      accessToken: "access-token-private-001",
      refreshToken: "refresh-token-private-001",
      expiresAt: h.now() + 3600000,
      scopes: xScopes,
      obtainedAt: h.now(),
    },
  });
  assert.deepEqual(result.account.capabilities, {
    oauth: true,
    refresh: true,
    readback: true,
  });
  const account = h.store.get<Account>("account:social")!;
  const meta: any = h.store.get("oauth:social");
  assert.doesNotMatch(account.secret, /access-token-private/);
  assert.doesNotMatch(meta.secret, /refresh-token-private/);
  const credential = await unseal<Credential>(
    account.secret,
    h.env.ENCRYPTION_KEY,
    owner.workspace + ":social",
  );
  assert.equal(credential.accessToken, "access-token-private-001");
  const refresh = await unseal<{ refreshToken: string }>(
    meta.secret,
    h.env.ENCRYPTION_KEY,
    owner.workspace + ":oauth:social",
  );
  assert.equal(refresh.refreshToken, "refresh-token-private-001");
  assert.doesNotMatch(JSON.stringify(oauth.status()), /access-token-private|refresh-token-private|secret/);
});

test("X token rotation keeps captured routing stable when identity and capabilities are unchanged", async () => {
  const h = configuredHarness();
  let refreshRequests = 0;
  const oauth = new ProviderOAuthConnections(
    h.store,
    h.env,
    h.provider,
    h.now,
    (async (url, init) => {
      refreshRequests++;
      assert.equal(String(url), "https://api.x.com/2/oauth2/token");
      assert.equal(init?.method, "POST");
      const body = new URLSearchParams(init!.body as URLSearchParams);
      assert.equal(body.get("grant_type"), "refresh_token");
      assert.equal(body.get("refresh_token"), "refresh-token-private-001");
      return Response.json({
        access_token: "access-token-private-002",
        refresh_token: "refresh-token-private-002",
        expires_in: 7200,
        scope: xScopes.join(" "),
      });
    }) as typeof fetch,
  );
  await oauth.connect(owner, {
    alias: "social",
    token: {
      provider: "x",
      accessToken: "access-token-private-001",
      refreshToken: "refresh-token-private-001",
      expiresAt: h.now() + 600000,
      scopes: xScopes,
      obtainedAt: h.now(),
    },
  });
  const before = h.store.get<Account>("account:social")!;
  h.advance(420000);
  await oauth.refreshDue();
  const after = h.store.get<Account>("account:social")!;
  assert.equal(refreshRequests, 1);
  assert.equal(after.version, before.version);
  assert.equal(after.identity.id, before.identity.id);
  const credential = await unseal<Credential>(
    after.secret,
    h.env.ENCRYPTION_KEY,
    owner.workspace + ":social",
  );
  assert.equal(credential.accessToken, "access-token-private-002");
  assert.equal(oauth.status()[0].status, "healthy");
});

test("refreshed credentials that resolve to a different stable identity deactivate and rebind-block the account", async () => {
  const h = configuredHarness();
  const oauth = new ProviderOAuthConnections(
    h.store,
    h.env,
    h.provider,
    h.now,
    (async () =>
      Response.json({
        access_token: "access-token-private-002",
        refresh_token: "refresh-token-private-002",
        expires_in: 7200,
        scope: xScopes.join(" "),
      })) as typeof fetch,
  );
  await oauth.connect(owner, {
    alias: "social",
    token: {
      provider: "x",
      accessToken: "access-token-private-001",
      refreshToken: "refresh-token-private-001",
      expiresAt: h.now() + 600000,
      scopes: xScopes,
      obtainedAt: h.now(),
    },
  });
  const before = h.store.get<Account>("account:social")!;
  h.provider.identity = async () => ({ id: "different-user", username: "different" });
  h.advance(420000);
  await oauth.refreshDue();
  const after = h.store.get<Account>("account:social")!;
  assert.equal(after.active, false);
  assert.equal(after.version, before.version + 1);
  assert.equal(oauth.status()[0].status, "identity_drift");
});

test("LinkedIn without an issued refresh token requests reauthorisation, wakes once at expiry and then deactivates", async () => {
  const h = configuredHarness();
  let network = 0;
  const oauth = new ProviderOAuthConnections(
    h.store,
    h.env,
    h.provider,
    h.now,
    (async () => {
      network++;
      throw new Error("No refresh call expected");
    }) as typeof fetch,
  );
  const expiresAt = h.now() + 60 * 86400000;
  await oauth.connect(owner, {
    alias: "linkedin",
    token: {
      provider: "linkedin",
      accessToken: "linkedin-access-private-001",
      expiresAt,
      scopes: ["openid", "profile", "w_member_social"],
      obtainedAt: h.now(),
    },
  });
  assert.equal(oauth.status()[0].strategy, "reauthorize");
  assert.equal(oauth.status()[0].needsReauthorization, false);
  h.advance(53 * 86400000);
  await oauth.refreshDue();
  assert.equal(network, 0);
  assert.equal(oauth.status()[0].status, "reauthorization_required");
  assert.equal(oauth.status()[0].needsReauthorization, true);
  assert.equal(h.store.get<Account>("account:linkedin")!.active, true);
  assert.equal(oauth.nextWake(), expiresAt);
  h.advance(7 * 86400000);
  await oauth.refreshDue();
  assert.equal(network, 0);
  assert.equal(oauth.status()[0].status, "expired");
  assert.equal(h.store.get<Account>("account:linkedin")!.active, false);
  assert.equal(oauth.nextWake(), undefined);
});

test("disconnect scrubs locally stored OAuth credentials even when provider revocation is unavailable", async () => {
  const h = configuredHarness();
  const oauth = new ProviderOAuthConnections(
    h.store,
    h.env,
    h.provider,
    h.now,
    (async () => {
      throw new Error("provider unavailable");
    }) as typeof fetch,
  );
  await oauth.connect(owner, {
    alias: "social",
    token: {
      provider: "x",
      accessToken: "access-token-private-001",
      refreshToken: "refresh-token-private-001",
      expiresAt: h.now() + 3600000,
      scopes: xScopes,
      obtainedAt: h.now(),
    },
  });
  const account = h.store.get<Account>("account:social")!;
  account.active = false;
  account.version++;
  h.store.put("account:social", account);
  await oauth.afterDisconnect("social");
  assert.equal(h.store.get("oauth:social"), undefined);
  const tombstone = await unseal<Credential>(
    h.store.get<Account>("account:social")!.secret,
    h.env.ENCRYPTION_KEY,
    owner.workspace + ":social",
  );
  assert.deepEqual(tombstone, { accessToken: "disconnected", expiresAt: 0 });
});
