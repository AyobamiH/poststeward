import * as oauth from "oauth4webapi";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

/**
 * Negative client-authentication diagnostic, not a sign-in or acceptance test.
 * Only a fresh, synthetic invalid code is sent. No browser state or user code
 * is accepted, no identity is created, and provider bodies are never returned.
 */
export async function probeOwnerClient(env, send = fetch, method = "client_secret_basic") {
  const report = (outcome, stage) => ({ outcome, stage, ownerSignInVerified: false });
  if (env.DEPLOY_ENV !== "staging" || env.OIDC_ISSUER !== "https://accounts.google.com")
    return report("not_applicable", "configuration");
  if (!["client_secret_basic", "client_secret_post"].includes(method) || typeof env.OIDC_CLIENT_ID !== "string" || !/^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$/.test(env.OIDC_CLIENT_ID) ||
      typeof env.OIDC_CLIENT_SECRET !== "string" || env.OIDC_CLIENT_SECRET.trim().length < 8 ||
      env.OIDC_CLIENT_SECRET !== env.OIDC_CLIENT_SECRET.trim() ||
      /[\x00-\x1f\x7f]/.test(env.OIDC_CLIENT_SECRET) ||
      env.PUBLIC_ORIGIN !== "https://poststeward-staging.woeinvests.workers.dev")
    return report("configuration_invalid", "configuration");
  let stage = "discovery";
  try {
    const issuer = new URL(env.OIDC_ISSUER);
    const options = { [oauth.customFetch]: send, signal: AbortSignal.timeout(10000) };
    const as = await oauth.processDiscoveryResponse(issuer, await oauth.discoveryRequest(issuer, options));
    if (as.token_endpoint !== "https://oauth2.googleapis.com/token")
      return report("metadata_unexpected", stage);
    const client = { client_id: env.OIDC_CLIENT_ID };
    const state = oauth.generateRandomState();
    const params = oauth.validateAuthResponse(as, client, new URLSearchParams({
      state, iss: as.issuer, code: "poststeward-invalid-diagnostic-" + crypto.randomUUID(),
    }), state);
    stage = "token_exchange";
    const response = await oauth.authorizationCodeGrantRequest(
      as, client, method === "client_secret_post" ? oauth.ClientSecretPost(env.OIDC_CLIENT_SECRET) : oauth.ClientSecretBasic(env.OIDC_CLIENT_SECRET), params,
      env.PUBLIC_ORIGIN + "/auth/callback", oauth.generateRandomCodeVerifier(),
      { [oauth.customFetch]: send, signal: AbortSignal.timeout(10000) },
    );
    await oauth.processAuthorizationCodeResponse(as, client, response, {
      expectedNonce: oauth.generateRandomNonce(), requireIdToken: true,
    });
    return report("unexpected_token_response", stage);
  } catch (error) {
    if (error instanceof oauth.ResponseBodyError) {
      if (["invalid_client", "unauthorized_client"].includes(error.error))
        return report("client_rejected", stage);
      if (error.error === "invalid_grant")
        return report("synthetic_code_rejected", stage);
      return report("provider_rejected", stage);
    }
    if (error instanceof oauth.WWWAuthenticateChallengeError && error.status === 401)
      return report("client_rejected", stage);
    return report("diagnostic_inconclusive", stage);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const c = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
    for (const method of ["client_secret_basic", "client_secret_post"]) {
      const result = await probeOwnerClient({ ...c.vars, OIDC_CLIENT_SECRET: process.env.OIDC_CLIENT_SECRET }, fetch, method);
      console.log(JSON.stringify({ event: "owner_oidc_client_diagnostic", method, ...result }));
    }
    console.log("This negative probe cannot verify an owner sign-in or prove that a real authorization code will succeed.");
  } catch {
    console.error("Owner OIDC diagnostic could not complete. No credential or provider response was logged.");
    process.exitCode = 1;
  }
}
