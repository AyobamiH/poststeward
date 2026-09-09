# Provenance and product boundary

Behavioural baseline: private `AyobamiH/post-once`, revision `e1dc7da2109745285ff242fb788037450cd3dc25`, v0.5.5, reviewed on 9 September 2026. The supplied repository-derived report and the agreed hosted-product plan define this implementation's contract. No owner credentials, personal account bindings, campaign inventory or packaged source profiles are included.

| Post Once milestone | Hosted implementation |
| --- | --- |
| PR #1: explicit durable scheduling | Workspace receipts, alarms, immutable payloads and cancellation race semantics |
| PR #2 / #4: project/account registry and metrics | Workspace-isolated aliases, explicit project routing and on-demand metrics |
| PR #7 / #9: allocation and replenishment | Initial separately gated source profile and allocation engine |
| PR #12 / #13: diversity and cross-platform cooling | Initial project/family spacing; richer category balancing remains release work |
| PR #15: canonical help catalogue | Typed operation registry, generated docs, schemas and handler parity checks |
| PR #16: Threads readiness | Durable create/wait/publish stages; bounded readiness; already-published ambiguity |

This is a TypeScript reimplementation of contracts, not a copied deployment of the original Python/systemd operator. It is independent of Post Once's lifecycle and promotional campaigns. Future upstream changes require deliberate review.

Implementation references checked during this work:

- [Cloudflare Durable Object alarms](https://developers.cloudflare.com/durable-objects/api/alarms/)
- [Chrome WebMCP imperative API](https://developer.chrome.com/docs/ai/webmcp/imperative-api), including `document.modelContext` and consequential annotations.
- [Stripe MPP](https://docs.stripe.com/payments/machine/mpp), plus the pinned `mppx` SDK's actual Stripe charge implementation and `requiresAuth` support.
- [X authentication mapping](https://docs.x.com/fundamentals/authentication/guides/v2-authentication-mapping)
- [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-08)
- [Threads publishing reference](https://developers.facebook.com/documentation/threads/reference/publishing)

Current SDK versions are pinned in `package-lock.json`; generated schemas and protocol adapters must be tested before updates. Tests prove local implementation behaviour under their stated scenarios, not live provider eligibility or a hosted launch.
