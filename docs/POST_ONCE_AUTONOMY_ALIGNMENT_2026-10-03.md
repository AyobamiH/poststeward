# Autonomous publishing grounded in Post-Once

Post-Once frees the owner's hands because the standing editorial, source, account
and scheduling permissions are established upstream. Eligible supply then flows
through the existing allocator and publisher. Requiring approval of every routine
delivery in PostSteward defeats that behaviour.

This change preserves both hosted delivery and the local runtime. It introduces no
second publisher and copies no personal account, project, vault or credential setup.

## Owner setup and agent use

1. Connect the destination accounts and bind them to a project.
2. Connect this workspace's own model and save its call, token and spending limits.
3. In AI preparation, select project documentation (repository branch/ref and up to three paths) or a release snapshot, audience,
   product context, voice, exclusions and call to action.
4. Choose **Run this project autonomously**, set spacing, stock and daily posting
   limits, accept standing authority and start. Hosted operation needs no local
   installation. Advanced entitlement is required; it does not confer authority.
5. Inspect stock and holds under Autonomous projects. Pause revokes further writes.

HTTP and MCP expose the same `autonomy_configure`, `autonomy_list`,
`autonomy_request` and `autonomy_pause` contract. Only the signed-in owner can
configure or pause standing authority. A `campaign:write` agent may request a real
stock deficit using the already selected sources/context; model settings must also
allow agent spending. Concurrent requests reuse one durable request. Agents cannot
replace the editorial context or authorise themselves by connecting a source.

## Hosted execution

The bounded controller senses stock per destination, queues the existing preparation
pipeline, reads pinned evidence, interprets useful audience problems, drafts distinct
copy, checks evidence references and editorial issues, admits an immutable campaign
and reserves the existing publisher. Unchanged sources can support distinct evergreen
explanations; no commit template invents news. Previous campaign copy is supplied as
novelty context and a deterministic similarity check rejects repeated supply.

Admission requires the exact checked digest, clean critique, complete strategy,
source-referenced claims, unchanged project/account bindings, current selected source
content and current model/owner authority. It captures policy revision and admission
digest on each delivery. These are rechecked before reservation and provider writes.
Advisory coverage warnings, such as a missing previous release comparison, do not require routine approval when admitted claims are supported. Empty or excluded evidence and essential missing context still hold work.

Standing authority never releases arbitrary agent copy or legacy pending approvals.
Existing owner review and template automation remain available.

Spacing and per-destination daily limits include non-cancelled reservations and
uncertain effects. Allocation searches at most eight UTC days. Model limits remain
the existing model subsystem's independent budget. Unstarted daily budget exhaustion
waits for reset; uncertain calls are held without automatic retry. Editorial issues,
source/account drift and duplicate copy hold the affected producer. Already eligible
scheduled stock can continue through its own current authority checks.

The existing scheduler provides identity checks, durable provider IDs, independent
readback where supported and no replay of uncertain writes or completed thread parts.
After consumption, the controller fills stock again. Each alarm handles one producer
and the existing bounded publishing batch. Completed autonomous preparation history
can be trimmed above 70 retained jobs only after all deliveries are terminal;
immutable campaigns, receipts and spending ledgers remain. Active admission evidence
cannot be removed through preparation archive.

Model checking assists editorial admission; it cannot prove every claim true or
promise that all generated content is good. This release's hosted producer uses the
existing GitHub reader and workspace model routes, with a bounded documentation-only repository mode that requires no release. Each cycle pins the selected branch/ref to a commit; it does not turn documentation into deployment evidence. It does
not imply a customer's ChatGPT subscription or private Google Docs authoring task is
available to a hosted workspace. Those original Work/Docs workflows remain local
capabilities; the hosted producer runs from the explicit connected model contract.

## Local adoption and lineage

Reference: Post-Once `309915fd60e92cd48c15d4a3acdbe04487232026`.
PostSteward base: `2560f5c79787f091fca4e19d531b68a61e0a10ae`.

| Adopted behaviour                              | PostSteward integration                                                                                                                       |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Bounded desired-state stock recovery           | `rolling_supply.py`, sensed account deficits, targeted vault sync, safe admission/reconciliation and the existing allocator                   |
| Account acceptance and ordered recovery demand | Updated `editorial_continuity.py`; physical accounts receive explicit coverage/blocker evidence                                               |
| Consumption-driven refill                      | `run_due_cycle.py` observes the existing scheduler results and signals the existing PostSteward refill service; the periodic timer remains    |
| Explicit bounded model fallback                | `workers_ai.py` and replenisher updates; separate inference credentials, saved opt-in policy, Work grace period and operational-buffer limits |

The unattended wrapper still checks local activation and the cloud heartbeat. The
CLI still polls the cloud bridge before running the scheduler. The new consumption
observer runs after that existing path and preserves full scheduler output. Local
service names and launcher paths use PostSteward. Standalone suppression of packaged
personal projects, source profiles and accounts is retained.

Fallback is not silently enabled. Nothing renews expired copy, increases publishing
quotas, changes account identity, replays uncertain effects or copies the original
owner's personal publishing configuration.

## Migration and release acceptance

This is additive Durable Object state, with versioned `autonomy:` policies and an
optional standing-admission field on new deliveries. Old records keep their existing
approval behaviour. Restore invalidates standing authority alongside accounts,
models and existing automation. Hosted autonomy configuration/requests are fenced
when a local executor owns publishing; the paired local runtime remains supported.

Acceptance covers repeated no-babysitting cycles, novelty, source/account drift,
owner pause, agent spending consent, concurrent requests, UTC budget reset, restore,
local supply recovery and the unchanged provider effect fence. Repository tests use
synthetic evidence, model results and provider effects. They are not live customer
onboarding or provider acceptance evidence. Production publication remains subject
to the repository's existing protected release process.
