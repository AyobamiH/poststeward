import assert from "node:assert/strict";
import test from "node:test";
import { digest } from "../src/common.ts";
import { claimRuntimePairing } from "../src/runtime-coordination.ts";
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
    RELEASE_SHA: "c".repeat(40),
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
    assert.equal(bindingValue.release, "c".repeat(40));
    assert.equal(bindingValue.schemaVersion, 1);
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

    const fenced = await mf.dispatchFetch(origin + "/api/runtime/executor/fence", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + paired.runtimeToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        authorityGeneration: 2,
        reason: "local runtime deactivation",
      }),
    });
    assert.equal(fenced.status, 200, await fenced.clone().text());
    const inactive: any = await fenced.json();
    assert.equal(inactive.executorMode, "local");
    assert.equal(inactive.executorStatus, "inactive");
    assert.equal(inactive.activeInstallationId, installationId);
    assert.equal(inactive.authorityGeneration, 3);
    assert.equal(inactive.leaseExpiresAt, undefined);

    const fencedHeartbeat = await mf.dispatchFetch(origin + "/api/runtime/heartbeat", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + paired.runtimeToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ authorityGeneration: 2 }),
    });
    assert.equal(fencedHeartbeat.status, 409);

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
    const hostedValue: any = await hosted.json();
    assert.equal(hostedValue.executor.authorityGeneration, 4);
    assert.equal(hostedValue.executor.executorStatus, "active");

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

test("approved pairing claim can recover a lost HTTP acknowledgement without a second issuance", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
  }, 100);
  try {
    const owner = await seedOwner(db);
    const installationId = "55555555-5555-4555-8555-555555555555";
    const initial = await mf.dispatchFetch(origin + "/api/runtime/pairing/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        installationId,
        label: "Interrupted claim",
        platform: "Linux",
        runtimeVersion: "0.28.29",
      }),
    });
    assert.equal(initial.status, 201, await initial.clone().text());
    const start: any = await initial.json();
    const approval = await mf.dispatchFetch(origin + "/api/runtime/pairing/approve", {
      method: "POST",
      headers: owner.headers,
      body: JSON.stringify({ pairingId: start.pairingId, userCode: start.userCode }),
    });
    assert.equal(approval.status, 200, await approval.clone().text());
    // Candidate material is generated and retained locally before any owner-claim GET.
    const candidate = "A".repeat(64);
    const query = origin + "/api/runtime/pairing/status?pairing_id=" + start.pairingId;
    const headers = {
      Authorization: "Bearer " + start.pollToken,
      "X-PostSteward-Runtime-Candidate": candidate,
    };
    const first = await mf.dispatchFetch(query, { headers });
    assert.equal(first.status, 200, await first.clone().text());
    const firstBody: any = await first.json();
    assert.equal(firstBody.status, "paired");
    assert.equal(firstBody.candidateAccepted, true);
    assert.equal(firstBody.runtimeToken, undefined, "Cloud must not echo the client-held token");
    // Simulate an interrupted client that did not receive or persist the first HTTP response.
    const second = await mf.dispatchFetch(query, { headers });
    assert.equal(second.status, 200, await second.clone().text());
    const secondBody: any = await second.json();
    assert.equal(secondBody.status, "paired");
    assert.equal(secondBody.candidateAccepted, true);
    assert.equal(secondBody.tokenExpiresAt, firstBody.tokenExpiresAt);
    const installations = await db.prepare(
      "SELECT installation_id,token_hash,status FROM runtime_installations WHERE installation_id=?",
    ).bind(installationId).all<any>();
    assert.equal(installations.results.length, 1);
    assert.equal(installations.results[0].token_hash, await digest(candidate));
    const binding = await mf.dispatchFetch(origin + "/api/runtime/bindings", {
      headers: { Authorization: "Bearer " + candidate },
    });
    assert.equal(binding.status, 200, await binding.clone().text());
    const different = await mf.dispatchFetch(query, {
      headers: { ...headers, "X-PostSteward-Runtime-Candidate": "B".repeat(64) },
    });
    assert.equal(different.status, 409, "wrong candidate must not inherit authority");
    const legacy = await mf.dispatchFetch(query, {
      headers: { Authorization: "Bearer " + start.pollToken },
    });
    assert.equal(legacy.status, 409, "legacy replay must remain denied");
  } finally {
    await mf.dispose();
  }
});

