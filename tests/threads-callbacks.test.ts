import assert from "node:assert/strict";
import test from "node:test";
import { digest } from "../src/common.ts";
import { parseThreadsSignedRequest } from "../src/threads-callbacks.ts";
import { runtime } from "./runtime-fixture.ts";

const origin = "https://publish.example";
const threadsSecret = "threads-client-secret";

async function seedOwner(db: D1Database, session = "owner-session") {
  const subject = "owner-subject";
  const workspace = "owner-workspace";
  const csrf = "owner-csrf";
  const sessionHash = await digest(session);
  await db
    .prepare(
      "INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?)",
    )
    .bind(subject, workspace, Date.now())
    .run();
  await db
    .prepare(
      "INSERT INTO workspace_registry(workspace) VALUES (?) ON CONFLICT(workspace) DO NOTHING",
    )
    .bind(workspace)
    .run();
  await db
    .prepare(
      "INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (?,?,?,?,?)",
    )
    .bind(sessionHash, workspace, subject, Date.now() + 3600000, csrf)
    .run();
  return { session, subject, workspace, csrf };
}

function ownerHeaders(owner: Awaited<ReturnType<typeof seedOwner>>) {
  return {
    Cookie: `__Host-session=${owner.session}`,
    Origin: origin,
    "Content-Type": "application/json",
    "X-CSRF-Token": owner.csrf,
  };
}

function base64url(value: Uint8Array | ArrayBuffer) {
  return Buffer.from(
    value instanceof Uint8Array ? value : new Uint8Array(value),
  ).toString("base64url");
}

async function signedRequest(userId: string, secret = threadsSecret) {
  const payload = base64url(
    new TextEncoder().encode(
      JSON.stringify({
        algorithm: "HMAC-SHA256",
        issued_at: Math.floor(Date.now() / 1000),
        user_id: userId,
      }),
    ),
  );
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(payload),
  );
  return base64url(signature) + "." + payload;
}

test("Threads signed request verifies HMAC and rejects tampering", async () => {
  const valid = await signedRequest("threads-user-123");
  const parsed = await parseThreadsSignedRequest(valid, threadsSecret);
  assert.equal(parsed.user_id, "threads-user-123");
  await assert.rejects(
    parseThreadsSignedRequest(valid + "x", threadsSecret),
    /signed request/,
  );
});

test("Threads uninstall and delete callbacks revoke and scrub the exact connected identity", async () => {
  let identityReads = 0;
  const { mf, db } = await runtime(
    async (request) => {
      const url = new URL(request.url);
      if (
        url.hostname === "graph.threads.net" &&
        url.pathname === "/v1.0/me"
      ) {
        identityReads++;
        assert.match(
          request.headers.get("authorization") || "",
          /^Bearer threads-access-/,
        );
        return Response.json({
          id: "threads-user-123",
          username: "thread_owner",
        });
      }
      throw new Error("Unexpected outbound endpoint " + url.href);
    },
    {
      OIDC_ISSUER: "https://accounts.google.com",
      THREADS_OAUTH_CLIENT_ID: "threads-client",
      THREADS_OAUTH_CLIENT_SECRET: threadsSecret,
    },
  );

  try {
    const owner = await seedOwner(db);
    const connect = async (token: string) => {
      const response = await mf.dispatchFetch(
        `${origin}/api/connections/import`,
        {
          method: "POST",
          headers: ownerHeaders(owner),
          body: JSON.stringify({
            alias: "primary_threads",
            provider: "threads",
            accessToken: token,
          }),
        },
      );
      assert.equal(response.status, 200, await response.clone().text());
    };

    await connect("threads-access-token-private-001");
    assert.equal(identityReads, 1);
    assert.equal(
      (
        await db
          .prepare(
            "SELECT count(*) AS n FROM provider_identity_bindings WHERE provider='threads' AND identity_id='threads-user-123'",
          )
          .first<any>()
      )?.n,
      1,
    );

    const uninstall = await mf.dispatchFetch(
      `${origin}/connections/oauth/threads/uninstall`,
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          signed_request: await signedRequest("threads-user-123"),
        }).toString(),
      },
    );
    assert.equal(uninstall.status, 200, await uninstall.clone().text());
    assert.deepEqual(await uninstall.json(), { received: true, revoked: 1 });
    assert.equal(
      (
        await db
          .prepare(
            "SELECT count(*) AS n FROM provider_identity_bindings WHERE provider='threads' AND identity_id='threads-user-123'",
          )
          .first<any>()
      )?.n,
      0,
    );

    let accounts = await mf.dispatchFetch(
      `${origin}/api/operations/accounts_list`,
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: "{}",
      },
    );
    let rows = (await accounts.json()) as any[];
    assert.equal(rows[0].active, false);
    assert.equal(rows[0].identity.id, "threads-user-123");

    await connect("threads-access-token-private-002");
    assert.equal(identityReads, 2);

    const deletion = await mf.dispatchFetch(
      `${origin}/connections/oauth/threads/delete`,
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          signed_request: await signedRequest("threads-user-123"),
        }).toString(),
      },
    );
    assert.equal(deletion.status, 200, await deletion.clone().text());
    const deleted: any = await deletion.json();
    assert.match(deleted.confirmation_code, /^[a-f0-9]{32}$/);
    assert.equal(
      deleted.url,
      `${origin}/connections/oauth/threads/delete/status?code=${deleted.confirmation_code}`,
    );

    accounts = await mf.dispatchFetch(
      `${origin}/api/operations/accounts_list`,
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: "{}",
      },
    );
    rows = (await accounts.json()) as any[];
    assert.equal(rows[0].active, false);
    assert.match(rows[0].identity.id, /^deleted:[a-f0-9]{24}$/);
    assert.equal(rows[0].identity.username, "deleted");

    const status = await mf.dispatchFetch(deleted.url);
    assert.equal(status.status, 200, await status.clone().text());
    const statusValue: any = await status.json();
    assert.equal(statusValue.provider, "threads");
    assert.equal(statusValue.state, "completed");

    assert.equal(
      (
        await db
          .prepare(
            "SELECT matched_accounts,state FROM provider_deletion_requests WHERE confirmation_code=?",
          )
          .bind(deleted.confirmation_code)
          .first<any>()
      )?.matched_accounts,
      1,
    );
  } finally {
    await mf.dispose();
  }
});

test("Threads callback rejects a forged signed request without touching provider state", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
    THREADS_OAUTH_CLIENT_ID: "threads-client",
    THREADS_OAUTH_CLIENT_SECRET: threadsSecret,
  });
  try {
    await db
      .prepare(
        "INSERT INTO workspace_registry(workspace) VALUES ('owner-workspace')",
      )
      .run();
    const response = await mf.dispatchFetch(
      `${origin}/connections/oauth/threads/delete`,
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          signed_request: await signedRequest(
            "threads-user-123",
            "wrong-client-secret",
          ),
        }).toString(),
      },
    );
    assert.equal(response.status, 400);
    const value: any = await response.json();
    assert.equal(value.error.code, "THREADS_SIGNED_REQUEST_INVALID");
    assert.equal(
      (
        await db
          .prepare("SELECT count(*) AS n FROM provider_deletion_requests")
          .first<any>()
      )?.n,
      0,
    );
  } finally {
    await mf.dispose();
  }
});
