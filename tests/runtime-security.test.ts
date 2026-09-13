import { test } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPair, exportJWK, SignJWT } from "jose";
import { Response as RuntimeResponse } from "miniflare";
import { digest } from "../src/common.ts";
import { expireIdentity } from "../src/security.ts";
import type { Env } from "../src/types.ts";
import { runtime } from "./runtime-fixture.ts";

test("Workers OIDC accepts a signed invited identity and rejects replay, forged signatures and unverified email", async () => {
  const trusted = await generateKeyPair("RS256"),
    attacker = await generateKeyPair("RS256");
  const jwk = {
    ...(await exportJWK(trusted.publicKey)),
    kid: "trusted",
    alg: "RS256",
    use: "sig",
  };
  let nonce = "",
    forged = false,
    verified = true;
  const { mf, db } = await runtime(async (request) => {
    const u = new URL(request.url);
    if (u.pathname === "/.well-known/openid-configuration")
      return RuntimeResponse.json({
        issuer: "https://identity.example",
        authorization_endpoint: "https://identity.example/authorize",
        token_endpoint: "https://identity.example/token",
        jwks_uri: "https://identity.example/jwks",
        response_types_supported: ["code"],
        subject_types_supported: ["public"],
        id_token_signing_alg_values_supported: ["RS256"],
        code_challenge_methods_supported: ["S256"],
      });
    if (u.pathname === "/jwks") return RuntimeResponse.json({ keys: [jwk] });
    if (u.pathname === "/token") {
      const body = new URLSearchParams(await request.text());
      assert.ok(body.get("code_verifier"));
      assert.equal(
        body.get("redirect_uri"),
        "https://publish.example/auth/callback",
      );
      const id_token = await new SignJWT({
        nonce,
        email: "owner@example.com",
        email_verified: verified,
      })
        .setProtectedHeader({ alg: "RS256", kid: "trusted" })
        .setIssuer("https://identity.example")
        .setSubject("owner-subject")
        .setAudience("poststeward-test")
        .setIssuedAt()
        .setExpirationTime("5m")
        .sign(forged ? attacker.privateKey : trusted.privateKey);
      return RuntimeResponse.json({
        access_token: "test-access-token",
        token_type: "Bearer",
        id_token,
      });
    }
    throw new Error("Unexpected identity endpoint");
  });
  async function begin() {
    const response = await mf.dispatchFetch(
      "https://publish.example/auth/login",
      { redirect: "manual" },
    );
    assert.equal(response.status, 302);
    const location = new URL(response.headers.get("location")!);
    nonce = location.searchParams.get("nonce")!;
    assert.equal(location.searchParams.get("code_challenge_method"), "S256");
    assert.ok(location.searchParams.get("scope")?.includes("email"));
    const state = location.searchParams.get("state")!;
    return () =>
      mf.dispatchFetch(
        `https://publish.example/auth/callback?state=${state}&code=test-code`,
        { redirect: "manual", headers: { Cookie: `__Host-login=${state}` } },
      );
  }
  try {
    const finish = await begin();
    const accepted = await finish();
    assert.equal(accepted.status, 302, await accepted.clone().text());
    assert.equal(accepted.headers.get("location"), "/app");
    assert.match(
      accepted.headers.get("set-cookie") || "",
      /HttpOnly; Secure; SameSite=Lax/,
    );
    assert.equal((await finish()).status, 400);
    forged = true;
    const forgedResponse = await (await begin())();
    assert.equal(forgedResponse.status, 400);
    assert.equal((await forgedResponse.json() as any).error.code, "LOGIN_IDENTITY_INVALID");
    forged = false;
    verified = false;
    assert.equal((await (await begin())()).status, 403);
    assert.equal(
      (await db.prepare("SELECT count(*) AS n FROM principals").first<any>())?.n,
      1,
    );
    assert.equal(
      (await db.prepare("SELECT count(*) AS n FROM sessions").first<any>())?.n,
      1,
    );
    assert.equal((await db.prepare("SELECT count(*) AS n FROM owner_proofs").first<any>())?.n, 1);
  } finally {
    await mf.dispose();
  }
});

test("Workers sessions enforce CSRF, reject Bearer fallback and cap active agent grants", async () => {
  const { mf, db } = await runtime();
  try {
    await db.prepare("INSERT INTO principals VALUES (?,?,?)").bind("owner", "workspace", Date.now()).run();
    await db.prepare("INSERT INTO sessions VALUES (?,?,?,?,?)")
      .bind(await digest("owner-session"), "workspace", "owner", Date.now() + 60000, "csrf-secret").run();
    const headers = {
      Cookie: "__Host-session=owner-session",
      Origin: "https://publish.example",
      "Content-Type": "application/json",
      "x-csrf-token": "csrf-secret",
    };
    const denied = await mf.dispatchFetch("https://publish.example/api/grants", {
      method: "POST", headers: { ...headers, "x-csrf-token": "wrong" }, body: '{"scopes":["read"]}',
    });
    assert.equal(denied.status, 403);
    const badBearer = await mf.dispatchFetch("https://publish.example/api/session", {
      headers: { ...headers, Authorization: "Basic invalid" },
    });
    assert.equal(badBearer.status, 401);
    for (let n = 0; n < 49; n++)
      await db.prepare("INSERT INTO grants VALUES (?,?,?,?,?,NULL)")
        .bind(`grant-${n}`, "workspace", "owner", '["read"]', Date.now() + 60000).run();
    const last = await mf.dispatchFetch("https://publish.example/api/grants", {
      method: "POST", headers, body: '{"scopes":["read"]}',
    });
    assert.equal(last.status, 201);
    assert.equal(last.headers.get("cache-control"), "no-store");
    const over = await mf.dispatchFetch("https://publish.example/api/grants", {
      method: "POST", headers, body: '{"scopes":["read"]}',
    });
    assert.equal(over.status, 409);
    assert.equal((await db.prepare("SELECT count(*) AS n FROM grants").first<any>())?.n, 50);
    const wrongHost = await mf.dispatchFetch("https://different.example/help.json");
    assert.equal(wrongHost.status, 403);
    const health = await mf.dispatchFetch("https://publish.example/health");
    assert.equal(health.headers.get("strict-transport-security"), "max-age=31536000");
    assert.match(health.headers.get("content-security-policy")!, /object-src 'none'/);
    await db.prepare("INSERT INTO sessions VALUES (?,?,?,?,?)").bind("expired-session", "workspace", "owner", 1, "expired").run();
    await db.prepare("INSERT INTO login_states (state_hash,verifier,nonce,expires_at) VALUES (?,?,?,?)")
      .bind("expired-state", "verifier", "nonce", 1).run();
    await expireIdentity({ IDENTITY: db } as unknown as Env);
    assert.equal((await db.prepare("SELECT count(*) AS n FROM sessions").first<any>())?.n, 1);
    assert.equal((await db.prepare("SELECT count(*) AS n FROM login_states").first<any>())?.n, 0);
    const statuses = [];
    // A short burst can cross a fixed 60-second bucket boundary; cover both buckets.
    for (let n = 0; n < 241; n++) {
      const r = await mf.dispatchFetch("https://publish.example/api/session", {
        headers: { "CF-Connecting-IP": "203.0.113.17" },
      });
      statuses.push(r.status);
      if (r.status === 429) { assert.equal(r.headers.get("retry-after"), "60"); break; }
    }
    assert.ok(statuses.includes(429));
  } finally {
    await mf.dispose();
  }
});