test("new owner-approved claim can rotate an expired credential for the same installation and workspace", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
  }, 100);
  try {
    const owner = await seedOwner(db);
    const installationId = "66666666-6666-4666-8666-666666666666";

    async function approvedOffer(label: string) {
      const start = await mf.dispatchFetch(origin + "/api/runtime/pairing/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ installationId, label, platform: "Linux", runtimeVersion: "0.28.29" }),
      });
      assert.equal(start.status, 201, await start.clone().text());
      const offer: any = await start.json();
      const approval = await mf.dispatchFetch(origin + "/api/runtime/pairing/approve", {
        method: "POST",
        headers: owner.headers,
        body: JSON.stringify({ pairingId: offer.pairingId, userCode: offer.userCode }),
      });
      assert.equal(approval.status, 200, await approval.clone().text());
      return offer;
    }

    async function claim(offer: any, candidate: string) {
      return mf.dispatchFetch(origin + "/api/runtime/pairing/status?pairing_id=" + offer.pairingId, {
        headers: {
          Authorization: "Bearer " + offer.pollToken,
          "X-PostSteward-Runtime-Candidate": candidate,
        },
      });
    }

    const oldCandidate = "A".repeat(64);
    const first = await claim(await approvedOffer("Original laptop"), oldCandidate);
    assert.equal(first.status, 200, await first.clone().text());
    await db.prepare("UPDATE runtime_installations SET token_expires_at=? WHERE installation_id=?")
      .bind(Date.now() - 1000, installationId).run();

    const newCandidate = "B".repeat(64);
    const second = await claim(await approvedOffer("Recovered laptop"), newCandidate);
    assert.equal(second.status, 200, await second.clone().text());
    assert.equal((await second.json() as any).candidateAccepted, true);
    const saved = await db.prepare(
      "SELECT workspace,token_hash,status,token_expires_at FROM runtime_installations WHERE installation_id=?",
    ).bind(installationId).first<any>();
    assert.equal(saved?.workspace, owner.workspace);
    assert.equal(saved?.status, "active");
    assert.equal(saved?.token_hash, await digest(newCandidate));
    assert.ok(saved?.token_expires_at > Date.now());
    const staleBinding = await mf.dispatchFetch(origin + "/api/runtime/bindings", {
      headers: { Authorization: "Bearer " + oldCandidate },
    });
    assert.equal(staleBinding.status, 401);
    const currentBinding = await mf.dispatchFetch(origin + "/api/runtime/bindings", {
      headers: { Authorization: "Bearer " + newCandidate },
    });
    assert.equal(currentBinding.status, 200);
  } finally {
    await mf.dispose();
  }
});

