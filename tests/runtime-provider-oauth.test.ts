import assert from "node:assert/strict";
import test from "node:test";
import { digest } from "../src/common.ts";
import { runtime } from "./runtime-fixture.ts";
import { Response as RuntimeResponse } from "miniflare";

const origin = "https://publish.example";
async function seedOwner(db: D1Database, session = "owner-session") {
  const subject = "owner-subject";
  const workspace = "owner-workspace";
  const csrf = "owner-csrf";
  const sessionHash = await digest(session);
  await db
    .prepare("INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?)")
    .bind(subject, workspace, Date.now())
    .run();
  await db
    .prepare("INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (?,?,?,?,?)")
    .bind(sessionHash, workspace, subject, Date.now() + 3600000, csrf)
    .run();
  await db
    .prepare(
      "INSERT INTO owner_proofs(session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (?,?,?,?,?,?,?,?)",
    )
    .bind(
      sessionHash,
      "owner-proof",
      "https://accounts.google.com",
      "poststeward-test",
      await digest("owner@example.com"),
      1,
      Date.now(),
      "test",
    )
    .run();
  return { session, sessionHash, subject, workspace, csrf };
}
function ownerHeaders(owner: Awaited<ReturnType<typeof seedOwner>>) {
  return {
    Cookie: `__Host-session=${owner.session}`,
    Origin: origin,
    "Content-Type": "application/json",
    "X-CSRF-Token": owner.csrf,
  };
}
function base64url(bytes: ArrayBuffer) {
  return Buffer.from(bytes).toString("base64url");
}

test("real Workers/D1 X OAuth binds state to the owner session, verifies PKCE and stores no token in public status", async () => {
  let expectedChallenge = "";
  let tokenExchanges = 0;
  let identityReads = 0;
  const { mf, db } = await runtime(
    async (request) => {
      const url = new URL(request.url);
      if (url.hostname === "api.x.com" && url.pathname === "/2/oauth2/token") {
        tokenExchanges++;
        assert.equal(request.method, "POST");
        assert.ok(request.headers.get("authorization")?.startsWith("Basic "));
        const body = new URLSearchParams(await request.text());
        assert.equal(body.get("grant_type"), "authorization_code");
        assert.equal(body.get("code"), "provider-code");
        assert.equal(body.get("redirect_uri"), `${origin}/connections/oauth/x/callback`);
        assert.equal(body.get("client_id"), "x-client");
        const verifier = body.get("code_verifier")!;
        assert.equal(
          base64url(
            await crypto.subtle.digest(
              "SHA-256",
              new TextEncoder().encode(verifier),
            ),
          ),
          expectedChallenge,
        );
        return RuntimeResponse.json({
          access_token: "x-access-token-private-001",
          refresh_token: "x-refresh-token-private-001",
          expires_in: 3600,
          scope: "tweet.read tweet.write users.read offline.access",
        });
      }
      if (url.hostname === "api.x.com" && url.pathname === "/2/users/me") {
        identityReads++;
        assert.equal(
          request.headers.get("authorization"),
          "Bearer x-access-token-private-001",
        );
        return RuntimeResponse.json({
          data: { id: "stable-x-user", username: "stable_owner" },
        });
      }
      throw new Error(`Unexpected outbound provider endpoint ${url.href}`);
    },
    {
      OIDC_ISSUER: "https://accounts.google.com",
      X_OAUTH_CLIENT_ID: "x-client",
      X_OAUTH_CLIENT_SECRET: "x-client-secret",
    },
    100,
  );
  try {
    const owner = await seedOwner(db);
    const start = await mf.dispatchFetch(
      `${origin}/api/connections/oauth/x/start`,
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: JSON.stringify({ alias: "primary" }),
      },
    );
    assert.equal(start.status, 200, await start.clone().text());
    const started: any = await start.json();
    const authorization = new URL(started.authorizationUrl);
    assert.equal(authorization.origin, "https://x.com");
    assert.equal(authorization.searchParams.get("response_type"), "code");
    assert.equal(authorization.searchParams.get("code_challenge_method"), "S256");
    assert.equal(authorization.searchParams.get("redirect_uri"), `${origin}/connections/oauth/x/callback`);
    assert.equal(authorization.searchParams.get("scope"), "tweet.read tweet.write users.read offline.access");
    expectedChallenge = authorization.searchParams.get("code_challenge")!;
    const state = authorization.searchParams.get("state")!;
    assert.ok(state);
    assert.match(start.headers.get("set-cookie") || "", /__Host-provider-oauth=/);
    assert.equal(
      (await db.prepare("SELECT count(*) AS n FROM provider_oauth_states").first<any>())?.n,
      1,
    );

    const callback = await mf.dispatchFetch(
      `${origin}/connections/oauth/x/callback?state=${encodeURIComponent(state)}&code=provider-code`,
      {
        redirect: "manual",
        headers: {
          Cookie: `__Host-session=${owner.session}; __Host-provider-oauth=${state}`,
        },
      },
    );
    assert.equal(callback.status, 302, await callback.clone().text());
    assert.equal(callback.headers.get("location"), "/pilot?connected=x");
    assert.equal(tokenExchanges, 1);
    assert.equal(identityReads, 1);
    assert.equal(
      (await db.prepare("SELECT count(*) AS n FROM provider_oauth_states").first<any>())?.n,
      0,
    );

    const accounts = await mf.dispatchFetch(
      `${origin}/api/operations/accounts_list`,
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: "{}",
      },
    );
    assert.equal(accounts.status, 200);
    const accountList = (await accounts.json()) as any[];
    assert.equal(accountList.length, 1);
    assert.equal(accountList[0].identity.id, "stable-x-user");
    assert.deepEqual(accountList[0].capabilities, {
      oauth: true,
      refresh: true,
      readback: true,
    });
    assert.doesNotMatch(
      JSON.stringify(accountList),
      /x-access-token-private|x-refresh-token-private|secret/,
    );

    const status = await mf.dispatchFetch(
      `${origin}/api/connections/oauth/status`,
      { headers: { Cookie: `__Host-session=${owner.session}` } },
    );
    assert.equal(status.status, 200);
    const value: any = await status.json();
    assert.equal(value.providers.x.available, true);
    assert.equal(value.connections[0].alias, "primary");
    assert.equal(value.connections[0].strategy, "refresh_token");
    assert.doesNotMatch(
      JSON.stringify(value),
      /x-access-token-private|x-refresh-token-private|x-client-secret|secret/,
    );

    const replay = await mf.dispatchFetch(
      `${origin}/connections/oauth/x/callback?state=${encodeURIComponent(state)}&code=provider-code`,
      {
        redirect: "manual",
        headers: {
          Cookie: `__Host-session=${owner.session}; __Host-provider-oauth=${state}`,
        },
      },
    );
    assert.equal(replay.status, 400);
    assert.equal(tokenExchanges, 1);
    assert.equal(identityReads, 1);
  } finally {
    await mf.dispose();
  }
});

