# Exact recovery live evidence — 14 September 2026

This record supersedes the earlier open exact-recovery status in `docs/live-external-gates-2026-09-13.md`. It does not change the separate status of Cloudflare's approximate timestamp-to-bookmark convenience path.

## Accepted restricted-staging evidence

GitHub Actions run `34890295276` executed the manual `Staging disposable exact recovery and erasure acceptance` workflow on reviewed and hosted release `02ff8d2285718530d888fd8bd91e1fdcc1c2ba03`.

The synthetic-only acceptance receipt reported:

- exact checkpoint capture succeeded on the same release;
- recovery plan used `targetMode: exact_checkpoint` and required one preparation attempt;
- the synthetic publishing-pause canary was visible before recovery and absent after restore;
- quarantine remained active through restore/reconciliation and cleared only after explicit resume;
- a real Cloudflare Durable Object PITR restore executed successfully;
- timestamp resolution was not used;
- the disposable workspace export and lifecycle erasure completed;
- the deleted workspace's old session was denied;
- the completed deletion tombstone and minimal registry entry were retained;
- no customer workspace, provider credential, provider effect, payment, Google sign-in, accepted social-publication evidence, or raw Durable Object bookmark was used or exposed.

The workflow job, including `Exercise exact checkpoint restore and erasure on disposable state`, completed successfully.

## Boundary

The exact-checkpoint recovery contract is now `live_verified` for restricted staging. Do not repeat the restore/erasure rehearsal merely to obtain another receipt.

Cloudflare's hosted `getBookmarkForTime()` path remains independently `blocked_external`. It is an optional approximate-time convenience and is not a dependency of PostSteward's accepted exact recovery guarantee.

The disposable erasure evidence proves export, deletion, old-session denial and tombstone/registry retention for the synthetic workspace. Production/public launch may still require any broader admission or re-sign-in anti-resurrection scenario defined by the production-readiness contract; this restricted-staging acceptance does not silently close unrelated production gates.
