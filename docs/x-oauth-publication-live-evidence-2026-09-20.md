# X OAuth and publication live evidence — 20 September 2026

## Evidence boundary

This record separates application configuration, an active owner connection, a verified publication/readback and long-running token refresh. Those are different claims.

The provider effect occurred on 20 September 2026 UTC and was reviewed on the same date. No credential or token value is stored here.

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
- provider effect time: `2026-09-20T02:07:16.164Z`, independently derived from the X status ID.

The approved copy was:

> PostSteward just completed a controlled production check on X: owner-approved account connection, scoped OAuth, exact-copy dispatch and independent readback. A connection is not proof of publishing; the receipt is. This post is the live verification.

A separate provider-page load independently displayed:

- author `PostSteward @poststeward`;
- the exact approved text;
- canonical status ID `2101493362620502160`;
- visible provider timestamp `7:07 PM · Sep 19, 2026` in the browser locale, consistent with the UTC status-ID timestamp above.

The runtime receipt and provider page therefore agree on author, content and provider creation ID.

## Natural refresh rotation

The consent grant included `offline.access`, PostSteward persisted refresh capability and the active connection reported refresh as available. A separate observability-only revision was then merged in pull request `146` as `5a0ddd5d4b6ec57763bb8616ec15ea86585cb561` and deployed successfully through:

- staging run `35485750312`;
- production run `35485750300`.

That revision exposed only bounded owner-visible health, strategy and refresh timestamps; it exposed no credential value and did not invoke refresh, consent or publication.

Immediately before the natural production alarm, the owner and read-only account surfaces showed:

- alias `poststeward_x_test`, provider `x` and account `poststeward`;
- active connection, binding version `1` and the same stable author-ID hash recorded above;
- OAuth health `healthy`, credential strategy `refresh token` and publish/readback/refresh capabilities available;
- connection verification time `2026-09-20T01:52:00.288Z`;
- no previously observed token refresh;
- next refresh scheduled for `2026-09-20T03:15:59Z`.

Without a reconnect, force-refresh path, consent prompt or publication call, the production alarm completed the real X refresh at `2026-09-20T03:15:59.434Z`. The resulting persisted evidence showed:

- connection verification and last-refresh time advanced to `2026-09-20T03:15:59.434Z`;
- next refresh advanced to `2026-09-20T04:39:59Z`;
- OAuth health remained `healthy`, with no displayed refresh error;
- alias, provider, `poststeward` username, stable author ID, active state, binding version `1` and negotiated capabilities remained unchanged.

After the refresh, a separate X page load still displayed the original accepted post at the same canonical status URL under `PostSteward @poststeward` with the exact approved text. No second post was created. This accepts the observed live happy path for provider refresh, credential persistence and identity continuity; it does not claim that every provider outage or crash timing has been exercised.

## Accepted state

The evidence advances:

- `x_oauth` to `live_verified` for the dedicated application, real `@poststeward` grant, exact production callback, stable identity and active connection;
- `x_publication_readback` to `live_verified` for the owner-approved dispatch, verified receipt and independent X-page readback.
- `x_token_refresh_rotation` to `live_verified` for the natural production refresh, persisted lifecycle evidence and unchanged verified publishing identity.

This evidence does not advance LinkedIn, public signup or any unrelated provider gate.
