import assert from "node:assert/strict";
import test from "node:test";
import { digest } from "../src/common.ts";
import { runtime } from "./runtime-fixture.ts";

const origin = "https://publish.example";

async function seedOwner(db: D1Database) {
  const session = "runtime-owner-session";
  const sessionHash = await digest(session);
  const workspace = "runtime-workspace";
  const actor = "runtime-owner";
  const csrf = "runtime-owner-csrf";
  await db
    .prepare("INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?)")
    .bind(actor, workspace, Date.now())
    .run();
  await db
    .prepare(
      "INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (?,?,?,?,?)",
    )
    .bind(sessionHash, workspace, actor, Date.now() + 3_600_000, csrf)
    .run();
  await db
    .prepare(
      "INSERT INTO owner_proofs(session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (?,?,?,?,?,?,?,?)",
    )
    .bind(
      sessionHash,
      "runtime-owner-proof",
      "https://accounts.google.com",
      "poststeward-test",
      await digest("owner@example.com"),
      1,
      Date.now(),
      "test",
    )
    .run();
  return {
    workspace,
    actor,
    headers: {
      Cookie: `__Host-session=${session}`,
      Origin: origin,
      "X-CSRF-Token": csrf,
      "Content-Type": "application/json",
    },
  };
}

test("runtime pairing is short-lived, owner-approved and yields one installation token", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
  }, 100);
  try {
    const owner = await seedOwner(db);
    const installationId = "11111111-1111-4111-8111-111111111111";
    const started = await mf.dispatchFetch(origin + "/api/runtime/pairing/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        installationId,
        label: "Beta laptop",
        platform: "Linux x86_64",
        runtimeVersion: "0.28.29",
        sourceRevision: "a".repeat(40),
      }),
    });
    assert.equal(started.status, 201, await started.clone().text());
    const start: any = await started.json();
    assert.equal(start.userCode.length, 8);
    assert.ok(start.pollToken);
    assert.doesNotMatch(JSON.stringify(start), /runtimeToken/);
    assert.match(start.verificationUrl, /runtime_pairing=/);

    const pending = await mf.dispatchFetch(
      origin + "/api/runtime/pairing/status?pairing_id=" + encodeURIComponent(start.pairingId),
      { headers: { Authorization: "Bearer " + start.pollToken } },
    );
    assert.equal(pending.status, 202);
    assert.equal((await pending.json() as any).status, "pending");

    const inspected = await mf.dispatchFetch(origin + "/api/runtime/pairing/inspect", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({ pairingId: start.pairingId, userCode: start.userCode }),
    });
    assert.equal(inspected.status, 200, await inspected.clone().text());
    const detail: any = await inspected.json();
    assert.equal(detail.installationId, installationId);
    assert.equal(detail.label, "Beta laptop");

    const approved = await mf.dispatchFetch(origin + "/api/runtime/pairing/approve", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({ pairingId: start.pairingId, userCode: start.userCode }),
    });
    assert.equal(approved.status, 200, await approved.clone().text());

    const claimed = await mf.dispatchFetch(
      origin + "/api/runtime/pairing/status?pairing_id=" + encodeURIComponent(start.pairingId),
      { headers: { Authorization: "Bearer " + start.pollToken } },
    );
    assert.equal(claimed.status, 200, await claimed.clone().text());
    const paired: any = await claimed.json();
    assert.equal(paired.status, "paired");
    assert.equal(paired.workspace, owner.workspace);
    assert.equal(paired.installationId, installationId);
    assert.ok(paired.runtimeToken);
    assert.equal(paired.executor.executorMode, "hosted");
    assert.equal(paired.executor.authorityGeneration, 1);

    const replay = await mf.dispatchFetch(
      origin + "/api/runtime/pairing/status?pairing_id=" + encodeURIComponent(start.pairingId),
      { headers: { Authorization: "Bearer " + start.pollToken } },
    );
    assert.equal(replay.status, 409);

    const bindings = await mf.dispatchFetch(origin + "/api/runtime/bindings", {
      headers: { Authorization: "Bearer " + paired.runtimeToken },
    });
    assert.equal(bindings.status, 200, await bindings.clone().text());
    const bindingValue: any = await bindings.json();
    assert.equal(bindingValue.workspace, owner.workspace);
    assert.deepEqual(bindingValue.accounts, []);
  } finally {
    await mf.dispose();
  }
});

