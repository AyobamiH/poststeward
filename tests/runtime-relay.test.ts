import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { Response as RuntimeResponse } from "miniflare";
import { digest } from "../src/common.ts";
import { runtime } from "./runtime-fixture.ts";

const origin = "https://publish.example";
const sha = (value: string) =>
  createHash("sha256").update(value, "utf8").digest("hex");

async function seed(db: D1Database) {
  const workspace = "relay-workspace";
  const owner = "relay-owner";
  const session = "relay-owner-session";
  const csrf = "relay-csrf";
  const sessionHash = await digest(session);
  await db
    .prepare(
      "INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?)",
    )
    .bind(owner, workspace, Date.now())
    .run();
  await db
    .prepare(
      "INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (?,?,?,?,?)",
    )
    .bind(sessionHash, workspace, owner, Date.now() + 3_600_000, csrf)
    .run();
  await db
    .prepare(
      "INSERT INTO owner_proofs(session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (?,?,?,?,?,?,?,?)",
    )
    .bind(
      sessionHash,
      "relay-owner-proof",
      "https://accounts.google.com",
      "poststeward-test",
      await digest("owner@example.com"),
      1,
      Date.now(),
      "test",
    )
    .run();
  const agent = "relay-agent-token";
  await db
    .prepare("INSERT INTO grants VALUES (?,?,?,?,?,NULL)")
    .bind(
      await digest(agent),
      workspace,
      "relay-agent",
      JSON.stringify([
        "read",
        "connections",
        "publish",
        "schedule",
        "campaign:write",
      ]),
      Date.now() + 3_600_000,
    )
    .run();
  return {
    workspace,
    ownerHeaders: {
      Cookie: `__Host-session=${session}`,
      Origin: origin,
      "X-CSRF-Token": csrf,
      "Content-Type": "application/json",
    },
    agentHeaders: {
      Authorization: "Bearer " + agent,
      Origin: origin,
      "Content-Type": "application/json",
    },
  };
}

async function pairLocal(
  mf: any,
  ownerHeaders: Record<string, string>,
  installationId = "33333333-3333-4333-8333-333333333333",
) {
  const startResponse = await mf.dispatchFetch(
    origin + "/api/runtime/pairing/start",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        installationId,
        label: "Relay runtime",
        platform: "Linux",
        runtimeVersion: "0.28.29",
      }),
    },
  );
  const start: any = await startResponse.json();
  await mf.dispatchFetch(origin + "/api/runtime/pairing/approve", {
    method: "POST",
    headers: ownerHeaders,
    body: JSON.stringify({
      pairingId: start.pairingId,
      userCode: start.userCode,
    }),
  });
  const claim = await mf.dispatchFetch(
    origin + "/api/runtime/pairing/status?pairing_id=" + start.pairingId,
    { headers: { Authorization: "Bearer " + start.pollToken } },
  );
  const paired: any = await claim.json();
  const preview = await mf.dispatchFetch(
    origin + "/api/runtime/executor/preview",
    {
      method: "POST",
      headers: ownerHeaders,
      body: JSON.stringify({
        mode: "local",
        installationId,
        reason: "relay acceptance",
      }),
    },
  );
  const review: any = await preview.json();
  assert.equal(review.status, "preview", JSON.stringify(review));
  const apply = await mf.dispatchFetch(origin + "/api/runtime/executor/apply", {
    method: "POST",
    headers: ownerHeaders,
    body: JSON.stringify({
      mode: "local",
      installationId,
      reason: "relay acceptance",
      expectedSha256: review.reviewSha256,
    }),
  });
  const applied: any = await apply.json();
  assert.equal(apply.status, 200, JSON.stringify(applied));
  return {
    runtimeToken: paired.runtimeToken as string,
    generation: applied.executor.authorityGeneration as number,
    installationId,
  };
}

