# Release gate checklist

This checklist is intentionally ordered. Do not skip ahead by enabling a feature whose evidence gate is still open.

1. Merge-only staging provenance is green.
2. Latest `main` passes production and full high-severity dependency audits plus `npm run verify`.
3. Staging reports the exact merged SHA and all hosted smoke checks pass.
4. Owner OIDC completion is exercised by the invited owner in the hosted application.
5. At least one provider application is configured and completes real OAuth in the same owner session.
6. The owner approves one exact controlled publication; the receipt records exactly one provider write and a separate matching readback.
7. Recovery owner UI can inspect state; a deliberate non-production PITR rehearsal completes prepare, execute, reconcile, resume and optional undo without replaying external effects.
8. Native WebMCP is accepted in a browser that actually implements it by executing a read-only hosted operation.
9. Stripe sandbox lifecycle evidence passes before `ADVANCED_ENABLED` can change to true; machine-payment evidence passes separately before `MPP_ENABLED` can change to true.
10. Account erasure, key rotation, alerts, capacity/retention and custom-domain/WAF acceptance are complete before public signup or production deployment.

The current release must remain `restricted staging` until every gate whose release effect says `blocked` in `docs/release-readiness.md` has real evidence.
