import assert from "node:assert/strict";
import test from "node:test";
import { unseal } from "../src/crypto.ts";
import {
  ProviderOAuthConnections,
  oauthConfiguration,
} from "../src/provider-oauth.ts";
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
  h.env.LINKEDIN_ORGANIZATION_OAUTH_CLIENT_ID = "linkedin-organization-client";
  h.env.LINKEDIN_ORGANIZATION_OAUTH_CLIENT_SECRET =
    "linkedin-organization-secret-value";
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
  assert.doesNotMatch(
    JSON.stringify(oauth.status()),
    /access-token-private|refresh-token-private|secret/,
  );
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
  h.provider.identity = async () => ({
    id: "different-user",
    username: "different",
  });
  h.advance(420000);
  await oauth.refreshDue();
  const after = h.store.get<Account>("account:social")!;
  assert.equal(after.active, false);
  assert.equal(after.version, before.version + 1);
  assert.equal(oauth.status()[0].status, "identity_drift");
});

test("LinkedIn organization connection binds the exact reviewed page actor and organization scopes", async () => {
  const h = configuredHarness();
  const actorUrn = "urn:li:organization:146607525";
  let observedActor: string | undefined;
  h.provider.identity = async (_provider, _credential, actor) => {
    observedActor = actor;
    return { id: actor!, username: actor! };
  };
  const oauth = new ProviderOAuthConnections(h.store, h.env, h.provider, h.now);
  const result: any = await oauth.connect(owner, {
    alias: "poststeward-page",
    actorUrn,
    token: {
      provider: "linkedin",
      accessToken: "linkedin-organization-access-001",
      expiresAt: h.now() + 3600000,
      scopes: ["w_organization_social", "r_organization_social"],
      scopeEvidence: "provider",
      obtainedAt: h.now(),
    },
  });
  assert.equal(observedActor, actorUrn);
  assert.equal(result.account.identity.id, actorUrn);
  assert.equal(result.oauth.actorUrn, actorUrn);
  assert.equal(result.oauth.capabilities.publish.state, "available");
  assert.equal(result.oauth.capabilities.readback.state, "available");
  assert.deepEqual(result.account.capabilities, {
    oauth: true,
    refresh: false,
    readback: true,
  });
});

test("LinkedIn organization connection normalizes a numeric Page ID before identity binding", async () => {
  const h = configuredHarness();
  let observedActor: string | undefined;
  h.provider.identity = async (_provider, _credential, actor) => {
    observedActor = actor;
    return { id: actor!, username: actor! };
  };
  const oauth = new ProviderOAuthConnections(h.store, h.env, h.provider, h.now);
  const result: any = await oauth.connect(owner, {
    alias: "linkedin-page-id",
    actorUrn: "146607525",
    token: {
      provider: "linkedin",
      accessToken: "linkedin-organization-access-002",
      expiresAt: h.now() + 3600000,
      scopes: ["w_organization_social", "r_organization_social"],
      scopeEvidence: "provider",
      obtainedAt: h.now(),
    },
  });
  assert.equal(observedActor, "urn:li:organization:146607525");
  assert.equal(result.account.identity.id, "urn:li:organization:146607525");
  assert.equal(result.oauth.actorUrn, "urn:li:organization:146607525");
});