test("executor transition is review-bound and stale generations cannot renew the lease", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
  }, 100);
  try {
    const owner = await seedOwner(db);
    const installationId = "22222222-2222-4222-8222-222222222222";
    const pairing = await mf.dispatchFetch(origin + "/api/runtime/pairing/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        installationId,
        label: "Local executor",
        platform: "Linux",
        runtimeVersion: "0.28.29",
      }),
    });
    const start: any = await pairing.json();
    await mf.dispatchFetch(origin + "/api/runtime/pairing/approve", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({ pairingId: start.pairingId, userCode: start.userCode }),
    });
    const claimed = await mf.dispatchFetch(
      origin + "/api/runtime/pairing/status?pairing_id=" + start.pairingId,
      { headers: { Authorization: "Bearer " + start.pollToken } },
    );
    const paired: any = await claimed.json();

    const preview = await mf.dispatchFetch(origin + "/api/runtime/executor/preview", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({
        mode: "local",
        installationId,
        reason: "activate reviewed local runtime",
      }),
    });
    assert.equal(preview.status, 200, await preview.clone().text());
    const review: any = await preview.json();
    assert.equal(review.status, "preview");
    assert.deepEqual(review.blockers, []);
    assert.match(review.reviewSha256, /^[a-f0-9]{64}$/);

    const wrong = await mf.dispatchFetch(origin + "/api/runtime/executor/apply", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({
        mode: "local",
        installationId,
        reason: "activate reviewed local runtime",
        expectedSha256: "0".repeat(64),
      }),
    });
    assert.equal(wrong.status, 409);

    const applied = await mf.dispatchFetch(origin + "/api/runtime/executor/apply", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({
        mode: "local",
        installationId,
        reason: "activate reviewed local runtime",
        expectedSha256: review.reviewSha256,
      }),
    });
    assert.equal(applied.status, 200, await applied.clone().text());
    const active: any = await applied.json();
    assert.equal(active.executor.executorMode, "local");
    assert.equal(active.executor.activeInstallationId, installationId);
    assert.equal(active.executor.authorityGeneration, 2);
    assert.ok(active.executor.leaseExpiresAt > Date.now());

    const stale = await mf.dispatchFetch(origin + "/api/runtime/heartbeat", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + paired.runtimeToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ authorityGeneration: 1 }),
    });
    assert.equal(stale.status, 409);
    assert.equal((await stale.json() as any).error.code, "RUNTIME_EXECUTOR_FENCED");

    const renewed = await mf.dispatchFetch(origin + "/api/runtime/heartbeat", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + paired.runtimeToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ authorityGeneration: 2 }),
    });
    assert.equal(renewed.status, 200, await renewed.clone().text());
    assert.equal((await renewed.json() as any).authorityGeneration, 2);

    const hostedPreview = await mf.dispatchFetch(origin + "/api/runtime/executor/preview", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({ mode: "hosted", reason: "return to hosted executor" }),
    });
    const hostedReview: any = await hostedPreview.json();
    assert.equal(hostedReview.status, "preview");
    const hosted = await mf.dispatchFetch(origin + "/api/runtime/executor/apply", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({
        mode: "hosted",
        reason: "return to hosted executor",
        expectedSha256: hostedReview.reviewSha256,
      }),
    });
    assert.equal(hosted.status, 200, await hosted.clone().text());
    assert.equal((await hosted.json() as any).executor.authorityGeneration, 3);

    const revoked = await mf.dispatchFetch(origin + "/api/runtime/installations/revoke", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({ installationId }),
    });
    assert.equal(revoked.status, 200, await revoked.clone().text());

    const dead = await mf.dispatchFetch(origin + "/api/runtime/bindings", {
      headers: { Authorization: "Bearer " + paired.runtimeToken },
    });
    assert.equal(dead.status, 401);
  } finally {
    await mf.dispose();
  }
});

test("uncertain provider effect blocks executor handoff before authority generation changes", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
  }, 100);
  try {
    const owner = await seedOwner(db);
    await db
      .prepare(
        "INSERT INTO external_effects(workspace,fingerprint,delivery_id,provider,text_digest,status,created_at,updated_at) VALUES (?,?,?,?,?,'uncertain',?,?)",
      )
      .bind(
        owner.workspace,
        "f".repeat(64),
        "delivery-uncertain",
        "x",
        "d".repeat(64),
        Date.now(),
        Date.now(),
      )
      .run();

    const preview = await mf.dispatchFetch(origin + "/api/runtime/executor/preview", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({ mode: "hosted", reason: "attempt transition" }),
    });
    assert.equal(preview.status, 200);
    const review: any = await preview.json();
    assert.equal(review.status, "blocked");
    assert.ok(review.blockers.includes("external_effect_uncertain"));
    assert.equal(review.current.authorityGeneration, 1);

    const row = await db
      .prepare("SELECT authority_generation FROM workspace_executors WHERE workspace=?")
      .bind(owner.workspace)
      .first<any>();
    assert.equal(Number(row.authority_generation), 1);
  } finally {
    await mf.dispose();
  }
});
