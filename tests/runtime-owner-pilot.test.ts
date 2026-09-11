import assert from "node:assert/strict";
import test from "node:test";
import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { Response as RuntimeResponse } from "miniflare";
import { runtime } from "./runtime-fixture.ts";
import { digest } from "../src/common.ts";
import { expireIdentity } from "../src/security.ts";
import type { Env } from "../src/types.ts";

async function googleFixture() {
  const trusted = await generateKeyPair("RS256"), attacker = await generateKeyPair("RS256");
  const jwk = { ...(await exportJWK(trusted.publicKey)), kid: "test-key", alg: "RS256", use: "sig" };
  const origin = "https://publish.example";
  let nonce = "", challenge = "", mode = "valid", postedText = "", writes = 0, readbacks = 0;
  const { mf, db } = await runtime(async (request) => {
    const url = new URL(request.url);
    if (["accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"].includes(url.hostname)) {
      if (url.pathname === "/.well-known/openid-configuration") return RuntimeResponse.json({
        issuer: "https://accounts.google.com", authorization_endpoint: "https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint: "https://oauth2.googleapis.com/token", jwks_uri: "https://www.googleapis.com/oauth2/v3/certs",
        authorization_response_iss_parameter_supported: true,
        response_types_supported: ["code"], subject_types_supported: ["public"],
        id_token_signing_alg_values_supported: ["RS256"], code_challenge_methods_supported: ["S256"],
        token_endpoint_auth_methods_supported: ["client_secret_post", "client_secret_basic"],
      });
      if (url.pathname === "/oauth2/v3/certs") return RuntimeResponse.json({ keys: [jwk] });
      if (url.pathname === "/token") {
        if (mode === "client_rejected" || mode === "code_rejected")
          return RuntimeResponse.json({ error: mode === "client_rejected" ? "invalid_client" : "invalid_grant",
            error_description: "private-upstream-description private-google-fixture-token" }, { status: mode === "client_rejected" ? 401 : 400 });
        if (mode === "challenge")
          return new RuntimeResponse("", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="private-provider-realm"' } });
        if (mode === "malformed") return RuntimeResponse.json({ private_value: "private-google-fixture-token" });
        const form = new URLSearchParams(await request.text());
        assert.equal(form.get("redirect_uri"), origin + "/auth/callback");
        assert.ok(request.headers.get("authorization")?.startsWith("Basic "));
        const computed = Buffer.from(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(form.get("code_verifier")!))).toString("base64url");
        assert.equal(computed, challenge);
        const id_token = await new SignJWT({ nonce: mode === "nonce" ? "wrong" : nonce,
          email: mode === "uninvited" ? "uninvited@example.com" : "owner@example.com", email_verified: mode !== "unverified" })
          .setProtectedHeader({ alg: "RS256", kid: "test-key" })
          .setIssuer(mode === "issuer" ? "https://wrong.example" : "https://accounts.google.com")
          .setSubject("stable-owner-subject")
          .setAudience(mode === "audience" ? "another-client" : "poststeward-test")
          .setIssuedAt().setExpirationTime(mode === "expired" ? Math.floor(Date.now() / 1000) - 1000 : Math.floor(Date.now() / 1000) + 300)
          .sign(mode === "forged" ? attacker.privateKey : trusted.privateKey);
        return RuntimeResponse.json({ access_token: "private-google-fixture-token", token_type: "Bearer", id_token });
      }
    }
    if (url.hostname === "api.x.com") {
      assert.equal(request.headers.get("authorization"), "Bearer private-provider-fixture-token");
      if (url.pathname === "/2/users/me") return RuntimeResponse.json({ data: { id: "provider-owner", username: "fixture_owner" } });
      if (url.pathname === "/2/tweets" && request.method === "POST") {
        writes++; postedText = (await request.json() as { text: string }).text;
        return RuntimeResponse.json({ data: { id: "one-created-post" } });
      }
      if (url.pathname === "/2/tweets/one-created-post" && request.method === "GET") {
        readbacks++; return RuntimeResponse.json({ data: { id: "one-created-post", author_id: "provider-owner", text: postedText } });
      }
    }
    throw new Error("Unexpected outbound endpoint in isolated runtime fixture");
  }, { OIDC_ISSUER: "https://accounts.google.com" }, 100);
  async function begin(nextMode = "valid", previousCookie = "") {
    mode = nextMode;
    const response = await mf.dispatchFetch(origin + "/auth/login?return=%2Fpilot", { redirect: "manual" });
    assert.equal(response.status, 302);
    const location = new URL(response.headers.get("location")!);
    nonce = location.searchParams.get("nonce")!; challenge = location.searchParams.get("code_challenge")!;
    const state = location.searchParams.get("state")!;
    const cookie = "__Host-login=" + state + (previousCookie ? "; " + previousCookie : "");
    return () => mf.dispatchFetch(origin + "/auth/callback?state=" + encodeURIComponent(state) + "&code=fixture-code&iss=" + encodeURIComponent(mode === "response_issuer" ? "https://wrong.example" : "https://accounts.google.com"), { redirect: "manual", headers: { Cookie: cookie } });
  }
  async function signin(previousCookie = "") {
    const response = await (await begin("valid", previousCookie))();
    assert.equal(response.status, 302, await response.clone().text());
    assert.equal(response.headers.get("location"), "/pilot");
    const value = /__Host-session=([^;,\s]+)/.exec(response.headers.get("set-cookie") || "")?.[1];
    assert.ok(value);
    const cookie = "__Host-session=" + value;
    const session: any = await (await mf.dispatchFetch(origin + "/api/session", { headers: { Cookie: cookie } })).json();
    assert.ok(session.csrf); return { cookie, session, value };
  }
  const call = (auth: { cookie: string; session: any }, path: string, input?: unknown, extra: Record<string, string> = {}) => mf.dispatchFetch(origin + path, {
    redirect: "manual", method: input === undefined ? "GET" : "POST",
    headers: { Cookie: auth.cookie, Origin: origin, "Content-Type": "application/json", "X-CSRF-Token": auth.session.csrf, ...extra },
    ...(input === undefined ? {} : { body: JSON.stringify(input) }),
  });
  return { mf, db, origin, begin, signin, call, writes: () => writes, readbacks: () => readbacks };
}

