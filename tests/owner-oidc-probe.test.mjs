import assert from "node:assert/strict";
import test from "node:test";
import { probeOwnerClient } from "../scripts/owner-oidc-probe.mjs";

const env = {
  DEPLOY_ENV: "staging", OIDC_ISSUER: "https://accounts.google.com",
  OIDC_CLIENT_ID: "fixture.apps.googleusercontent.com", OIDC_CLIENT_SECRET: "private-fixture-client-secret",
  PUBLIC_ORIGIN: "https://poststeward-staging.woeinvests.workers.dev",
};
function sender(error = "invalid_grant", tokenEndpoint = "https://oauth2.googleapis.com/token", method = "client_secret_basic") {
  const calls = [];
  let assertionFailure;
  const send = async (input, init) => {
    try {
    const url = String(input); calls.push(url);
    assert.equal(init.redirect, "manual");
    if (url === "https://accounts.google.com/.well-known/openid-configuration")
      return Response.json({ issuer: env.OIDC_ISSUER, token_endpoint: tokenEndpoint, authorization_response_iss_parameter_supported: true });
    assert.equal(url, "https://oauth2.googleapis.com/token");
    const form = new URLSearchParams(init.body);
    const authorization = new Headers(init.headers).get("authorization");
    if (method === "client_secret_basic") {
      assert.match(authorization, /^Basic /);
      const credentials = Buffer.from(authorization.slice(6), "base64").toString().split(":")
        .map(value => decodeURIComponent(value.replaceAll("+", " ")));
      assert.deepEqual(credentials, [env.OIDC_CLIENT_ID, env.OIDC_CLIENT_SECRET]);
      assert.equal(form.get("client_secret"), null);
    } else {
      assert.equal(authorization, null);
      assert.equal(form.get("client_id"), env.OIDC_CLIENT_ID);
      assert.equal(form.get("client_secret"), env.OIDC_CLIENT_SECRET);
    }
    assert.equal(form.get("grant_type"), "authorization_code");
    assert.equal(form.get("redirect_uri"), env.PUBLIC_ORIGIN + "/auth/callback");
    assert.match(form.get("code"), /^poststeward-invalid-diagnostic-[a-f0-9-]{36}$/);
    assert.ok(form.get("code_verifier"));
    assert.equal(form.get("refresh_token"), null);
    return Response.json({ error, error_description: "private-provider-description " + env.OIDC_CLIENT_SECRET }, { status: error === "invalid_client" ? 401 : 400 });
    } catch (error) { assertionFailure = error; throw error; }
  };
  return { send, calls, assertHealthy() { if (assertionFailure) throw assertionFailure; } };
}

test("negative OIDC probe sends only a synthetic code and never claims sign-in", async () => {
  for (const method of ["client_secret_basic", "client_secret_post"])
  for (const [error, expected] of [["invalid_grant", "synthetic_code_rejected"], ["invalid_client", "client_rejected"], ["unauthorized_client", "client_rejected"], ["private-unrecognised-error", "provider_rejected"]]) {
    const f = sender(error, "https://oauth2.googleapis.com/token", method);
    const result = await probeOwnerClient(env, f.send, method);
    f.assertHealthy();
    assert.deepEqual(result, { outcome: expected, stage: "token_exchange", ownerSignInVerified: false });
    assert.equal(f.calls.length, 2);
    assert.doesNotMatch(JSON.stringify(result), /private-|fixture\.apps|code_verifier/);
  }
});

test("OIDC probe refuses other environments, endpoints and malformed configuration before sending secrets", async () => {
  for (const change of [
    { DEPLOY_ENV: "production" }, { OIDC_ISSUER: "https://other.example" },
    { OIDC_CLIENT_ID: "bad\nid" }, { OIDC_CLIENT_SECRET: " trailing-secret\n" },
    { PUBLIC_ORIGIN: "https://another.example" },
  ]) {
    let calls = 0;
    await probeOwnerClient({ ...env, ...change }, async () => { calls++; throw new Error("must not send"); });
    assert.equal(calls, 0);
  }
  const f = sender("invalid_grant", "https://untrusted.example/token");
  assert.equal((await probeOwnerClient(env, f.send)).outcome, "metadata_unexpected");
  assert.equal(f.calls.length, 1);
  const result = await probeOwnerClient(env, async () => { throw new Error("private-transport-details"); });
  assert.equal(result.outcome, "diagnostic_inconclusive");
  assert.doesNotMatch(JSON.stringify(result), /private-/);
});
