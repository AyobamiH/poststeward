# Owner sign-in diagnosis — 11 September 2026

## Observed failure

The owner attempted Google sign-in outside ChatGPT. The callback returned PostSteward's JSON `INTERNAL_ERROR` response. The screenshot contains no verified owner workspace or completed sign-in receipt. The deployed runtime at that observation was `2dc0bfee59a441bbcedd95a59d6877a89c4845f0`; documentation-only main was `96de71ab2efd6ab7c4241b4be7e5aa50e40b4a0d`.

Cloud Browser separately rejected the callback with Chromium `ERR_BLOCKED_BY_CLIENT`, including during manual takeover after both sites were set to Always allow. That browser restriction does not explain the application error observed in the owner's normal browser. No callback URL query, authorisation code, state, cookie, identity token or secret is retained in this record.

## Diagnostic defect and change

The existing callback converted every unexpected OAuth or database exception to a generic durable-operation error. Neither that message nor the existing request-failure log identified the failing sign-in stage. The cause of the observed callback error is therefore still unproven.

The callback now classifies client rejection, rejected authorisation codes, response/token validation, provider availability and identity storage failures. It returns an opaque support reference, fixed stage/reason and release identifier. Logs contain only that bounded classification; provider descriptions, exception messages/causes, claims, credentials and callback URLs are excluded. Existing deliberate access/state faults remain intact.

The Google runtime fixture now follows the documented separate discovery, token and JWKS hosts and includes the issuer response parameter. Regression cases cover client/code rejection, authentication challenge, malformed response, invalid nonce/issuer, replay, session preservation, failed proof persistence and diagnostic redaction. These are isolated tests, not a live Google sign-in.

## Staging negative client probe

The protected staging deploy step uses the already-selected OIDC client secret only against Google's exact token endpoint. It supplies a newly generated synthetic invalid code and verifier. It accepts no user code, browser cookie or callback URL and cannot create a PostSteward session. Dependency installation and tests do not receive the secret.

- `client_rejected`: Google rejected the client authentication/grant authorisation; check the paired saved client ID and secret.
- `configuration_invalid`: the configured diagnostic inputs are malformed, missing, have surrounding/control whitespace, or do not match the canonical staging origin.
- `synthetic_code_rejected`: Google rejected the deliberately invalid code. This does **not** prove the client secret is correct or that a real sign-in will succeed.
- Any other outcome: inconclusive. Do not infer sign-in success.

The probe is diagnostic, not a deployment acceptance gate. A successful deployment still does not close the owner sign-in journey.

## Next acceptance evidence

Inspect the staging probe result first. After resolving any demonstrated configuration failure, start a new sign-in from `/pilot`; never reload or replay an old callback. If it fails, retain only the returned error code, support reference, stage/reason and release. A successful owner proof must then be observed in the owner's workspace. Cloud Browser sign-in is a separate session and remains blocked until its policy issue is resolved.

The five live journeys remain open. Do not infer provider publication, private GitHub authority, point-in-time recovery, native WebMCP execution, or Stripe settlement from this change.

References: [Google discovery metadata](https://accounts.google.com/.well-known/openid-configuration), [Google OIDC reference](https://developers.google.com/identity/openid-connect/reference).
