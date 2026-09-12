# P0 live acceptance evidence — 12 September 2026

## Disposition

| Gate | Status | Evidence boundary |
| --- | --- | --- |
| Owner Google sign-in | Accepted | Owner supplied hosted UI evidence; preserve the prior acceptance. A fresh session may still be required for a later exact approval. |
| P0-1 Threads grant | Blocked | App credentials are deployed, but Meta has not saved the redirect allowlist; no stored verified Threads identity was shown. |
| P0-2 one controlled publication | Open | Owner supplied workspace and pilot evidence showed no delivery, reservation or recorded external effect. Reinspect before any future write. |
| P0-3 independent readback | Open | No real provider creation ID or readback receipt supplied. |
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

## Resume only unfinished work

1. Resolve the Meta settings-save blocker through the supported dashboard/support path. Reload settings and confirm the exact callback persisted.
2. Inspect existing connections and pilot/delivery receipts before beginning a new attempt. A grant or write may have completed outside this record.
3. Start fresh Threads consent from PostSteward for the intended `@proofandstate` account. Verify stored stable provider ID and capabilities. An alias or token-generator entry for `tailwaggingwebdesigns` is not the intended account's identity proof.
4. Obtain a fresh owner session if required by the fifteen-minute approval window. Prepare one exact text/destination review in `/pilot`; approve only that immutable review.
5. Preserve the single delivery and provider creation ID. On uncertain outcomes, inspect that attempt; never generate a new key or publication merely to retry.
6. Perform independent readback of the recorded provider ID and require matching stable author and exact text, ending in `published_verified`.
7. Preserve the completed P0-4 record above. Any future delegated publishing or broader-scope proof is a distinct acceptance claim and requires its own authorised scope.

Native WebMCP, private GitHub, recovery, billing and public production remain separate gates. No public post or Meta support message was sent as part of this reconciliation.
