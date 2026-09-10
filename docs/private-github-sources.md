# Private GitHub source authority

PostSteward supports GitHub repository paths as Advanced source signals. Public repositories continue to use anonymous read-only GitHub API requests. Private repositories require an explicit owner-controlled GitHub App connection.

This document separates the implemented security contract from the remaining hosted acceptance evidence. A configured GitHub App or a green CI run is not evidence that a real owner granted access to a private repository.

## Authority model

Private repository authority is deliberately outside the 26 agent operations. Only a signed-in owner browser can start, complete or remove a GitHub source connection. Agent tokens cannot install the GitHub App, inspect its credential material or expand repository access.

The GitHub App must be installed with:

- **Repository access:** selected repositories only.
- **Repository permission:** Contents read-only.
- **Metadata:** read-only as supplied by GitHub.
- No write permission and no additional active repository permission.
- At most 50 selected repositories for one PostSteward installation.

PostSteward rejects an `all`-repositories installation, a write-capable installation, unexpected active permissions, a suspended/wrong app installation, or a repository set outside the bounded inventory.

## GitHub App registration options

The registration settings are part of the protocol contract, not optional operator preferences:

- Keep **User-to-server token expiration** enabled. PostSteward intentionally requires the expiring user access token plus rotating refresh token that GitHub issues for this mode. A non-expiring token response is rejected rather than retained.
- Leave **Request user authorization (OAuth) during installation** disabled. GitHub makes the Setup URL unavailable when that option is enabled. PostSteward deliberately uses the Setup URL first, then starts the separate GitHub App user OAuth flow after binding the returned candidate installation ID to the existing owner/session/state.
- Keep the exact **Setup URL** and **Callback URL** separate. GitHub sends installation completion to the Setup URL and web-application user authorization to the Callback URL.
- Device Flow is not required for the browser-hosted PostSteward owner path.

This preserves the security property GitHub recommends for Setup URLs: the `installation_id` returned to the Setup URL is not trusted by itself; PostSteward obtains a user access token and proves that the installation is accessible to that user before retaining authority.

## Owner installation flow

1. The owner signs in to PostSteward through the restricted Google OIDC flow.
2. `POST /api/sources/github/start` requires the owner browser, same-origin CSRF and a fresh owner proof. It creates a bounded ten-minute state plus PKCE verifier in D1 and returns the exact GitHub App installation URL.
3. GitHub returns to `/sources/github/setup` with a candidate `installation_id`. PostSteward binds that candidate to the existing owner/session/state but does **not** treat the query parameter as proof of authority.
4. PostSteward starts GitHub App user OAuth with PKCE. It sends a random `state`, an S256 `code_challenge`, and later the matching `code_verifier` at token exchange. The browser can navigate only to exact `https://github.com` through the workspace's trusted-external navigation guard.
5. `/sources/github/callback` atomically consumes the pending state. PostSteward exchanges the code, verifies the GitHub user can see the candidate installation, verifies the expected app slug/active state, selected-repository scope and read-only permissions, then fetches the complete selected repository inventory.
6. Only after those checks succeed does PostSteward retain the installation and repository links.

The setup callback therefore cannot turn a spoofed `installation_id` into repository authority.

## Credential lifecycle

PostSteward retains an expiring **GitHub App user access/refresh credential**, not a broad installation token. This keeps source authority tied to the GitHub user's current access as well as to the app's permissions.

The credential is encrypted with the deployment encryption root and authenticated context `<workspace>:github:<installation-id>`. Plaintext access/refresh tokens are not returned by status APIs, stored in browser storage or included in logs.

GitHub refresh tokens rotate and invalidate the preceding access/refresh pair ([GitHub contract](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens)). Before sending a refresh token, PostSteward atomically claims a 30-second D1 lease for the exact encrypted credential and revision. A concurrent reader receives retryable `GITHUB_REFRESH_IN_PROGRESS`, or uses the newly committed credential if rotation has already finished. Only that lease holder can commit before the deadline; reconnection clears the lease and supersedes earlier responses.

A lost response, expired lease or uncertain refresh outcome never permits replay of the old token. The installation remains fenced with `GITHUB_REFRESH_UNCERTAIN` and requires owner reconnection. This deliberately trades automatic recovery for avoiding reuse of a possibly consumed credential. Revocation/error updates also match the exact credential revision, so a delayed request cannot mark a newer owner connection stale.

