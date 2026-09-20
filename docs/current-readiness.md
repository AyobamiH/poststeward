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
| `threads_oauth_callback` | `live_verified` | `restricted_staging` | no | Meta callback persistence, one real production owner Threads OAuth journey and one current healthy long-lived owner connection are accepted; publication/readback evidence remains a separate gate. |
| `native_webmcp` | `unavailable_capability` | `restricted_staging` | no | Native WebMCP is deployed and tool registration is advertised, but the authenticated page's read-only workspace_status execution check still reports that the tool is not registered. |
| `x_oauth` | `live_verified` | `provider_optional` | no | Dedicated X application authority, the real @poststeward production OAuth grant, exact callback return, stable identity and current active connection are accepted. |
| `x_publication_readback` | `live_verified` | `provider_optional` | no | One owner-approved @poststeward X publication, verified PostSteward receipt and independent provider-page readback are accepted. |
| `x_token_refresh_rotation` | `live_verified` | `provider_optional` | no | A natural production alarm completed a real X token refresh, persisted the new lifecycle state and reverified the unchanged @poststeward publishing identity without consent or publication. |
| `linkedin_poststeward_page_identity` | `live_verified` | `provider_optional` | no | PostSteward LinkedIn Page identity is independently verified as urn:li:organization:146607525; OAuth authority remains separate. |
| `linkedin_oauth` | `external_setup_required` | `provider_optional` | no | LinkedIn OAuth implementation exists, but provider application/client authority and a real owner Page grant/connection are not configured. |
| `linkedin_member_readback` | `external_setup_required` | `provider_optional` | no | Independent member-profile post readback remains unavailable until LinkedIn grants the restricted permission; it is separate from and does not block the organization/Page path. |
| `advanced_rollout` | `disabled_policy` | `advanced` | no | Advanced execution remains deliberately disabled until canary product acceptance and SLO gates pass. |
| `mpp` | `disabled_policy` | `advanced` | no | MPP is a separate optional settlement stream and remains disabled. |
| `public_signup` | `disabled_policy` | `public_launch` | yes | Public signup remains restricted until production/support/abuse controls are accepted. |
| `production_edge` | `live_verified` | `production` | no | The exact production custom domain, Worker binding, security headers, canonical WAF rule and auth rate-limit rule are independently read back and accepted. |
| `github_main_ruleset` | `live_verified` | `production` | no | Server-side main protection is active and independently read back from GitHub with the reviewed solo-maintainer policy. |
| `operational_alert_delivery` | `live_verified` | `production` | no | Production operator alert delivery is accepted through the out-of-band GitHub issue control plane, including all reviewed alert classes and a deliberately failed-path escalation drill. |
| `capacity_cost_calibration` | `live_verified` | `production` | no | Exact-release staging and production observations project the reviewed first-100 workload with at least 30% product headroom and inside the reviewed Cloudflare/provider cost envelope. |
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
- `threads_oauth_callback`: `docs/threads-oauth-callback-live-evidence-2026-09-19.md`, `docs/provider-connection-audit-2026-09-19.md`
- `native_webmcp`: `docs/live-external-gates-2026-09-13.md`, `docs/provider-connection-audit-2026-09-19.md`; owner action: Use one authenticated supporting browser for the read-only workspace_status proof when available.
- `x_oauth`: `docs/x-oauth-publication-live-evidence-2026-09-20.md`, `docs/provider-connection-audit-2026-09-19.md`
- `x_publication_readback`: `docs/x-oauth-publication-live-evidence-2026-09-20.md`
- `x_token_refresh_rotation`: `docs/x-oauth-publication-live-evidence-2026-09-20.md`
- `linkedin_poststeward_page_identity`: `docs/live-external-gates-2026-09-13.md`
- `linkedin_oauth`: `docs/live-external-gates-2026-09-13.md`, `docs/provider-connection-audit-2026-09-19.md`
- `linkedin_member_readback`: `docs/live-external-gates-2026-09-13.md`
- `advanced_rollout`: `docs/production-readiness-acceptance.md`
- `mpp`: `docs/production-readiness-acceptance.md`
- `public_signup`: `docs/production-readiness-acceptance.md`
- `production_edge`: `docs/production-edge-live-evidence-2026-09-19.md`, `actions/35440149519`
- `github_main_ruleset`: `docs/github-main-ruleset-live-evidence-2026-09-15.md`, `ruleset/23461973`
- `operational_alert_delivery`: `docs/operational-alert-live-evidence-2026-09-19.md`, `actions/35440129849`
- `capacity_cost_calibration`: `docs/capacity-cost-live-evidence-2026-09-19.md`, `actions/35442767724`
- `hosted_cross_tenant`: `docs/hosted-cross-tenant-live-evidence-2026-09-15.md`, `actions/35029954430`
