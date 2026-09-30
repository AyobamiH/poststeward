# Bounded public launch — 30 September 2026

The owner explicitly requested **Open public signup** on 30 September 2026 after the full convergence deployed successfully to production. This reviewed change opens Google sign-in to verified-email owners while retaining the implemented atomic bounds: 100 admitted workspaces total and 10 new workspaces per hour. Existing owners remain able to sign in when those limits are reached. Deleted workspaces retain tombstones; a later legitimate sign-in creates a new identity.

## Existing evidence accepted

- Production edge: [accepted evidence](production-edge-live-evidence-2026-09-19.md).
- Operator alerts: [accepted evidence](operational-alert-live-evidence-2026-09-19.md).
- Capacity and cost: [accepted first-100 evidence](capacity-cost-live-evidence-2026-09-19.md).
- Public operator drill: [successful run 35440165248](https://github.com/AyobamiH/poststeward/actions/runs/35440165248) on 19 September; [closed assigned issue 137](https://github.com/AyobamiH/poststeward/issues/137) independently read back with support intake, abuse escalation, privacy requests, incident command, provider outages and emergency publishing pause. This evidence already exists and is not replayed.
- Converged production release `ed06af5515ee447fa6192a53059ffef7987b1d70`: [deployment 36708071169](https://github.com/AyobamiH/poststeward/actions/runs/36708071169) passed; independent health, readiness, installer, metadata and unauthenticated runtime-boundary checks passed. Public distribution [36708488826](https://github.com/AyobamiH/poststeward-showcase/actions/runs/36708488826) and a fresh isolated Linux installation also passed.

`production_ready` records this reviewed admission decision and existing evidence. It does **not** claim a newly completed public-customer Google consent journey, local-machine pairing, a local provider publication or a lifecycle drill. Real distributed effect and lifecycle/platform acceptance remain as defined in [delivery milestones](DISTRIBUTED_DELIVERY_MILESTONES.md).

## Product and operational boundary

The website links directly to `https://app.poststeward.com/auth/login` and the real owner workspace. Production pages omit environment badges and internal rollout banners; staging keeps its environment indication. An optional static demo remains clearly identified as sample data and never collects credentials or performs effects.

Advanced subscriptions, MPP and Stripe sandbox stay disabled. LinkedIn software remains available but provider application approval/configuration and member readback permissions remain external. X and Threads use the configured hosted provider applications. Public admission does not grant provider access or publishing consent.

For support and abuse reports use the published [support route](https://app.poststeward.com/support). Consequential incidents retain their receipts and may be paused using existing owner controls. To stop new admission, a reviewed production request sets both source-controlled signup fields back to `restricted`; existing owner access and data remain intact. Emergency publication pause remains separate.

The protected production credential mode currently records `shared_staging_bootstrap`; credential isolation/rotation remains operational debt and is not falsely reported as complete by this launch.
