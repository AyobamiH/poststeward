# Provider connection audit — 19 September 2026

## Scope and evidence boundary

This audit separates provider application configuration, an active owner account connection and a verified publication/readback. Those are three different claims and must not be collapsed into one readiness label.

The read-only review used exact deployed release `bf74c0a442acba18e41a1faf4d5d793bd4a36981` in staging and production, an authenticated production owner session, the reviewed source at that revision and the provider-focused automated tests. It did not start provider consent, connect or disconnect an account, create a project or campaign, or publish anything.

## Observed state

| Provider | Application configured | Active production owner connection | Live PostSteward publication/readback evidence |
| --- | --- | --- | --- |
| X | yes, staging and production | none | none accepted |
| Threads | yes, staging and production | one healthy OAuth connection using the long-lived-token strategy | accepted separately from an earlier controlled publication and independent provider readback; the current production workspace has no delivery receipt |
| LinkedIn | no | none | none accepted; the independently verified PostSteward Page identity is not OAuth authority |

The production owner interface reports X and Threads applications configured, LinkedIn OAuth unconfigured, zero X connections, one Threads connection and zero LinkedIn connections. The connected Threads record has a verified stable provider identity. The production workspace currently has no projects and no delivery receipts.

The public readiness endpoint correctly reports application capability only. Its `connection_required` values are not a workspace connection inventory. The authenticated owner endpoint and account list are the authoritative connection views.

## Enforcement and test evidence

PostSteward refuses meaningful agent publication without an active destination connection:

- project routing calls the active-connection check for every alias;
- campaign validation rechecks the active account and provider text;
- dispatch rechecks connection activity, stable provider identity, binding version, credential authority, publication pause and actor authority immediately before a provider write;
- disconnect increments the binding and blocks future unclaimed work from using the old authority.

The exact revision passed 84 provider/connection-focused tests after a clean local build. These cover X and LinkedIn OAuth callbacks in Workers/D1, PKCE and one-use state, owner-only OAuth start/status, encrypted access and refresh material, stable-identity drift, refresh races, LinkedIn Page scopes, provider write ambiguity, exact readback and the engine's connection/binding fences. Mocked provider tests prove implementation behaviour; they do not manufacture a real external grant.

## Corrections from this audit

- The reviewed `x_oauth` ledger entry was stale: X application credentials are deployed, but no owner X connection is accepted.
- The hosted readiness script incorrectly failed when X was configured. Restricted Threads acceptance requires Threads; it must report optional provider configuration without requiring X and LinkedIn to remain absent.
- Owner UI labels conflated application configuration with connected-account capability. Application counts, active connection counts and per-connection capability evidence now render separately.
- LinkedIn member-profile readback is not a prerequisite for the intended organization/Page path. The Page path needs a configured LinkedIn application, owner consent for the exact Page actor and organization publish/read scopes.
- Threads connection acceptance and Threads publication/readback acceptance remain separate evidence. No repeat public post is needed merely to prove that the connection exists.

## Remaining provider actions

1. X: complete one real owner OAuth consent, persist and inspect the stable identity and granted scopes, observe refresh authority, then perform one deliberately approved publication/readback only if X is claimed as end-to-end supported.
2. LinkedIn: configure the application and exact callbacks, obtain organization permissions, complete owner consent for `urn:li:organization:146607525`, verify Page access and then perform one controlled Page publication/readback if LinkedIn is in release scope.
3. Threads: preserve the active connection and existing accepted publication evidence. A single combined OAuth-connected publication receipt is optional strengthening, not a reason to create an unnecessary public post.
4. Native WebMCP: the supporting browser advertised 27 tools, but the page's authenticated read-only `workspace_status` check still returned “tool is not registered”; the gate remains open.

Public signup remains disabled by policy. Production infrastructure gates marked `live_verified` do not imply that all three social providers are connected or accepted.

## X follow-up — 20 September 2026

The X rows and remaining action above are a point-in-time record from the read-only 19 September audit. They are superseded for X by `docs/x-oauth-publication-live-evidence-2026-09-20.md`.

The owner subsequently configured the dedicated application authority, authorised the production callback as `@poststeward`, verified the stable identity and active connection, and approved one controlled publication with independent provider-page readback. A real token-expiry refresh rotation was not forced and remains a separately visible, non-blocking follow-up. LinkedIn and native WebMCP findings are unchanged.
