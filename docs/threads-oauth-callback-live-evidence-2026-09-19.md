# Threads OAuth callback evidence — 19 September 2026

## What changed

The earlier external blocker was Meta's refusal to persist the exact Threads callback allowlist. That blocker is no longer current.

The owner confirmed that Meta accepted and saved the PostSteward Threads settings with the production callback contract:

- OAuth redirect: `https://app.poststeward.com/connections/oauth/threads/callback`
- Uninstall callback: `https://app.poststeward.com/connections/oauth/threads/uninstall`
- Data deletion callback: `https://app.poststeward.com/connections/oauth/threads/delete`

The staging OAuth redirect remains allowed for restricted acceptance:

- `https://poststeward-staging.woeinvests.workers.dev/connections/oauth/threads/callback`

## Runtime evidence

Production deployment run `35417886121` completed successfully on release `951034d3e1b2c2c2057ba193dc66e5b80a5c059f`.

The hosted production runtime reports Threads OAuth configured. Independent read-only probes confirm:

- `/health` returns the exact production release and `providerOAuth.threads=true`.
- the uninstall route exists and rejects an ordinary GET with HTTP 405, proving it is present without triggering an uninstall;
- the deletion route exists and rejects an ordinary GET with HTTP 405, proving it is present without triggering deletion.

The callback handlers verify Meta `signed_request` HMACs, revoke the exact matching Threads authority, scrub provider-derived identifiers on deletion, and return Meta-compatible deletion confirmation/status responses.

## Remaining acceptance boundary

This evidence closes the specific upstream callback-persistence blocker. It does **not** claim the owner OAuth journey is accepted yet.

The remaining bounded acceptance is one real owner Threads OAuth connection through PostSteward that proves:

1. Meta authorisation returns to the exact PostSteward callback.
2. PostSteward exchanges the code successfully.
3. The stable Threads identity is persisted for the intended workspace.
4. The resulting capability evidence is visible without exposing tokens.

Until that owner journey is completed, keep the gate in `external_setup_required`, not `live_verified`.
