# Application layout audit — 30 September 2026

Scope: https://app.poststeward.com. Baseline production release: `03cf033b4412f3aa909460826c8b8fceb1d42e4e`. This change is presentation and browser navigation only. Runtime adoption, OAuth, payment configuration, publishing approval, recovery and executor authority are unchanged.

## Evidence and coverage

Fresh live public baseline: 17 routes × 6 widths (360, 390, 768, 1024, 1440, 1920) × 2 heights (600, 1080), with full-page and viewport screenshots. Menus additionally opened at short widths. Owner-state captures use the real compiled Worker with isolated, synthetic API responses, never customer accounts or posts. Capture manifests and matching image gallery are retained under `/workspace/setup-logs/app-layout/`; production verification and exact merged revision are recorded there after deployment.

| Route / implemented surface | States covered | Boundary |
|---|---|---|
| `/` | Overview, example flow, hosted/local/agent choices, install command and copy, pricing, public menu | Live signed-out and compiled Worker |
| `/docs/` | Start/onboarding guidance, menu, code, links | Live and compiled Worker |
| `/docs/install` | Installation, prerequisites, copy controls | Live and compiled Worker |
| `/docs/agent-guide` | Agent access guidance, reference table and code | Live and compiled Worker; local table scrolling is keyboard reachable |
| `/docs/operations` | Search/filter controls, operations, metadata, expanded disclosure | Live and compiled Worker |
| `/privacy`, `/terms`, `/security`, `/support`, `/status` | Trust/legal/help, menus, ledger, unavailable status | Live, compiled Worker, isolated status failure |
| `/app` | Signed out, empty/populated accounts and projects, provider availability, disconnected/disabled controls, long records, expanded manual/agent/runtime panels, approval and receipt states, filters, campaign review and validation failure | Private content is synthetic; delivery never submitted |
| `/app#publishing`, `#evidence-panel` | Project and account selection, exact reviewed copy, scheduling form, receipts and approval distinctions | Existing server contracts; synthetic campaign responses only |
| `/app#agent-access`, `#advanced`, `#runtime-settings` | Scoped grants, expiry/revocation, billing unavailable in GBP, reviewed profiles, executor and pairing settings | No token issue, checkout, runtime transition or recovery effect |
| `/advanced-inventory` | Categories, inventory, allocations, metrics unavailable, loading/errors/expiry, long identifiers | Synthetic owner reads |
| `/lifecycle` | Data/export/retention/deletion boundary, disabled controls, loading/errors/expiry | No data export, pruning or deletion submitted |
| `/recovery` | Checkpoints, current proof, approximate restore disclosure, disabled/available controls, loading/errors/expiry | No checkpoint capture or restore |
| `/pilot` | Owner/provider setup, disabled controls, loading/error/expiry | External provider review and actual publication are unverified |
| `/auth/login` | Google redirect destination inspected without finishing sign-in | External Google UI and real owner session unverified |
| `/auth/callback`, unknown route | Safe callback failure and 404 guidance | No callback URL replay |

Pricing and installation are sections of the homepage and installation guide, rather than invented independent app routes. Account management, approvals, scheduling, settings and billing are implemented within the workspace. Native details/summary controls are used for panels; the inventory has no custom tabs, tooltips or modal dialog widgets. Runtime transitions use native confirmation prompts; the managed browser's native UI is outside screenshot acceptance.

## Findings and shared fixes