test("runtime relay reuses the D1 provider-effect fence and hosted consequence paths are fenced", async () => {
  let writes = 0;
  let reads = 0;
  const { mf, db } = await runtime(
    async (request) => {
      const url = new URL(request.url);
      if (url.hostname === "api.x.com" && url.pathname === "/2/users/me") {
        reads++;
        return RuntimeResponse.json({
          data: { id: "12345", username: "relay_owner" },
        });
      }
      if (
        url.hostname === "api.x.com" &&
        url.pathname === "/2/tweets" &&
        request.method === "POST"
      ) {
        writes++;
        assert.deepEqual(await request.json(), { text: "Exact runtime copy." });
        return RuntimeResponse.json({ data: { id: "post-relay-1" } });
      }
      if (
        url.hostname === "api.x.com" &&
        url.pathname === "/2/tweets/post-relay-1"
      ) {
        reads++;
        return RuntimeResponse.json({
          data: {
            id: "post-relay-1",
            author_id: "12345",
            text: "Exact runtime copy.",
          },
        });
      }
      throw new Error("Unexpected provider endpoint " + url.href);
    },
    {
      OIDC_ISSUER: "https://accounts.google.com",
    },
    100,
  );
  try {
    const auth = await seed(db);

    const connected = await mf.dispatchFetch(
      origin + "/api/connections/import",
      {
        method: "POST",
        headers: auth.agentHeaders,
        body: JSON.stringify({
          alias: "relay_x",
          provider: "x",
          accessToken: "runtime-relay-test-token",
          funding: "customer_app",
        }),
      },
    );
    assert.equal(connected.status, 200, await connected.clone().text());

    const local = await pairLocal(mf, auth.ownerHeaders);
    const runtimeHeaders = {
      Authorization: "Bearer " + local.runtimeToken,
      "Content-Type": "application/json",
    };

    const account = await mf.dispatchFetch(origin + "/api/runtime/relay", {
      method: "POST",
      headers: runtimeHeaders,
      body: JSON.stringify({
        action: "account",
        provider: "x",
        accountId: "12345",
      }),
    });
    assert.equal(account.status, 200, await account.clone().text());
    assert.equal(((await account.json()) as any).account.identity.id, "12345");

    const relayInput = {
      action: "publish",
      authorityGeneration: local.generation,
      provider: "x",
      accountId: "12345",
      effectId: "effect-runtime-0001",
      campaign: "RUNTIME-001",
      text: "Exact runtime copy.",
      textDigest: sha("Exact runtime copy."),
    };

    const first = await mf.dispatchFetch(origin + "/api/runtime/relay", {
      method: "POST",
      headers: runtimeHeaders,
      body: JSON.stringify(relayInput),
    });
    assert.equal(first.status, 200, await first.clone().text());
    assert.equal(((await first.json()) as any).id, "post-relay-1");
    assert.equal(writes, 1);

    const replay = await mf.dispatchFetch(origin + "/api/runtime/relay", {
      method: "POST",
      headers: runtimeHeaders,
      body: JSON.stringify(relayInput),
    });
    assert.equal(replay.status, 200, await replay.clone().text());
    assert.equal(((await replay.json()) as any).id, "post-relay-1");
    assert.equal(writes, 1);

    const changed = await mf.dispatchFetch(origin + "/api/runtime/relay", {
      method: "POST",
      headers: runtimeHeaders,
      body: JSON.stringify({
        ...relayInput,
        text: "Changed runtime copy.",
        textDigest: sha("Changed runtime copy."),
      }),
    });
    assert.equal(changed.status, 409);
    assert.equal(
      ((await changed.json()) as any).error.code,
      "EXTERNAL_EFFECT_FENCE_MISMATCH",
    );
    assert.equal(writes, 1);

    const verify = await mf.dispatchFetch(origin + "/api/runtime/relay", {
      method: "POST",
      headers: runtimeHeaders,
      body: JSON.stringify({
        ...relayInput,
        action: "verify",
        postId: "post-relay-1",
      }),
    });
    assert.equal(verify.status, 200, await verify.clone().text());
    assert.equal(((await verify.json()) as any).verified, true);
    assert.equal(writes, 1);
    assert.ok(reads >= 2);

    const effect = await db
      .prepare(
        "SELECT status,post_id FROM external_effects WHERE workspace=? AND fingerprint=?",
      )
      .bind(auth.workspace, "runtime:effect-runtime-0001")
      .first<any>();
    assert.equal(effect.status, "verified");
    assert.equal(effect.post_id, "post-relay-1");

    const hosted = await mf.dispatchFetch(
      origin + "/api/operations/schedule_create",
      {
        method: "POST",
        headers: auth.agentHeaders,
        body: JSON.stringify({
          campaign: "does-not-matter",
          at: "2026-10-01T12:00:00Z",
          timezone: "UTC",
          idempotencyKey: "hosted-block-0001",
        }),
      },
    );
    assert.equal(hosted.status, 409);
    assert.equal(
      ((await hosted.json()) as any).error.code,
      "RUNTIME_LOCAL_OPERATION_REQUIRED",
    );
    assert.equal(writes, 1);
  } finally {
    await mf.dispose();
  }
});

test("stale runtime generation is rejected before provider I/O", async () => {
  let writes = 0;
  const { mf, db } = await runtime(
    async (request) => {
      const url = new URL(request.url);
      if (url.pathname === "/2/users/me")
        return RuntimeResponse.json({
          data: { id: "12345", username: "relay_owner" },
        });
      if (request.method === "POST") {
        writes++;
        return RuntimeResponse.json({ data: { id: "should-not-exist" } });
      }
      throw new Error("Unexpected provider read " + request.url);
    },
    { OIDC_ISSUER: "https://accounts.google.com" },
    100,
  );
  try {
    const auth = await seed(db);
    const connected = await mf.dispatchFetch(
      origin + "/api/connections/import",
      {
        method: "POST",
        headers: auth.agentHeaders,
        body: JSON.stringify({
          alias: "relay_x",
          provider: "x",
          accessToken: "runtime-relay-test-token",
          funding: "customer_app",
        }),
      },
    );
    assert.equal(connected.status, 200, await connected.clone().text());
    const local = await pairLocal(mf, auth.ownerHeaders);

    const response = await mf.dispatchFetch(origin + "/api/runtime/relay", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + local.runtimeToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        action: "publish",
        authorityGeneration: local.generation - 1,
        provider: "x",
        accountId: "12345",
        effectId: "effect-runtime-stale",
        campaign: "RUNTIME-002",
        text: "Must not publish.",
        textDigest: sha("Must not publish."),
      }),
    });
    assert.equal(response.status, 409);
    assert.equal(
      ((await response.json()) as any).error.code,
      "RUNTIME_EXECUTOR_FENCED",
    );
    assert.equal(writes, 0);
  } finally {
    await mf.dispose();
  }
});
