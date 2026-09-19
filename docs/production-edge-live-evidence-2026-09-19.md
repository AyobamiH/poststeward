# Production edge live evidence — 19 September 2026

Production edge acceptance is bound to workflow run `35440149519` on release `1f0831a912177e37e6997a2539f1b7a30fe75563`.

The protected production workflow verified the Cloudflare API token, exact `poststeward.com` zone and WAF read authority before mutation. It then reconciled only the reviewed PostSteward rules and independently read them back.

Observed evidence:

- production origin: `https://app.poststeward.com`;
- Worker: `poststeward`;
- Cloudflare authority preflight: `ready=true`;
- canonical WAF state before/after: `ready`;
- canonical authentication rate-limit state before/after: `ready`;
- reconciliation required no drift mutation on this run;
- independent edge verifier: `ready=true`;
- active rate-limit rules read back: 1;
- environment read back as `production`.

The Worker custom domain is managed by the reviewed production deployment and was independently verified through the authoritative Worker custom-domain path introduced by PR #131. Security headers and exact hosted release are part of the same read-only edge verifier.

This gate does not assert public signup. Admission remains a separate launch-policy decision.
