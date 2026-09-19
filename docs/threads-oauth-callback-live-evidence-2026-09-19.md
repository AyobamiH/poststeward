# Threads OAuth callback and owner-grant evidence — 19 September 2026

## Accepted callback contract

The earlier Meta callback-persistence blocker is closed.

Meta accepted and saved the PostSteward Threads configuration with the production callback contract:

- OAuth redirect: `https://app.poststeward.com/connections/oauth/threads/callback`
- Uninstall callback: `https://app.poststeward.com/connections/oauth/threads/uninstall`
- Data deletion callback: `https://app.poststeward.com/connections/oauth/threads/delete`

The restricted staging redirect remains allowed:

- `https://poststeward-staging.woeinvests.workers.dev/connections/oauth/threads/callback`

## Hosted runtime evidence

Production deployment run `35419352439` completed successfully on release `cb1f3697fca545c94453cf3b4339fecbfd3744a1`.

Hosted checks on that exact production release proved:

- `/health` returned the exact release with Threads OAuth configured;
- the uninstall callback route existed and rejected a non-effectful GET with HTTP 405;
- the deletion callback route existed and rejected a non-effectful GET with HTTP 405;
- release-control reconciliation passed against the reviewed gate ledger.

The callback handlers verify Meta `signed_request` HMACs, revoke the matching Threads authority, scrub provider-derived identifiers on deletion and return Meta-compatible deletion confirmation/status responses.

## Real owner OAuth acceptance

The owner then completed the normal production customer journey at `https://app.poststeward.com`:

1. PostSteward initiated the Threads authorisation flow against Meta.
2. Meta displayed the PostSteward consent screen for Threads account information/posts, content publishing and optional insights.
3. The owner approved the provider consent.
4. Meta returned to the production PostSteward callback.
5. PostSteward completed the server-side authorisation-code exchange and long-lived-token exchange.
6. PostSteward independently read the provider identity and persisted the connection.
7. The production workspace displayed the account as **Connected** with a fresh verification timestamp and a stable Threads author ID.

For evidence minimisation, the raw provider ID is not copied into this repository. Its SHA-256 is:

`911246cbc48d3d2a952dba06f4ceb45d1754196ca50ce11afbfe9da466da1e2b`

The owner-visible connection verification timestamp was 19 September 2026 at 04:54:29 Europe/London.

No social publication was required to close this OAuth gate.

## Scope/readback nuance

Threads may omit the granted-scope echo during token exchange. PostSteward deliberately records that as `scopeEvidence=request_assumed` rather than inventing provider-confirmed scope evidence.

For the required Threads baseline, the application requested `threads_basic` and `threads_content_publish`, the stable provider identity read succeeded, and the compatibility account record treats the required baseline readback path as usable. Optional insights authority is never inferred when Meta omits scope echoing.

This distinction is preserved in the UI and does not weaken the OAuth acceptance claim.

## Accepted state

The `threads_oauth_callback` gate is now `live_verified`.

This acceptance covers:

- Meta callback persistence;
- real owner provider consent;
- exact production callback return;
- successful server-side code/token exchange;
- stable provider identity verification and persistence.

It does not claim that every optional Threads permission was independently echoed by Meta.