## Every private source read revalidates authority

A linked private source check does not trust the installation snapshot captured at link time. Before the repository commit endpoint is read, PostSteward:

1. Loads and, if necessary, refreshes the encrypted user credential.
2. Re-reads the GitHub user's accessible installations.
3. Re-proves the expected app slug, unsuspended state, selected-repositories mode and read-only permission set.
4. Re-reads the selected repository inventory and proves the exact linked repository ID remains present.
5. Handles repository removal or rename by marking the installation stale while retaining the original repository link, so a subsequent read cannot silently switch to anonymous access.
6. Only then reads the requested branch/path commit snapshot.

If the owner later grants a write permission, widens the installation to all repositories, loses organisation/repository access, removes the repository, suspends the app or revokes the user credential, the next private source read fails closed before repository content is consumed. No anonymous fallback is attempted for a repository that is already linked as private authority.

Public repositories that have no workspace link retain the existing anonymous read-only path.

## Browser and agent surface

Owner browser endpoints:

- `GET /api/sources/github/status`
- `POST /api/sources/github/start`
- `POST /api/sources/github/unlink`
- `GET /sources/github/setup`
- `GET /sources/github/callback`

The workspace UI exposes only installation/repository metadata and bounded status/error codes. Linked repositories populate suggestions for the Advanced repository field; that field remains free text so public repositories do not require a GitHub App connection.

The source-authority endpoints are intentionally not MCP/WebMCP/agent tools. Automation can consume a repository link only after the owner has established it.

## Deletion boundary

A pending workspace deletion fences every GitHub source route before GitHub-specific handling. The final callback credential/link transaction rechecks the still-live owner session and absence of a deletion tombstone. A callback already waiting on GitHub cannot recreate authority after logout or pending/completed erasure. Completed erasure removes:

- pending GitHub installation/OAuth state;
- encrypted GitHub user credentials and installation metadata;
- workspace repository links.

The permanent minimal workspace-deletion tombstone remains so restored Durable Object state cannot resurrect repository or publication authority.

## Deployment configuration

Private GitHub sources are optional. The deployment accepts the GitHub App configuration only as an all-or-nothing triple:

Environment variables:

- `GITHUB_APP_CLIENT_ID`
- `GITHUB_APP_SLUG`

Environment secret:

- `GITHUB_APP_CLIENT_SECRET`

If one member of the triple is present without the others, deployment fails before Cloudflare mutation. The client secret is supplied only to the deployment step and is never mapped into the verification job.

For the current workers.dev staging origin, configure the GitHub App with:

- Setup URL: `https://poststeward-staging.woeinvests.workers.dev/sources/github/setup`
- Callback URL: `https://poststeward-staging.woeinvests.workers.dev/sources/github/callback`
- Request user authorization (OAuth) during installation: **off**
- User-to-server token expiration: **on**

If staging moves to a custom origin, both URLs must move to that exact HTTPS origin before enabling the capability there.

## Verification boundary

CI uses the real Workers/D1/SQLite runtime with simulated GitHub HTTP responses. It verifies PKCE/state/session binding, spoofed installation rejection, encrypted credential retention, broad/write permission rejection, per-read privilege revalidation, public anonymous compatibility, deletion fencing and erasure. Deterministic concurrent tests pause refresh responses and cover one-request rotation, uncertain outcomes without replay, expired leases, reconnect supersession, unlink during refresh and persistent removal/rename fences.

Hosted verification remains non-destructive. It checks the private-source static module and confirms unauthenticated status/start/unlink/setup/callback requests are rejected. It does not create a GitHub installation, retain an owner credential or read a private repository.

Remaining external acceptance is therefore:

1. Create/configure the real staging GitHub App with the exact URLs, registration options and minimum permissions above.
2. Save the staging client ID/slug/secret in the protected GitHub environment.
3. Deploy the reviewed main revision through the normal protected deployment workflow.
4. As the invited owner, install the app for one selected private repository and complete the GitHub user OAuth flow.
5. Verify the workspace status shows only the intended repository and no credential material.
6. Configure an Advanced source profile for a harmless path and verify one read-only source observation. Do not enable paid Advanced execution merely to prove repository access.
7. Remove/revoke access and verify the next check fails closed, then reconnect if the staging test should continue.

Do not treat CI fixtures, an installation redirect, a stored installation ID or a GitHub screenshot as private-source acceptance evidence.
