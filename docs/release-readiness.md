# PostSteward release readiness

This matrix is the canonical release-gate view. It distinguishes implementation from real external acceptance.

| Gate | Engineering state | Staging state | External acceptance | Release effect |
| --- | --- | --- | --- | --- |
| Core Worker/D1/Durable Objects | implemented | deployed | not applicable | satisfied for restricted staging |
| Owner Google OIDC | implemented | configured | real owner completion still required | public release blocked until accepted |
| Controlled publication | implemented | deployed | one real provider write + separate readback still required | public release blocked until accepted |
| Provider OAuth/refresh | implemented | provider apps not configured | provider app approval + consent required | OAuth buttons stay disabled per provider |
| LinkedIn member readback | implemented/capability-gated | disabled | provider approval required | LinkedIn excluded from controlled acceptance while disabled |
| External-effect ledger | implemented | deployed | non-destructive hosted checks complete | satisfied for restricted staging |
| Workspace PITR recovery | implemented | deployed | one deliberate owner rehearsal still required | public release blocked until rehearsed |
| Dependency high-severity audit | enforced | clean at latest verified merge | not applicable | satisfied |
| Native browser WebMCP | implemented | hosted JS available | supported-browser authenticated execution required | public release blocked until accepted |
| Advanced billing | implemented foundation | disabled | Stripe sandbox lifecycle required | purchases disabled |
| Machine payments | implemented foundation | disabled | merchant/wallet eligibility and settlement required | disabled |
| Public signup | implemented switch | restricted | abuse/support readiness required | disabled |
| Production deployment | deployment path exists | unconfigured | custom domain/WAF/alerts/capacity/erasure/key-rotation evidence required | disabled |
| GitHub merge governance | CI + deploy provenance gate | in repository | account-level branch rule still required | automatic staging protected from ordinary direct-push authority; repository itself not fully protected |

A row may move to `satisfied` only when its stated evidence exists. Configuration values, screenshots, mocks or green unit tests cannot substitute for real provider, payment, recovery or browser evidence where the row requires it.