test("provider OAuth start requires a current owner completion proof and a configured provider app", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
  }, 100);
  try {
    const owner = await seedOwner(db);
    const missingApp = await mf.dispatchFetch(
      `${origin}/api/connections/oauth/x/start`,
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: JSON.stringify({ alias: "primary" }),
      },
    );
    assert.equal(missingApp.status, 503);
    const missingValue: any = await missingApp.json();
    assert.equal(missingValue.error.code, "OAUTH_NOT_CONFIGURED");

    await db
      .prepare("DELETE FROM owner_proofs WHERE session_hash=?")
      .bind(owner.sessionHash)
      .run();
    const noProof = await mf.dispatchFetch(
      `${origin}/api/connections/oauth/x/start`,
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: JSON.stringify({ alias: "primary" }),
      },
    );
    assert.equal(noProof.status, 409);
    const noProofValue: any = await noProof.json();
    assert.equal(noProofValue.error.code, "OWNER_SIGNIN_REQUIRED");
  } finally {
    await mf.dispose();
  }
});

test("provider denial consumes the one-use state without exchanging or storing credentials", async () => {
  let outbound = 0;
  const { mf, db } = await runtime(
    async () => {
      outbound++;
      throw new Error("No provider request expected after denial");
    },
    {
      OIDC_ISSUER: "https://accounts.google.com",
      X_OAUTH_CLIENT_ID: "x-client",
      X_OAUTH_CLIENT_SECRET: "x-client-secret",
    },
    100,
  );
  try {
    const owner = await seedOwner(db);
    const start = await mf.dispatchFetch(
      `${origin}/api/connections/oauth/x/start`,
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: JSON.stringify({ alias: "primary" }),
      },
    );
    const started: any = await start.json();
    const state = new URL(started.authorizationUrl).searchParams.get("state")!;
    const denied = await mf.dispatchFetch(
      `${origin}/connections/oauth/x/callback?state=${encodeURIComponent(state)}&error=access_denied`,
      {
        redirect: "manual",
        headers: {
          Cookie: `__Host-session=${owner.session}; __Host-provider-oauth=${state}`,
        },
      },
    );
    assert.equal(denied.status, 302);
    assert.equal(denied.headers.get("location"), "/pilot?connection=denied");
    assert.equal(outbound, 0);
    assert.equal(
      (await db.prepare("SELECT count(*) AS n FROM provider_oauth_states").first<any>())?.n,
      0,
    );
    const accounts = await mf.dispatchFetch(
      `${origin}/api/operations/accounts_list`,
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: "{}",
      },
    );
    assert.deepEqual(await accounts.json(), []);
  } finally {
    await mf.dispose();
  }
});

test("agent Bearer tokens cannot inspect or initiate provider OAuth", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
    X_OAUTH_CLIENT_ID: "x-client",
    X_OAUTH_CLIENT_SECRET: "x-client-secret",
  }, 100);
  try {
    await db
      .prepare("INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?)")
      .bind("owner-subject", "owner-workspace", Date.now())
      .run();
    const bearer = "agent-provider-oauth-token";
    await db
      .prepare("INSERT INTO grants VALUES (?,?,?,?,?,NULL)")
      .bind(
        await digest(bearer),
        "owner-workspace",
        "agent",
        '["read","connections"]',
        Date.now() + 3600000,
      )
      .run();
    const status = await mf.dispatchFetch(
      `${origin}/api/connections/oauth/status`,
      { headers: { Authorization: `Bearer ${bearer}` } },
    );
    assert.equal(status.status, 403);
    const start = await mf.dispatchFetch(
      `${origin}/api/connections/oauth/x/start`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${bearer}`,
          Origin: origin,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ alias: "agent" }),
      },
    );
    assert.equal(start.status, 403);
  } finally {
    await mf.dispose();
  }
});
