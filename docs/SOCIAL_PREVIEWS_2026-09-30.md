# Social-link preview release — 30 September 2026

The public homepage advertised an SVG as its social image. Most other public pages had a description/canonical but no Open Graph or X card metadata. The old card clipped its evidence diagram and advertised unconditional LinkedIn support. Fresh probes returned 200, valid TLS and no challenge for six crawler user agents on all inspected public paths. These observations identify website defects, but do not independently prove the cause of the reported LinkedIn rejection: its inspector is inaccessible from this environment.

## Implementation

- Accurate page-specific title, description, canonical, Open Graph and X card tags in the initial HTML, without scripts/cookies/authentication. Eight website routes and fifteen application entry points have fixed copy. Query parameters, host input, workspace data and session data never populate metadata.
- New reviewed branded PNG at `/social/poststeward-v2.png`: 1200×630, about 583 KiB, absolute HTTPS image/secure URL, correct MIME and dimensions, descriptive alt text. The generic brand card includes no provider, pricing or private-workspace claim. Obsolete SVG graphics are removed; legacy image URLs redirect to the PNG. The application retains its legacy raster URL with the new branding.
- Public inert demo is explicitly labelled and `noindex,nofollow`, while its safe metadata can be fetched. Real owner routes retain robots exclusions, noindex and authenticated APIs. Authentication callbacks, payment/API/machine responses are not converted into share pages. No authentication or WAF control is disabled.
- HTTP apex/www aliases previously returned 200 without upgrading. A minimal asset Worker now issues HTTPS redirects and streams every HTTPS response unchanged. Existing application HTTP denial is retained; no WAF rule is relaxed.
- Newly versioned image URL avoids reuse of the old SVG image cache. Normal website revalidation and application HTML `no-store` remain. A platform's cached page/failed preview still needs its own supported inspection/refresh; site deployment cannot purge third-party caches.
- Regression checks cover all maintained routes, early initial-HTML tags, duplicates, exact identities, PNG magic/dimensions/size, secret canaries, invalid origins, image retrieval, redirect destinations and failure responses. Existing browser/effect guards remain intact.

## Sources and verification limits

Retrieved current official Open Graph source from `facebook/open-graph-protocol` (the source for ogp.me), including `og:title`, `og:type`, `og:image`, `og:url`, description/site/locale and image MIME/dimensions/alt. Retrieved current Cloudflare static asset headers/redirects docs. X's current public official docs repository was inspected; it covers canonical webpage properties, but the legacy card-format reference could not be retrieved here. URLs and retrieval outcomes are recorded in the workspace research evidence.

Official references attempted: LinkedIn sharing help and Post Inspector, Meta Sharing/Webmasters/Crawler/Debugger, X large-image card/troubleshooting/validator, Threads docs, Slack unfurling/robots and Bluesky external-card docs. The environment proxy returned CONNECT 403. A network permission request was granted, but the proxy still blocked these hosts. Actual Chromium attempts also returned `ERR_TUNNEL_CONNECTION_FAILED` before reaching LinkedIn, Facebook, X, Threads, WhatsApp, Slack or Bluesky. These are environment access failures, not evidence of a platform outage or website WAF block. The affected current platform-doc checks and real platform previews are **unverified**. No test post, external message, consent, payment or private-workspace action is used to obtain a preview.

Crawler-user-agent probes and a locally rendered expected-card fixture establish website delivery/presentation only, not actual platform ingestion, caching, crop or UI. No undocumented global Threads refresh or X preview guarantee is claimed.

## Refresh and rollback

When inspector access is available, inspect the exact canonical URL in LinkedIn Post Inspector and Meta Sharing Debugger; use their supported re-inspection controls and record actual output. Check X card diagnostics and an unpublished composer only if an authenticated session and that tool are available. Threads/chat previews require the corresponding supported client flow; do not publish/send a test message without authorisation. Record desktop/mobile output separately. Do not repeatedly change canonical URLs or attach arbitrary cache-busting query strings.

Deploy additive assets and metadata using the existing protected workflows. Keep the current service serving throughout. No data migration or provider mutation is required. If a regression appears, revert this preview change and redeploy a reviewed exact revision; do not disable authentication or change signup/billing/executor policy. Refresh branded release metadata after the application deployment using the existing distribution workflow.

Workspace delivery artefacts will contain the exact commits, successful workflows, continuous health probes, crawler responses, page/platform verification CSV/Markdown, preview fixture screenshots and remaining unverified external checks. Do not represent this document's implementation plan as live deployment proof.
