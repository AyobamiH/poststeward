# P0 live acceptance evidence — 12 September 2026

## Disposition

| Gate | Status | Evidence boundary |
| --- | --- | --- |
| Owner Google sign-in | Accepted | Owner supplied hosted UI evidence; preserve the prior acceptance. A fresh session may still be required for a later exact approval. |
| P0-1 Threads grant | Blocked | A real authorised Threads token was manually imported and its stable identity verified. PostSteward redirect/code exchange and automatic refresh remain unverified; Meta callback saving is blocked. |
| P0-2 one controlled publication | Accepted from owner-supplied receipt | Delivery is `published_verified`; preserve the existing publication. |
| P0-3 independent readback | Accepted from owner-supplied receipt | Two `EXACT_PROVIDER_READBACK` observations and `completed: true`. |
| P0-4 Inspect-only HTTP/remote MCP delegation | Accepted from owner-supplied live transcript | Both transports accepted the same workspace/release, then denied the same token after owner revocation. Do not repeat this completed journey merely to close an older checklist. |

This is an owner-executed acceptance record, not an independently collected or cryptographically signed receipt. No token, raw workspace ID, OAuth state, cookie or authentication code is retained here. This evidence does not establish delegated publishing, native WebMCP, cross-tenant acceptance or public-launch readiness.

## Recorded token proof

The owner ran `scripts/hosted-acceptance.mjs` pinned to `ae4831ef368c5e3361f053e4ea28f4b2faa0a4f8`, using an Inspect-only token requested with a one-hour expiry. The supplied transcript records:

- Before revocation: 2026-09-12T13:25:57Z.
- Workspace fingerprint: `c5fa9d53b8253659`.
- Returned release: `ae4831ef368c5e3361f053e4ea28f4b2faa0a4f8`.
- HTTP: accepted; remote MCP: accepted.
- Owner revoked the same token between checks.
- After revocation: 2026-09-12T13:26:35Z.
- HTTP: denied; remote MCP: denied.

The reviewed harness requires HTTP 200 for the valid operation, MCP initialization and MCP operation, matching workspace/release across transports, then HTTP 401 from both operation transports after revocation. Timestamps above are client-side run timestamps. The grant identifier and server-side revocation timestamp were not supplied; the revocation action and requested expiry are owner-reported. The successful read test followed by denial is not evidence of a publishing grant.

The release also has a successful [staging deployment run](https://github.com/AyobamiH/poststeward/actions/runs/34679445845). Deployment checks remain separate from owner acceptance.

## Threads blocker

The code-derived callback for this staging origin is:

```text
https://poststeward-staging.woeinvests.workers.dev/connections/oauth/threads/callback
```

The owner supplied:

- Provider authorization error 1349168: redirect URI not allowlisted.
- Meta dashboard save error: “Form can't be saved”.
- Settings save request: POST `/apps/1863312665044847/async/threads-login/setting/save/?th_app_id=1774515593555988`.
- HTTP 404 with HTML titled “Page Not Found - Meta for Developers”.
- The same visible saving failure reproduced in incognito and another app. A separate HTTP trace for the second app was not supplied.

A Meta dashboard routing or access failure is suspected; its exact cause is unconfirmed. These observations do not prove callback syntax rejection, a global Meta outage, or a PostSteward runtime defect. App-credential readiness is not proof that the provider allowlist is saved.

Keep the original app and existing credentials. Do not replace callback paths, weaken OAuth checks, invent uninstall/deletion endpoints, import another product's token or repeatedly create apps to bypass this failure.

### Support report text (prepared, not submitted)

Threads API settings cannot save a redirect callback. The original app is 1863312665044847; its Threads app ID is 1774515593555988. Saving the exact HTTPS callback above triggers the POST path recorded above, which returns HTTP 404 and a Meta “Page Not Found” HTML response rather than a field-validation response. The UI says “Form can't be saved”. The saving failure also reproduces in an incognito session and another app. Please investigate the settings-save route and app access, and confirm how to persist the Threads redirect allowlist. No app secrets or user tokens are included.

## Completed controlled publication

The owner supplied the private receipt for release `ae4831ef368c5e3361f053e4ea28f4b2faa0a4f8`. Offline consistency validation matches delivery binding, stable author, exact text SHA-256, approval freshness, cancellation window and recorded readback. This does not authenticate the export or perform a fresh provider request.

- Owner approval: 2026-09-12T17:42:31.147Z.
- Delivery due: 2026-09-12T17:43:01.147Z.
- First exact readback: 2026-09-12T17:43:45.267Z; second: 17:46:27.182Z.
- Delivery status: `published_verified`; receipt `completed: true`.
- The outer `record.state: reserved` describes the consumed pilot slot, not a failed publication.
- Authorisation came through manual import. The receipt explicitly excludes live provider OAuth, native WebMCP, payments, disaster recovery and public launch.

Keep the raw export private. Run `node scripts/check-pilot-receipt.mjs /private/receipt.json` to check its consistency without any network request or provider write. The checker emits an allowlisted summary without owner/session identifiers.

## Resume only unfinished work

1. Preserve the completed publication, readback and Inspect-only grant/revoke evidence. Do not publish again or issue another token to repeat those checks.
2. Resolve the supported Meta callback-save path, then verify PostSteward's own OAuth consent, callback and code exchange. A verified manual token does not close that integration gate.
3. Exercise private GitHub, disposable-workspace recovery/erasure, protected root-key replacement, native WebMCP and Stripe separately when their required configuration and targets are available.

No new public post or Meta support message was sent during this reconciliation.