test("an unexpired active installation cannot consume a different approved owner claim", async () => {
  const { mf, db } = await runtime(undefined, { OIDC_ISSUER: "https://accounts.google.com" }, 100);
  try {
    const owner = await seedOwner(db);
    const installationId = "77777777-7777-4777-8777-777777777777";
    const firstToken = "C".repeat(64);
    await db.prepare(
      "INSERT INTO runtime_installations (installation_id,workspace,label,platform,runtime_version,token_hash,status,created_at,approved_at,token_expires_at) VALUES (?,?,?,?,?,?,'active',?,?,?)",
    ).bind(installationId, owner.workspace, "Existing", "Linux", "0.28.29", await digest(firstToken), Date.now(), Date.now(), Date.now() + 900_000).run();
    const started = await mf.dispatchFetch(origin + "/api/runtime/pairing/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ installationId, label: "Competing request", platform: "Linux", runtimeVersion: "0.28.29" }),
    });
    assert.equal(started.status, 201);
    const offer: any = await started.json();
    const approval = await mf.dispatchFetch(origin + "/api/runtime/pairing/approve", {
      method: "POST", headers: owner.headers,
      body: JSON.stringify({ pairingId: offer.pairingId, userCode: offer.userCode }),
    });
    assert.equal(approval.status, 200);
    const refusal = await mf.dispatchFetch(origin + "/api/runtime/pairing/status?pairing_id=" + offer.pairingId, {
      headers: {
        Authorization: "Bearer " + offer.pollToken,
        "X-PostSteward-Runtime-Candidate": "D".repeat(64),
      },
    });
    assert.equal(refusal.status, 409);
    const pairing = await db.prepare("SELECT status FROM runtime_pairings WHERE id=?").bind(offer.pairingId).first<any>();
    assert.equal(pairing?.status, "approved", "refused claim must not be falsely recorded as consumed");
    const installed = await db.prepare("SELECT token_hash FROM runtime_installations WHERE installation_id=?").bind(installationId).first<any>();
    assert.equal(installed?.token_hash, await digest(firstToken));
  } finally {
    await mf.dispose();
  }
});

test("an expired installation cannot be reclaimed by a different owner's workspace", async () => {
  const { mf, db } = await runtime(undefined, { OIDC_ISSUER: "https://accounts.google.com" }, 100);
  try {
    const owner = await seedOwner(db);
    const installationId = "88888888-8888-4888-8888-888888888888";
    const oldToken = "E".repeat(64);
    await db.prepare(
      "INSERT INTO runtime_installations (installation_id,workspace,label,platform,runtime_version,token_hash,status,created_at,approved_at,token_expires_at) VALUES (?,?,?,?,?,?,'active',?,?,?)",
    ).bind(installationId, "different-workspace", "Other owner", "Linux", "0.28.29", await digest(oldToken), Date.now() - 5000, Date.now() - 5000, Date.now() - 1000).run();
    const started = await mf.dispatchFetch(origin + "/api/runtime/pairing/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ installationId, label: "Cross-workspace", platform: "Linux", runtimeVersion: "0.28.29" }),
    });
    assert.equal(started.status, 201);
    const offer: any = await started.json();
    const approval = await mf.dispatchFetch(origin + "/api/runtime/pairing/approve", {
      method: "POST", headers: owner.headers,
      body: JSON.stringify({ pairingId: offer.pairingId, userCode: offer.userCode }),
    });
    assert.equal(approval.status, 200);
    const refused = await mf.dispatchFetch(origin + "/api/runtime/pairing/status?pairing_id=" + offer.pairingId, {
      headers: {
        Authorization: "Bearer " + offer.pollToken,
        "X-PostSteward-Runtime-Candidate": "F".repeat(64),
      },
    });
    assert.equal(refused.status, 409);
    const pairing = await db.prepare("SELECT status FROM runtime_pairings WHERE id=?").bind(offer.pairingId).first<any>();
    assert.equal(pairing?.status, "approved");
    const installed = await db.prepare("SELECT workspace,token_hash FROM runtime_installations WHERE installation_id=?").bind(installationId).first<any>();
    assert.equal(installed?.workspace, "different-workspace");
    assert.equal(installed?.token_hash, await digest(oldToken));
  } finally {
    await mf.dispose();
  }
});

