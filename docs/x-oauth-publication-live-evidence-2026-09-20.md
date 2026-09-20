# X OAuth and publication live evidence — 20 September 2026

## Evidence boundary

This record separates application configuration, an active owner connection, a verified publication/readback and long-running token refresh. Those are different claims.

The provider effect occurred on 19 September 2026 UTC and was reviewed on 20 September 2026. No credential or token value is stored here.

## Dedicated application and deployed authority

X application `PostSteward-Publishing` (application ID `33450857`) is dedicated to PostSteward publishing. It is owned from the developer account `@JohnWOE15`; that developer ownership does not determine which public account publishes.

The application was configured with:

- read-and-write access;
- confidential web-app/bot client type;
- production callback `https://app.poststeward.com/connections/oauth/x/callback`;
- restricted-staging callback `https://poststeward-staging.woeinvests.workers.dev/connections/oauth/x/callback`;
- no direct-message or email authority.

The client identifier and protected client secret were installed separately in the staging and production GitHub environments. The exact reviewed release `bc9ecb1980b558be45d5edf58405296fa77b218b` then deployed successfully through:

- staging run `35480757661`;
- production run `35480790294`.

Public signup remained restricted.

## Real `@poststeward` owner grant

The production customer journey was completed from `https://app.poststeward.com`:

1. PostSteward initiated the X OAuth authorisation-code flow with PKCE.
2. The X consent screen identified the signing-in account as `@poststeward` and the application as `PostSteward-Publishing`.
3. The displayed authority was `tweet.read`, `tweet.write`, `users.read` and `offline.access`; it did not include direct messages.
4. The owner explicitly authorised that account.
5. X returned to the exact production callback and PostSteward completed the server-side exchange.
6. PostSteward independently resolved and persisted the provider identity.
7. The owner interface displayed an active X connection for `poststeward`, with publish, readback, refresh and metrics capabilities available.

For evidence minimisation, the stable provider author ID is represented by this SHA-256 rather than copied into the repository:

`ae78af0815339a941ff575d5fd7e1894f6ccf19a9e3caad23e7b90e9ddc8b385`

## Controlled publication and independent readback

After the connection was verified, the owner explicitly approved one immediate production delivery. PostSteward submitted it exactly once and recorded:

- receipt state: `Verified`;
- receipt ID: `3456691d-254c-4a93-a72b-862bd22787a8`;
- provider creation/status ID: `2101493362620502160`;
- provider URL: `https://x.com/i/web/status/2101493362620502160`;
- provider effect time: 19 September 2026 at 19:07 UTC.

The approved copy was:

> PostSteward just completed a controlled production check on X: owner-approved account connection, scoped OAuth, exact-copy dispatch and independent readback. A connection is not proof of publishing; the receipt is. This post is the live verification.

A separate provider-page load independently displayed:

- author `PostSteward @poststeward`;
- the exact approved text;
- canonical status ID `2101493362620502160`;
- visible provider timestamp 19 September 2026 at 19:07.

The runtime receipt and provider page therefore agree on author, content and provider creation ID.

## Refresh boundary

The consent grant included `offline.access`, PostSteward persisted refresh capability and the active connection reports refresh as available. This proves refresh authority is present; it does not prove a token-expiry rotation actually occurred.

No expiry was manufactured and no real refresh-token rotation was deliberately forced during this acceptance run. That longevity observation remains a separate, non-blocking gate so the reviewed ledger does not overstate what was tested.

## Accepted state

The evidence advances:

- `x_oauth` to `live_verified` for the dedicated application, real `@poststeward` grant, exact production callback, stable identity and active connection;
- `x_publication_readback` to `live_verified` for the owner-approved dispatch, verified receipt and independent X-page readback.

The separate `x_token_refresh_rotation` gate remains `implemented` until a safe real rotation is observed. This evidence does not advance LinkedIn, public signup or any unrelated provider gate.
