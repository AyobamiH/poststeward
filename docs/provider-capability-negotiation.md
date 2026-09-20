# Provider capability negotiation

PostSteward does not treat a configured provider application, a requested OAuth scope, and a granted runtime capability as the same fact.

The provider application surface reports what can be requested. A completed owner connection records the scopes returned by the provider and derives separate identity, publish, readback, refresh and metrics capabilities. If a provider omits scope echoing, only the required baseline scopes are retained as requested-but-unconfirmed; optional authority is never invented.

## Current contracts

- **X** requires `tweet.read`, `tweet.write`, `users.read` and `offline.access`. A usable refresh token is required. Readback and public metrics use the `tweet.read` grant.
- **Threads** requires `threads_basic` and `threads_content_publish`. `threads_manage_insights` is optional. If Meta omits scope echoing, publishing/readback remain operational under the requested baseline but the detailed capability state stays `unknown`; insights remain unavailable unless explicitly echoed.
- **LinkedIn member profile** requires `openid`, `profile` and `w_member_social`. Restricted `r_member_social` is optional and never inferred; without it, controlled acceptance cannot claim independent member-post readback.
- **LinkedIn organisation/Page** uses a dedicated Community Management-only application and requires `w_organization_social` and `r_organization_social` for the exact reviewed Page actor. The member is the OAuth subject, while the Page URN is state-bound and independently rechecked through the role-gated Posts finder. Page publishing/readback does not request OpenID scopes or require `r_member_social`.

The compatibility `Account.capabilities` booleans remain deliberately small so existing delivery bindings do not change merely because richer evidence metadata was added. Detailed negotiation evidence is stored with OAuth metadata and exposed without tokens or refresh secrets through the owner-only OAuth status endpoint.

Refresh re-negotiates capabilities and increments the account routing version only if an operational capability changes. Identity drift still deactivates the account. Missing optional scopes degrade the relevant feature rather than weakening permission checks or pretending provider acceptance.
