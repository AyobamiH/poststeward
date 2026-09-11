import * as oauth from "oauth4webapi";
import { Fault, uid } from "./common.ts";

export type LoginStage = "state" | "discovery" | "authorization" | "token_exchange" | "token_validation" | "signature" | "principal" | "session";

const validationCodes = new Set([
  "OAUTH_JWT_CLAIM_COMPARISON_FAILED", "OAUTH_JWT_TIMESTAMP_CHECK_FAILED",
  "OAUTH_INVALID_RESPONSE", "OAUTH_JSON_ATTRIBUTE_COMPARISON_FAILED",
  "OAUTH_KEY_SELECTION_FAILED", "OAUTH_INVALID_SERVER_METADATA",
]);

/** Never copy provider descriptions, exception messages/causes, claims or request URLs. */
export function loginFailure(error: unknown, stage: LoginStage, release: string): Fault {
  if (error instanceof Fault) return error;
  let code = "LOGIN_CALLBACK_FAILED";
  let message = "Owner sign-in could not complete. Restart sign-in from the acceptance page and retain this reference if it fails again.";
  let status = 500;
  let reason = "unexpected";
  if (error instanceof oauth.AuthorizationResponseError) {
    code = "LOGIN_AUTHORIZATION_REJECTED";
    message = "The identity provider did not authorise this sign-in. Restart from the acceptance page.";
    status = 400;
    reason = error.error === "access_denied" ? "access_denied" : "authorization_rejected";
  } else if (error instanceof oauth.ResponseBodyError) {
    if (["invalid_client", "unauthorized_client"].includes(error.error)) {
      code = "LOGIN_CLIENT_REJECTED";
      message = "The identity provider rejected PostSteward's OAuth client configuration. The operator must check the saved client ID and secret before another sign-in.";
      status = 503;
      reason = error.error;
    } else if (error.error === "invalid_grant") {
      code = "LOGIN_CODE_REJECTED";
      message = "The identity provider rejected this sign-in code. Start a new sign-in from the acceptance page; do not reload the callback.";
      status = 400;
      reason = "invalid_grant";
    } else {
      code = "LOGIN_PROVIDER_REJECTED";
      message = "The identity provider could not complete the sign-in request. Restart from the acceptance page.";
      status = 502;
      reason = "provider_rejected";
    }
  } else if (error instanceof oauth.WWWAuthenticateChallengeError && ["token_exchange", "token_validation"].includes(stage) && error.status === 401) {
    code = "LOGIN_CLIENT_REJECTED";
    message = "The identity provider rejected PostSteward's OAuth client authentication. The operator must check the saved client ID and secret.";
    status = 503;
    reason = "client_authentication_rejected";
  } else if (stage === "state" || stage === "principal" || stage === "session") {
    code = "LOGIN_STORAGE_FAILED";
    message = "PostSteward could not finish saving this sign-in. Restart from the acceptance page and retain this reference if it fails again.";
    reason = "identity_storage";
  } else if (error instanceof oauth.OperationProcessingError) {
    code = stage === "authorization" ? "LOGIN_RESPONSE_INVALID" : "LOGIN_IDENTITY_INVALID";
    message = "The identity response could not be verified. No new owner session was issued. Restart sign-in from the acceptance page.";
    status = 400;
    reason = error.code && validationCodes.has(error.code) ? error.code : "identity_validation";
  } else if (error instanceof TypeError || (error instanceof Error && ["AbortError", "TimeoutError", "NetworkError"].includes(error.name))) {
    code = "LOGIN_PROVIDER_UNAVAILABLE";
    message = "PostSteward could not reach the identity provider. Start a new sign-in from the acceptance page.";
    status = 503;
    reason = "provider_transport";
  }
  const reference = uid();
  const revision = /^[a-f0-9]{40}$/.test(release) ? release : "development";
  const details = { reference, stage, reason, release: revision };
  console.warn(JSON.stringify({ event: "owner_login_failed", code, ...details }));
  return new Fault(code, message, status, details);
}