| Finding | Evidence / root cause | Resolution / regression |
|---|---|---|
| F1: Installation crowded into half-width hero | 1440×1080: diagram starts at y345 against introduction y153; detached button beside 359px command panel. Centred columns and max-content button grid caused it. | Top-aligned hero. Hosted/local actions together. A full-width installation section directly below contains guide, exact command, copy, installer link and prerequisites. Diagram labelled **EXAMPLE PUBLISHING FLOW**. Exact command/copy and alignment tested. |
| F2: Menus cover their own summary | Live root and workspace at 360/390×600. Fixed top offsets did not follow wrapped headers. | Each panel anchors beneath its own summary. Height bounded to the measured header and viewport; local scrolling retains all links. Keyboard Enter, Tab through every link and Escape focus restoration tested at 600 and 225 height. |
| F3: Short desktop sidebar | 1024×600 workspace: final links below viewport. Sticky container had no scrolling. | Measured header offset, bounded sidebar scrolling and focus-reachable last task link. |
| F4: Publishing task interrupted and stretched | Populated 1024 workspace: account metadata and ten project records stretch the second pane beyond 4500px. | Top-aligned adaptive task columns; stack when space is limited. Account name, connection state and readback remain visible. Full technical metadata and existing project bindings remain in labelled disclosures. Review selectors and forms remain visible. |
| F5: Focus offsets and result overlays | Unrelated fixed 86/88/96px anchor offsets; plain result panels sticky over short screens. Interactive containers clipped descendants. | A read-only ResizeObserver measures actual header height. Shared scroll padding follows it; short screens use static headers. Results flow beside their action. Interactive cards no longer hide overflow. Hash targets, input focus and review/error results tested. |
| F6: Inconsistent hierarchy/density | Oversized docs/legal headings, repeated large padding and small form controls. | Shared 44px control height and section spacing; 16px form text, bounded docs/legal headings and readable line height. Brand, legal and provider limits retained. |
| F7: Duplicate intermediate navigation | Docs at768 show desktop links and mobile menu because thresholds differ. | One consistent navigation mode below900. |
| F8: Enlarged-text overflow | Baseline 360 doubled-text simulation overflowed on 13 routes; 1024 on root and operations. Headers could not wrap, grid minimums and long inline identifiers enlarged beyond containers. | Wrapping header groups, zero/minmax intrinsic constraints, bounded badges and identifiers. Local reference-table scrolling is labelled and keyboard focusable. No blanket page overflow hiding. Route-wide reflow/text regression added. |

The historical horizontal-scrollbar example was **not reproduced at normal sizes**: the 204 fresh normal baseline pages had no document overflow. The enlarged-text checks did reproduce independent overflow; those causes are addressed.

## Verification and limits

Run the frozen existing environment with writable Wrangler config/cache:

```sh
XDG_CONFIG_HOME=/workspace/runtime/config XDG_CACHE_HOME=/workspace/runtime/cache CLOUDFLARE_SEND_METRICS=false npm run verify
node /workspace/setup-logs/run-browser.mjs
```

The wrapper selects the installed system Chromium for the repository's Playwright harness; CI uses its locked Playwright Chromium. Six added browser checks exercise actual DOM layout and interactions: short menus; connected installation/copy; route-wide reflow and enlarged text; long owner data; sidebar/hash focus; campaign review success and long validation failure. Consequential campaign calls are explicitly intercepted with synthetic responses; delivery is never submitted. Existing approval, receipt, OAuth, CSRF, isolation and authority assertions remain intact.

Available native browser: Chromium151. Widths are desktop browser viewport simulation, not physical phones/tablets. Reduced-motion and light/dark checks use browser preferences. Axe checks cover WCAG A/AA tags, plus explicit keyboard and geometric assertions; they do not establish full accessibility conformance or screen-reader acceptance.

Native 200%/400% browser zoom is **unverified**: isolated browser-CDP extension loading returned “Loading of unpacked extensions is disabled by the administrator.” The administrator policy was preserved. Separate tests explicitly use **zoom-equivalent reflow** (640×450 and320×225 for a1280×900 window) and **doubled-text simulation** at360/1024. These are not described as native browser zoom. Firefox, Safari, physical mobile browsers and assistive technologies are unavailable in this environment. Real signed-in customer flows and external Google/provider/payment UI remain unverified.

Deployment uses the existing protected production workflow: its request comment changes; production variables, secret bindings, signup limits, Advanced entitlement/approval checks and undecided GBP price remain unchanged. Keep the production health monitor running until independent release and live layout verification finish. Refresh public-domain distribution through the existing showcase workflow to pin the exact healthy application revision.