test("real Workers callback proof, owner approval, durable alarm and independent provider readback form one path", { timeout: 90000 }, async () => {
  const f = await googleFixture();
  try {
    const auth = await f.signin();
    const status: any = await (await f.call(auth, "/api/pilot/status")).json();
    assert.equal(status.owner.issuer, "https://accounts.google.com"); assert.equal(status.owner.workspace, auth.session.workspace);
    assert.ok(status.owner.id); assert.equal(status.record, null); assert.equal(status.completed, false);
    assert.equal((await f.call(auth, "/api/pilot/prepare", { alias: "account", text: "Exact controlled publication." }, { "X-CSRF-Token": "wrong" })).status, 403);
    assert.equal((await f.call(auth, "/api/pilot/prepare", { alias: "account", text: "Exact controlled publication." }, { Origin: "https://hostile.example" })).status, 403);
    const connected = await f.call(auth, "/api/connections/import", { alias: "account", provider: "x", accessToken: "private-provider-fixture-token", funding: "customer_app" });
    assert.equal(connected.status, 200, await connected.clone().text());
    const preview: any = await (await f.call(auth, "/api/pilot/prepare", { alias: "account", text: "Exact controlled publication." })).json();
    assert.ok(preview.record?.id, JSON.stringify(preview)); assert.equal(f.writes(), 0);
    const input = { reviewId: preview.record.id, reviewDigest: preview.record.reviewDigest, approve: true };
    const confirmed = await f.call(auth, "/api/pilot/confirm", input); assert.equal(confirmed.status, 200, await confirmed.clone().text());
    const reserved: any = await confirmed.json();
    const repeated: any = await (await f.call(auth, "/api/pilot/confirm", input)).json();
    assert.equal(repeated.delivery.id, reserved.delivery.id); assert.equal(repeated.completed, false);
    assert.doesNotMatch(JSON.stringify(repeated), /private-provider|private-google|ownerSession|sessionHash|email_hash/);
    const bypass = await f.call(auth, "/api/operations/publish_now", { campaign: preview.record.id, idempotencyKey: "attempt-pilot-bypass" });
    assert.equal(bypass.status, 409);
    const granted: any = await (await f.call(auth, "/api/grants", { scopes: ["read", "publish"], hours: 1 })).json();
    assert.ok(granted.token);
    const agent = await f.mf.dispatchFetch(f.origin + "/api/pilot/status", { headers: { Authorization: "Bearer " + granted.token } });
    assert.equal(agent.status, 403);
    // No calls are made while the real durable alarm waits for the cancellation window.
    await new Promise((resolve) => setTimeout(resolve, Math.max(0, reserved.delivery.dueAt - Date.now()) + 1000));
    let receipt: any;
    for (let attempt = 0; attempt < 20; attempt++) {
      receipt = await (await f.call(auth, "/api/pilot/status")).json();
      if (receipt.delivery?.postId) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    assert.equal(receipt.delivery?.postId, "one-created-post", JSON.stringify(receipt));
    assert.equal(receipt.completed, false); assert.equal(f.writes(), 1);
    const accepted: any = await (await f.call(auth, "/api/pilot/recheck", {})).json();
    assert.equal(accepted.completed, true, JSON.stringify(accepted)); assert.ok(accepted.record.firstVerified.at);
    assert.equal(accepted.record.firstVerified.outcome, "EXACT_PROVIDER_READBACK"); assert.ok(f.readbacks() >= 2);
    assert.equal(f.writes(), 1);
    const loggedOut = await f.call(auth, "/auth/logout", {}); assert.equal(loggedOut.status, 200);
    assert.equal((await f.call(auth, "/api/pilot/status")).status, 401);
    assert.equal((await f.db.prepare("SELECT count(*) AS n FROM owner_proofs").first<any>())?.n, 0);
  } finally { await f.mf.dispose(); }
});

test("real D1 proof admission rejects forged/wrong claims, replay, missing proof, expiry and open redirects", async () => {
  const f = await googleFixture();
  try {
    const redirected = await f.mf.dispatchFetch(f.origin + "/auth/login?return=https%3A%2F%2Fevil.example", { redirect: "manual" });
    assert.equal(redirected.status, 400);
    for (const mode of ["forged", "issuer", "response_issuer", "audience", "nonce", "expired", "uninvited", "unverified"]) {
      const denied = await (await f.begin(mode))(); assert.ok(denied.status >= 400, mode);
      assert.equal((await f.db.prepare("SELECT count(*) AS n FROM owner_proofs").first<any>())?.n, 0, mode);
      assert.equal((await f.db.prepare("SELECT count(*) AS n FROM sessions").first<any>())?.n, 0, mode);
    }
    const callback = await f.begin(); assert.equal((await callback()).status, 302); assert.equal((await callback()).status, 400);
    const auth = await f.signin();
    const renewed = await f.signin(auth.cookie);
    assert.equal((await f.call(auth, "/api/session")).status, 401);
    const proof: any = await (await f.call(renewed, "/api/pilot/status")).json(); assert.ok(proof.owner.id);
    const stored = await f.db.prepare("SELECT * FROM owner_proofs WHERE session_hash=?").bind(await digest(renewed.value)).first<any>();
    assert.equal(stored.email_hash, await digest("owner@example.com"));
    assert.doesNotMatch(JSON.stringify(stored), /owner@example.com|private-google-fixture-token/);
    await f.db.prepare("UPDATE owner_proofs SET email_hash=? WHERE session_hash=?").bind(await digest("removed@example.com"), await digest(renewed.value)).run();
    assert.equal((await f.call(renewed, "/api/pilot/status")).status, 409);
    await f.db.prepare("UPDATE sessions SET expires_at=1 WHERE token_hash=?").bind(await digest(renewed.value)).run();
    await expireIdentity({ IDENTITY: f.db } as unknown as Env);
    assert.equal(await f.db.prepare("SELECT id FROM owner_proofs WHERE session_hash=?").bind(await digest(renewed.value)).first(), null);
    const legacy = await f.signin();
    await f.db.prepare("DELETE FROM owner_proofs WHERE session_hash=?").bind(await digest(legacy.value)).run();
    assert.equal((await f.call(legacy, "/api/session")).status, 200);
    assert.equal((await f.call(legacy, "/api/pilot/status")).status, 409);
    assert.equal(f.writes(), 0);
  } finally { await f.mf.dispose(); }
});

test("proof storage failure cannot return a newly usable owner session", async () => {
  const f = await googleFixture();
  try {
    await f.db.prepare("CREATE TRIGGER reject_proof BEFORE INSERT ON owner_proofs BEGIN SELECT RAISE(ABORT, 'fixture failure'); END").run();
    const response = await (await f.begin())(); assert.equal(response.status, 500);
    const failure: any = await response.clone().json();
    assert.equal(failure.error.code, "LOGIN_STORAGE_FAILED");
    assert.equal(failure.error.details.stage, "session");
    assert.match(failure.error.details.reference, /^[a-f0-9-]{36}$/);
    assert.doesNotMatch(JSON.stringify(failure), /fixture failure|RAISE|INSERT|owner@example.com|private-/);
    assert.doesNotMatch(response.headers.get("set-cookie") || "", /__Host-session=/);
    assert.equal((await f.db.prepare("SELECT count(*) AS n FROM sessions").first<any>())?.n, 0);
    assert.equal((await f.db.prepare("SELECT count(*) AS n FROM owner_proofs").first<any>())?.n, 0);
  } finally { await f.mf.dispose(); }
});

test("Google callback failures report safe causes, consume state once and preserve an existing owner session", async () => {
  const f = await googleFixture();
  try {
    const auth = await f.signin();
    for (const [mode, code, status] of [
      ["client_rejected", "LOGIN_CLIENT_REJECTED", 503],
      ["code_rejected", "LOGIN_CODE_REJECTED", 400],
      ["challenge", "LOGIN_CLIENT_REJECTED", 503],
      ["malformed", "LOGIN_IDENTITY_INVALID", 400],
      ["nonce", "LOGIN_IDENTITY_INVALID", 400],
      ["response_issuer", "LOGIN_RESPONSE_INVALID", 400],
    ] as const) {
      const callback = await f.begin(mode, auth.cookie);
      const response = await callback();
      assert.equal(response.status, status, mode);
      const failure: any = await response.json();
      assert.equal(failure.error.code, code, mode);
      assert.match(failure.error.details.reference, /^[a-f0-9-]{36}$/);
      assert.doesNotMatch(JSON.stringify(failure), /private-|fixture-code|owner@example.com|Basic |claims|nonce.*wrong/);
      assert.doesNotMatch(response.headers.get("set-cookie") || "", /__Host-session=/);
      const replay = await callback();
      assert.equal(replay.status, 400);
      assert.equal((await replay.json() as any).error.code, "LOGIN_STATE_EXPIRED");
      assert.equal((await f.call(auth, "/api/pilot/status")).status, 200);
      assert.equal((await f.db.prepare("SELECT count(*) AS n FROM sessions").first<any>())?.n, 1);
      assert.equal((await f.db.prepare("SELECT count(*) AS n FROM owner_proofs").first<any>())?.n, 1);
    }
    assert.equal(f.writes(), 0);
  } finally { await f.mf.dispose(); }
});