test("LinkedIn organization connection rejects member-only scope grants", async () => {
  const h = configuredHarness();
  const oauth = new ProviderOAuthConnections(h.store, h.env, h.provider, h.now);
  await assert.rejects(
    oauth.connect(owner, {
      alias: "poststeward-page",
      actorUrn: "urn:li:organization:146607525",
      token: {
        provider: "linkedin",
        accessToken: "linkedin-member-only-access-001",
        expiresAt: h.now() + 3600000,
        scopes: ["openid", "profile", "w_member_social"],
        scopeEvidence: "provider",
        obtainedAt: h.now(),
      },
    }),
    /Provider did not grant every required permission/,
  );
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

function latch<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function testToken(now: number, suffix = "001") {
  return {
    provider: "x" as const,
    accessToken: "access-token-private-" + suffix,
    refreshToken: "refresh-token-private-" + suffix,
    expiresAt: now + 600000,
    scopes: xScopes,
    obtainedAt: now,
  };
}
for (const outcome of ["success", "failure", "identity_drift"] as const) {
  for (const change of ["disconnect", "reconnect"] as const) {
    test(`late refresh ${outcome} cannot overwrite ${change}`, async () => {
      const h = configuredHarness();
      const started = latch<void>();
      const response = latch<Response>();
      const oauth = new ProviderOAuthConnections(
        h.store,
        h.env,
        h.provider,
        h.now,
        (async () => {
          started.resolve();
          return response.promise;
        }) as typeof fetch,
      );
      await oauth.connect(owner, {
        alias: "social",
        token: testToken(h.now()),
      });
      h.advance(420000);
      const pending = oauth.refreshDue();
      await started.promise;
      if (change === "disconnect") {
        const account = h.store.get<Account>("account:social")!;
        h.store.put("account:social", {
          ...account,
          active: false,
          version: account.version + 1,
        });
        h.store.delete("oauth:social");
      } else {
        await oauth.connect(owner, {
          alias: "social",
          token: testToken(h.now(), "replacement"),
        });
      }
      const expectedAccount = h.store.get("account:social");
      const expectedMeta = h.store.get("oauth:social");
      if (outcome === "identity_drift")
        h.provider.identity = async () => ({ id: "other", username: "other" });
      response.resolve(
        outcome === "failure"
          ? new Response(null, { status: 503 })
          : Response.json({
              access_token: "late-refreshed-access",
              refresh_token: "late-refreshed-secret",
              expires_in: 7200,
              scope: xScopes.join(" "),
            }),
      );
      await pending;
      assert.deepEqual(h.store.get("account:social"), expectedAccount);
      assert.deepEqual(h.store.get("oauth:social"), expectedMeta);
    });
  }
}

test("late provider revocation cleanup cannot erase a replacement connection", async () => {
  const h = configuredHarness();
  const started = latch<void>();
  const response = latch<Response>();
  const oauth = new ProviderOAuthConnections(
    h.store,
    h.env,
    h.provider,
    h.now,
    (async () => {
      started.resolve();
      return response.promise;
    }) as typeof fetch,
  );
  await oauth.connect(owner, { alias: "social", token: testToken(h.now()) });
  const account = h.store.get<Account>("account:social")!;
  h.store.put("account:social", {
    ...account,
    active: false,
    version: account.version + 1,
  });
  const pending = oauth.afterDisconnect("social");
  await started.promise;
  await oauth.connect(owner, {
    alias: "social",
    token: testToken(h.now(), "replacement"),
  });
  const expectedAccount = h.store.get("account:social");
  const expectedMeta = h.store.get("oauth:social");
  response.resolve(Response.json({ revoked: true }));
  await pending;
  assert.deepEqual(h.store.get("account:social"), expectedAccount);
  assert.deepEqual(h.store.get("oauth:social"), expectedMeta);
});

test("overlapping refresh callers send one provider request", async () => {
  const h = configuredHarness();
  const started = latch<void>();
  const response = latch<Response>();
  let calls = 0;
  const http = (async () => {
    calls++;
    started.resolve();
    return response.promise;
  }) as typeof fetch;
  const oauth = new ProviderOAuthConnections(
    h.store,
    h.env,
    h.provider,
    h.now,
    http,
  );
  await oauth.connect(owner, { alias: "social", token: testToken(h.now()) });
  h.advance(420000);
  const pending = oauth.refreshDue();
  await started.promise;
  const another = new ProviderOAuthConnections(
    h.store,
    h.env,
    h.provider,
    h.now,
    http,
  );
  await another.refreshDue();
  assert.equal(calls, 1);
  response.resolve(
    Response.json({
      access_token: "refreshed-access-token",
      expires_in: 7200,
      scope: xScopes.join(" "),
    }),
  );
  await pending;
  assert.equal(oauth.status()[0].status, "healthy");
});

test("connection verification cannot overwrite a disconnect while identity is pending", async () => {
  const h = configuredHarness();
  const oauth = new ProviderOAuthConnections(h.store, h.env, h.provider, h.now);
  await oauth.connect(owner, { alias: "social", token: testToken(h.now()) });
  const started = latch<void>();
  const identity = latch<{ id: string; username: string }>();
  h.provider.identity = async () => {
    started.resolve();
    return identity.promise;
  };
  const pending = oauth.connect(owner, {
    alias: "social",
    token: testToken(h.now(), "replacement"),
  });
  const rejection = assert.rejects(pending, /Connection changed/);
  await started.promise;
  const account = h.store.get<Account>("account:social")!;
  const disconnected = {
    ...account,
    active: false,
    version: account.version + 1,
  };
  h.store.put("account:social", disconnected);
  identity.resolve(account.identity);
  await rejection;
  assert.deepEqual(h.store.get("account:social"), disconnected);
});

test("manual replacement removes the previous OAuth refresh grant", async () => {
  const h = configuredHarness();
  const oauth = new ProviderOAuthConnections(h.store, h.env, h.provider, h.now);
  await oauth.connect(owner, { alias: "social", token: testToken(h.now()) });
  await h.engine.connect(owner, {
    alias: "social",
    provider: "x",
    accessToken: "manual-replacement-token",
    funding: "customer_app",
  });
  assert.equal(h.store.get("oauth:social"), undefined);
  assert.equal(oauth.nextWake(), undefined);
  assert.equal(h.store.get<Account>("account:social")!.active, true);
});

test("manual identity verification cannot overwrite an intervening disconnect", async () => {
  const h = configuredHarness();
  const oauth = new ProviderOAuthConnections(h.store, h.env, h.provider, h.now);
  await oauth.connect(owner, { alias: "social", token: testToken(h.now()) });
  const started = latch<void>();
  const identity = latch<{ id: string; username: string }>();
  h.provider.identity = async () => {
    started.resolve();
    return identity.promise;
  };
  const pending = h.engine.connect(owner, {
    alias: "social",
    provider: "x",
    accessToken: "manual-replacement-token",
    funding: "customer_app",
  });
  const rejection = assert.rejects(pending, /Connection changed/);
  await started.promise;
  const account = h.store.get<Account>("account:social")!;
  const disconnected = {
    ...account,
    active: false,
    version: account.version + 1,
  };
  h.store.put("account:social", disconnected);
  identity.resolve(account.identity);
  await rejection;
  assert.deepEqual(h.store.get("account:social"), disconnected);
});

for (const insights of [false, true]) {
  test(
    "Threads publishing connects with optional insights scope: " + insights,
    async () => {
      const h = configuredHarness();
      assert.ok(
        oauthConfiguration(h.env).threads.scopes.includes(
          "threads_manage_insights",
        ),
      );
      const oauth = new ProviderOAuthConnections(
        h.store,
        h.env,
        h.provider,
        h.now,
      );
      const result: any = await oauth.connect(owner, {
        alias: "threads",
        token: {
          provider: "threads",
          accessToken: "threads-access-private-001",
          expiresAt: h.now() + 3600000,
          obtainedAt: h.now(),
          scopes: [
            "threads_basic",
            "threads_content_publish",
            ...(insights ? ["threads_manage_insights"] : []),
          ],
        },
      });
      assert.equal(result.account.active, true);
      assert.equal(
        result.account.capabilities.metrics,
        insights ? true : undefined,
      );
    },
  );
}
