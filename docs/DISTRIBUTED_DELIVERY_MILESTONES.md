# Distributed PostSteward delivery milestones

Owner instruction: set milestones, learn from mature technology companies, implement through deployment. Recorded 30 September 2026. Existing hosted acceptance and A–K adoption are the baseline; do not repeat accepted publications, payments or destructive drills merely to refresh a checklist.

| Milestone | Required result | Acceptance |
| --- | --- | --- |
| 1. Bounded remote/local bridge | Outbound machine polling; scoped HTTP/MCP inspection and explicit local schedule creation/cancellation; durable command receipts | Scope denial, grant revoke, expiry, concurrent claim, immutable completion, response loss and stale-generation tests; local tools replace hosted planner tools in MCP |
| 2. Reviewed recovery generation | Newly paired target; owner-approved exact local restore digest; atomic cloud generation/transition receipt; activation checks the receipt | Old machine cannot renew or relay after handoff; same-identity recovery rejected; changed local review rejected |
| 3. Reviewed cloud deployment | Merge exact green integration; forward D1 migrations; deploy staging then production through existing protected workflows | Exact deployed release, access boundary, migrations and read-only smoke evidence; signup stays restricted and Advanced stays disabled |
| 4. Branded distribution | Showcase publishes canonical installer and exact deployed stable metadata | Apex and www return installer/metadata; fresh isolated Linux install validates SHA/tree/provenance and targets app.poststeward.com for pairing |
| 5. Distributed live acceptance | Owner pairing/consent, reviewed local handoff, one explicitly approved scheduled provider effect and matching receipts | Real machine/provider evidence; historical hosted acceptance remains preserved; LinkedIn requires its separate approved application/permissions |
| 6. Lifecycle/platform acceptance | Actual deactivation, upgrade/rollback, migration, dead-machine recovery and stale-source drill; WSL/macOS coverage | Evidence per platform and exercise; native Windows remains a separately scoped delivery |

Deployment closes milestones 3–4, not milestones 5–6. Public signup is a separate admission decision.

## Research applied

The following source documents were retrieved from their official GitHub repositories during this implementation; direct documentation-host retrieval was unavailable in the execution environment.

- [Kubernetes leases](https://github.com/kubernetes/website/blob/main/content/en/docs/concepts/architecture/leases.md): heartbeats and leader coordination. PostSteward preserves one reviewed executor, renewable leases and monotonically increasing generations. Lease expiry does not silently enable a second scheduler. Recovery uses a new installation identity so a restored token cannot acquire the target's authority.
- [AWS Powertools idempotency](https://github.com/aws-powertools/powertools-lambda-typescript/blob/main/docs/features/idempotency.md): bind an idempotency key to a payload and persist the outcome. Bridge submission binds actor/key/input; the local journal records intent before mutation; acknowledgement can repeat but mutation cannot. An interrupted claimed command stays unknown/failed rather than being automatically redistributed.
- [Cloudflare Workers best practices](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/workers/best-practices/workers-best-practices.mdx): use bindings, bounded I/O and explicit asynchronous work. Coordination uses existing D1/Workspace bindings and an outbound polling runtime. There is no inbound laptop listener, generic shell operation or provider-secret transfer.

These are design patterns, not claims of equivalence to those platforms or proof of exactly-once delivery across arbitrary external systems.

## Bridge operation contract

The hosted operation catalogue adds `runtime_inspect`, `runtime_schedule_create`, `runtime_schedule_cancel` and `runtime_command_get`. Inspection is bounded to status, projects, campaign identities and the latest 50 schedule receipts. Scheduling uses already configured immutable local campaign content and a verified provider binding. Project configuration and owner authority remain separate.

The paired machine runs `poststeward cloud bridge` to process at most one command. The canonical `run-due` service also polls once before dispatch and renews the exact lease. Queue expiry is five minutes, with at most 100 live commands per workspace. A command captures workspace, installation, generation, original actor/grant, scope, immutable input and key. Grant validity is checked at claim, immediately before local execution, and again at the cloud provider-write boundary for agent-created schedules.

Claimed commands are never automatically retried. After an interrupted local mutation, inspect existing local state and the command receipt; do not submit new keys as blind retries. Command completion is distinct from provider publication/readback.

## Recovery review

Restore and verify locally using the existing A–K machinery. Keep automation inactive. Pair the target as a new cloud installation and run `poststeward cloud recovery-review`. The digest binds the verified local setup event history and reconciliation/recovery review files.

In the signed-in owner workspace choose migration/recovery, source cloud installation, newly paired target and that digest. The source must self-fence or its lease must expire; unresolved external effects/recovery quarantine block the transition. Exact preview/apply advances the cloud generation and records the restore digest atomically. Local activation requires that exact transition purpose and digest. A copied/restored old machine retains its old cloud identity and cannot renew the target's generation.

## Domain ownership

`poststeward.com` and `www.poststeward.com` are the public static showcase/distribution surface. `app.poststeward.com` owns authenticated pairing, owner approval, OAuth, executor coordination and provider relay. The installer downloads channel metadata from the distribution surface but writes the application origin into its launcher. Publishing runtime artifacts does not enable effectful showcase controls.
