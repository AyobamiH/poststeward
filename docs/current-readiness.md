# Current release gates

Generated from `src/release-gates.ts`. Do not edit by hand. Runtime configuration is reported separately by `/readiness.json`; these rows record reviewed evidence/policy state and never infer live acceptance from code alone.

| Gate | State | Scope | Production blocker | Summary |
| --- | --- | --- | --- | --- |
| `owner_google_signin` | `live_verified` | `restricted_staging` | no | Real owner Google sign-in accepted in the owner browser. |
| `threads_publication_readback` | `live_verified` | `restricted_staging` | no | Controlled Threads publication and independent provider readback accepted. |
| `inspect_http_remote_mcp` | `live_verified` | `restricted_staging` | no | Inspect-only HTTP and remote MCP accepted before revoke and denied after revoke. |
| `stripe_sandbox_lifecycle` | `live_verified` | `restricted_staging` | no | Sandbox Checkout, paid state, refund/revocation, cancellation and webhook ledger accepted. |
| `protected_root_cutover` | `live_verified` | `restricted_staging` | no | Protected next-root writer and complete readback traversal accepted; legacy root intentionally retained. |
| `private_github_authority` | `live_verified` | `restricted_staging` | no | Selected private repository read and provider-side revoke/fail-closed proof accepted. |
| `exact_recovery_checkpoints` | `live_verified` | `restricted_staging` | no | Exact checkpoint capture, real restore, reconciliation, resume and disposable erasure are accepted on restricted staging. |
| `approximate_timestamp_pitr` | `blocked_external` | `restricted_staging` | no | Cloudflare hosted timestamp-to-bookmark resolution is failing; exact checkpoints do not depend on it. |
| `threads_oauth_callback` | `blocked_external` | `restricted_staging` | no | Meta currently rejects persistence of the exact Threads callback allowlist. |
| `native_webmcp` | `unavailable_capability` | `restricted_staging` | no | PostSteward native WebMCP is deployed; the owner's ordinary browser does not expose the native API. |
| `x_oauth` | `external_setup_required` | `provider_optional` | no | X OAuth implementation is present; the real application/client authority is not configured in staging. |
| `linkedin_poststeward_page_identity` | `live_verified` | `provider_optional` | no | PostSteward LinkedIn Page identity is independently verified as urn:li:organization:146607525; OAuth authority remains separate. |
| `linkedin_oauth` | `external_setup_required` | `provider_optional` | no | LinkedIn OAuth implementation is present; the real application/client authority is not configured in staging. |
| `linkedin_member_readback` | `external_setup_required` | `provider_optional` | no | Independent member-post readback remains unavailable until the actual LinkedIn application receives the restricted permission. |
| `advanced_rollout` | `disabled_policy` | `advanced` | no | Advanced execution remains deliberately disabled until canary product acceptance and SLO gates pass. |
| `mpp` | `disabled_policy` | `advanced` | no | MPP is a separate optional settlement stream and remains disabled. |
| `public_signup` | `disabled_policy` | `public_launch` | yes | Public signup remains restricted until production/support/abuse controls are accepted. |
| `production_edge` | `external_setup_required` | `production` | yes | Custom production origin, DNS/TLS, WAF and rate-policy evidence remain open. |
| `github_main_ruleset` | `live_verified` | `production` | no | Server-side main protection is active and independently read back from GitHub with the reviewed solo-maintainer policy. |
| `operational_alert_delivery` | `external_setup_required` | `production` | yes | Production alert delivery and escalation evidence remain open. |
| `capacity_cost_calibration` | `external_setup_required` | `production` | yes | Representative capacity/cost observations with required headroom remain open. |
| `hosted_cross_tenant` | `live_verified` | `production` | no | Hosted two-workspace isolation, hostile-Origin denial and ephemeral grant cleanup are accepted on the exact staged release. |

## Evidence references

- `owner_google_signin`: `docs/p0-live-evidence-2026-09-12.md`
- `threads_publication_readback`: `docs/p0-live-evidence-2026-09-12.md`
- `inspect_http_remote_mcp`: `docs/p0-live-evidence-2026-09-12.md`
- `stripe_sandbox_lifecycle`: `docs/live-external-gates-2026-09-13.md`
- `protected_root_cutover`: `docs/protected-root-cutover.md`, `docs/live-external-gates-2026-09-13.md`
- `private_github_authority`: `docs/live-external-gates-2026-09-13.md`
- `exact_recovery_checkpoints`: `docs/recovery.md`, `docs/exact-recovery-live-evidence-2026-09-14.md`, `actions/34890295276`
- `approximate_timestamp_pitr`: `docs/live-external-gates-2026-09-13.md`
- `threads_oauth_callback`: `docs/live-external-gates-2026-09-13.md`
- `native_webmcp`: `docs/live-external-gates-2026-09-13.md`; owner action: Use one authenticated supporting browser for the read-only workspace_status proof when available.
- `x_oauth`: `docs/live-external-gates-2026-09-13.md`
- `linkedin_poststeward_page_identity`: `docs/live-external-gates-2026-09-13.md`
- `linkedin_oauth`: `docs/live-external-gates-2026-09-13.md`
- `linkedin_member_readback`: `docs/live-external-gates-2026-09-13.md`
- `advanced_rollout`: `docs/production-readiness-acceptance.md`
- `mpp`: `docs/production-readiness-acceptance.md`
- `public_signup`: `docs/production-readiness-acceptance.md`
- `production_edge`: `docs/production-readiness-acceptance.md`
- `github_main_ruleset`: `docs/github-main-ruleset-live-evidence-2026-09-15.md`, `ruleset/23461973`
- `operational_alert_delivery`: `docs/production-readiness-acceptance.md`
- `capacity_cost_calibration`: `docs/production-readiness-acceptance.md`
- `hosted_cross_tenant`: `docs/hosted-cross-tenant-live-evidence-2026-09-15.md`, `actions/35029954430`