test("a revoked approval during a claim cannot mint an orphaned installation credential", async () => {
  const { mf, db } = await runtime(undefined, { OIDC_ISSUER: "https://accounts.google.com" }, 100);
  try {
    const owner = await seedOwner(db);
    const installationId = "99999999-9999-4999-8999-999999999999";
    const started = await mf.dispatchFetch(origin + "/api/runtime/pairing/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ installationId, label: "Revoked during claim", platform: "Linux", runtimeVersion: "0.28.29" }),
    });
    assert.equal(started.status, 201);
    const offer: any = await started.json();
    const approval = await mf.dispatchFetch(origin + "/api/runtime/pairing/approve", {
      method: "POST", headers: owner.headers,
      body: JSON.stringify({ pairingId: offer.pairingId, userCode: offer.userCode }),
    });
    assert.equal(approval.status, 200);
    let intervened = 0;
    const intercepted = new Proxy(db, {
      get(target, key) {
        if (key === "batch")
          return async (statements: D1PreparedStatement[]) => {
            intervened++;
            // Another authorised operation revokes the approved bootstrap
            // after this claimant read the row, before the D1 atomic batch.
            await target.prepare(
              "UPDATE runtime_pairings SET status='revoked' WHERE id=? AND status='approved'",
            ).bind(offer.pairingId).run();
            return target.batch(statements);
          };
        const value = Reflect.get(target, key);
        return typeof value === "function" ? value.bind(target) : value;
      },
    }) as D1Database;
    const candidate = "Z".repeat(64);
    const pollRequest = new Request(origin + "/api/runtime/pairing/status?pairing_id=" + offer.pairingId, {
      headers: {
        Authorization: "Bearer " + offer.pollToken,
        "X-PostSteward-Runtime-Candidate": candidate,
      },
    });
    await assert.rejects(
      claimRuntimePairing(intercepted, pollRequest, offer.pairingId),
      (error: any) => error?.code === "RUNTIME_PAIRING_RACE",
    );
    assert.equal(intervened, 1);
    const pairing = await db.prepare("SELECT status FROM runtime_pairings WHERE id=?").bind(offer.pairingId).first<any>();
    assert.equal(pairing?.status, "revoked");
    const stray = await db.prepare("SELECT token_hash FROM runtime_installations WHERE installation_id=?").bind(installationId).first<any>();
    assert.equal(stray, null, "revoked owner approval must never mint a token even transiently");
  } finally {
    await mf.dispose();
  }
});

test("owner-approved pairing never silently revives a revoked installation identity", async () => {
  const { mf, db } = await runtime(undefined, { OIDC_ISSUER: "https://accounts.google.com" }, 100);
  try {
    const owner = await seedOwner(db);
    const installationId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const oldToken = "G".repeat(64);
    await db.prepare(
      "INSERT INTO runtime_installations (installation_id,workspace,label,platform,runtime_version,token_hash,status,created_at,approved_at,token_expires_at,revoked_at) VALUES (?,?,?,?,?,?,'revoked',?,?,?,?)",
    ).bind(installationId, owner.workspace, "Revoked", "Linux", "0.28.29", await digest(oldToken), Date.now() - 5000, Date.now() - 5000, Date.now() - 1000, Date.now() - 1500).run();
    const started = await mf.dispatchFetch(origin + "/api/runtime/pairing/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ installationId, label: "Revoked try", platform: "Linux", runtimeVersion: "0.28.29" }),
    });
    assert.equal(started.status, 201);
    const offer: any = await started.json();
    const approval = await mf.dispatchFetch(origin + "/api/runtime/pairing/approve", {
      method: "POST", headers: owner.headers,
      body: JSON.stringify({ pairingId: offer.pairingId, userCode: offer.userCode }),
    });
    assert.equal(approval.status, 200);
    const attempt = await mf.dispatchFetch(origin + "/api/runtime/pairing/status?pairing_id=" + offer.pairingId, {
      headers: {
        Authorization: "Bearer " + offer.pollToken,
        "X-PostSteward-Runtime-Candidate": "H".repeat(64),
      },
    });
    assert.equal(attempt.status, 409);
    const record = await db.prepare("SELECT token_hash,status,revoked_at FROM runtime_installations WHERE installation_id=?").bind(installationId).first<any>();
    assert.equal(record?.token_hash, await digest(oldToken));
    assert.equal(record?.status, "revoked");
    assert.ok(record?.revoked_at);
  } finally {
    await mf.dispose();
  }
});
