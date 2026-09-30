# Provider pipelines

`post-once` 0.2.1 applies the same explicit consequence contract to X, Threads and LinkedIn.

The contract remains:

`campaign -> account readback -> duplicate/effect check -> dry run -> explicit --live -> provider receipt -> readback`

There is still no scheduler, cron job or background publisher.

## X

X text creation uses `POST /2/tweets`. Copy that fits one bounded post remains one post. Longer approved copy is frozen before consequence as one logical publication containing deterministic parts; each continuation is created as a reply to the previous post using `reply.in_reply_to_tweet_id`.

The root ID remains the logical publication ID/URL. Known continuation IDs are retained in durable receipt/schedule evidence. A definite continuation rejection after earlier parts exist becomes `partial_effect`. A transport/5xx uncertainty during any continuation becomes `ambiguous_effect`. Neither state grants permission to replay the root or whole thread.

## Canonical owner credential bridge

The OneClickPostFactory product runtime already stores tenant provider credentials in Supabase `user_credentials`, encrypted with the `enc:v1:` AES-256-GCM format implemented by `social-agents/src/tenant-credentials.ts`.

For owner GTM, `post-once` can deterministically project those existing credentials into its local user-only provider files:

```bash
./scripts/import-ocpf-db-credentials
```

The bridge auto-discovers `post-once/.env` first and reads the Supabase bootstrap values from that file. It resolves one tenant, performs a read-only REST select, decrypts only in local process memory, writes provider files with private permissions, and then runs non-consequential status checks. It never writes provider credentials back to Supabase and never stores the Supabase service key or credential-encryption key in `post-once` state.

This is the preferred owner setup path. The manual provider importers remain fallback paths for standalone accounts.

## Threads

Required token scopes for publication are `threads_basic` and `threads_content_publish`.

Import from the canonical OCPF database when available:

```bash
./scripts/import-ocpf-db-credentials --provider threads
```

Standalone fallback:

```bash
./scripts/import-threads-credentials
```

Then verify the exact account:

```bash
./ocpf-post threads status
```

Dry-run OCPF-001:

```bash
./ocpf-post threads publish --campaign OCPF-001
```

Publish explicitly:

```bash
./ocpf-post threads publish --campaign OCPF-001 --live
```

Threads text creation uses Meta's `auto_publish_text=true` path on `POST /me/threads`. Copy that fits one bounded post remains one request. Longer approved copy is frozen as one logical reply chain; each continuation uses native `reply_to_id` against the immediately previous Threads post. There is no client-side draft-container handoff or fallback retry after an uncertain response. See [THREADED_PUBLICATIONS.md](THREADED_PUBLICATIONS.md) and [THREADS_TEXT_PUBLISHING.md](THREADS_TEXT_PUBLISHING.md).

When the database contains the provider's actual expiry timestamp, the bridge preserves it instead of inventing a new lifetime. Long-lived Threads tokens are refreshed near expiry using `grant_type=th_refresh_token`.

## LinkedIn

LinkedIn supports member actors plus explicitly registered `organization`/`organizationBrand` Page actors. Page comments/readback require the relevant organisation/community-management permission; access denial remains unavailable evidence rather than zero activity. See [LINKEDIN_PAGE_ACTORS.md](LINKEDIN_PAGE_ACTORS.md).


Import from the canonical OCPF database when available:

```bash
./scripts/import-ocpf-db-credentials --provider linkedin
```

Standalone fallback:

```bash
./scripts/import-linkedin-credentials
```

Then verify the exact member:

```bash
./ocpf-post linkedin status
```

When an OAuth token contains `openid profile`, `post-once` derives the author URN from live `userinfo.sub` and refuses a mismatch with any database-configured person URN.

Existing OCPF credentials may predate OpenID scope and instead contain a previously configured `linkedin_person_urn_enc`. In that case `post-once` does not blindly trust an opaque token: it requires LinkedIn token introspection using the stored client ID/secret to confirm the access token is active before using the configured member URN as the author identity.

Dry-run OCPF-001:

```bash
./ocpf-post linkedin publish --campaign OCPF-001
```

Publish explicitly:

```bash
./ocpf-post linkedin publish --campaign OCPF-001 --live
```

LinkedIn publication uses `POST https://api.linkedin.com/rest/posts`, the current Posts API rather than the retired UGC Posts write path. Requests include `X-Restli-Protocol-Version: 2.0.0` and a YYYYMM `Linkedin-Version` header (default `202608`).

The create response's `x-restli-id` becomes the durable provider receipt. Readback can require restricted `r_member_social`; when that permission is unavailable, a successful create is recorded as `published_unverified` rather than falsely claiming readback verification. Duplicate retry is still blocked.

## Consequence states

- `published_verified`: create receipt and provider readback agree.
- `published_unverified`: provider returned a durable post ID but readback could not be proven.
- `ambiguous_effect`: a live root/continuation write may have succeeded after an uncertain provider outcome. Do not blind-retry or blindly continue.
- `partial_effect`: one or more parts definitely published but a later continuation was definitely rejected. The existing parts are external effects; do not replay the logical publication.

All provider credentials stay outside the repository under the user config directory with `0700` directory / `0600` file permissions where supported.
