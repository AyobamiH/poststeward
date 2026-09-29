# PostSteward convergence implementation plan

**Purpose:** converge the proven `post-once-bootstrap` runtime and the existing hosted
PostSteward product into the final architecture in
[ARCHITECTURE.md](ARCHITECTURE.md).

**Execution rule:** work may proceed without repeated owner approval, but every
consequential external effect still requires the product's own explicit authority
boundary. Do not manufacture provider posts merely to prove code.

Engineering rationale from OpenClaw, Kubernetes Leases, generation/epoch fencing and
TUF-informed update metadata is recorded in [ENGINEERING_INSIGHTS.md](ENGINEERING_INSIGHTS.md).

## Phase 0 — architecture freeze

- [x] Declare PostSteward as the only public product identity.
- [x] Keep the owner's original `post-once` independent.
- [x] Define cloud/local roles.
- [x] Define the one-executor invariant.
- [x] Define hosted provider relay instead of copied OAuth secrets.
- [x] Document repository roles and anti-confusion rules.

## Phase 1 — OpenClaw-style distribution surface

- [x] Public `https://poststeward.com/install.sh` asset in the product repository.
- [x] User-local no-root installer.
- [x] `--no-onboard`, `--dry-run`, exact ref/version support.
- [x] Post-install client verification.
- [x] First-class Install page and homepage command.
- [x] Replace the temporary thin-client-only install payload with the full local runtime.
- [x] Stable/beta channel manifest.
- [ ] Exact runtime provenance in install receipt.
- [ ] Clean-machine install acceptance from the production domain.
- [ ] PowerShell/native Windows path after macOS/Linux/WSL acceptance.

## Phase 2 — adopt the A-K local runtime into PostSteward

- [x] Vendor one reviewed runtime snapshot into `AyobamiH/poststeward/runtime`.
- [x] Preserve source provenance and exact source SHA.
- [x] Rebrand public paths/command/service namespace to `poststeward`.
- [x] Keep historical internal `ocpf_post` compatibility behind the product boundary.
- [x] Move A-K acceptance tests needed to protect Setup/Recovery invariants.
- [ ] Add PostSteward CI job for the embedded runtime on Python 3.10/3.12.
- [x] Make installer stage the full runtime and canonical `poststeward` wrapper.
- [x] Make `poststeward setup/status/health/capabilities/... ` route to local runtime.

## Phase 3 — machine pairing

- [x] Add D1 machine/pairing/executor schema.
- [x] `poststeward onboard` starts a pairing request and opens a short verification URL.
- [x] Owner browser approves exact installation identity.
- [x] Local CLI polls using a one-time secret and receives a scoped runtime token once.
- [x] Store runtime token 0600; never print it after provisioning.
- [x] List/revoke paired installations from owner workspace.
- [x] Pairing expiry/replay/cross-workspace tests.

## Phase 4 — provider onboarding convergence

- [x] Hosted PostSteward already implements X OAuth.
- [x] Hosted PostSteward already implements Threads OAuth and long-lived refresh.
- [x] Hosted PostSteward already implements LinkedIn member + organization/Page OAuth paths.
- [ ] Pairing flow syncs verified non-secret provider/account bindings to local runtime.
- [ ] Fresh local verification accepts hosted provider readiness evidence.
- [ ] Owner UI presents X/Threads/LinkedIn consistently during first setup.
- [ ] LinkedIn member vs Page selection remains explicit.
- [ ] Local setup reaches `verification_ready` without copying provider credentials.

## Phase 5 — executor lease and one-writer fencing

- [x] Workspace executor record: hosted/local, active installation, generation, status.
- [x] Owner-reviewed transition API.
- [x] Hosted scheduling/publish paths fail closed for locally owned authority.
- [x] Local activation requires matching cloud executor generation.
- [x] Lease heartbeat/expiry closes future provider relay effects.
- [ ] Deactivation closes cloud relay fence before local cleanup.
- [ ] Recovery/migration advance generation only after review.
- [x] Concurrency and stale-generation adversarial tests.

## Phase 6 — provider relay

- [x] Dedicated runtime-effect API; do not overload ordinary agent `publish_now`.
- [x] Input: installation, generation, exact account alias/provider, effect id,
      publication payload hash/text, reply/thread metadata where required.
- [x] Server revalidates executor lease and stable provider identity.
- [x] Use existing PostSteward provider adapters and credential custody.
- [x] Exact effect-id idempotency.
- [x] Ambiguous/partial effect preservation.
- [x] Readback endpoint/projection without provider rewrite.
- [x] Hosted relay receipt mirrored to local receipt/event history.
- [x] No hosted scheduler authority implied by relay availability.

## Phase 7 — runtime/hosted operation convergence

- [x] Local projects/campaigns/schedules remain local execution truth in local mode.
- [x] Cloud stores coordination/receipt mirror, not a divergent campaign planner.
- [ ] Remote agents can inspect/control the active local runtime through a bounded bridge.
- [x] Local agent surface remains deterministic JSON.
- [ ] Remote MCP distinguishes local-executor operations from hosted mode.
- [x] Agent grant scope cannot change executor/admin authority.

## Phase 8 — newer owner-runtime feature review

The owner's original `post-once` continued evolving after the 0.28.29 fork.
Do not bulk-merge it.

Review and deliberately port useful changes such as:

- reply Work / reviewed reply-candidate handoff;
- replenisher improvements;
- admission/scoped-admission improvements;
- source-pipeline/vault-sync improvements;
- operating-cycle improvements;
- later bug fixes/security fixes.

For every port:

- [ ] identify exact source commits;
- [ ] compare against standalone divergence;
- [ ] retain PostSteward isolation/authority rules;
- [ ] add regression tests;
- [ ] prove no owner-specific identities/defaults leak into Fresh installs.

## Phase 9 — installation and lifecycle UX

- [ ] Installer launches guided onboarding by default.
- [ ] `poststeward configure` resumes/changes setup later.
- [ ] `poststeward doctor` proves host/runtime/cloud/provider capability layers.
- [ ] `poststeward update --channel stable|beta`.
- [ ] `poststeward runtime status` shows exact local/cloud compatibility.
- [ ] `poststeward deactivate` closes local + cloud fences.
- [ ] migration/recovery surfaces retain preview/review/apply semantics.

## Phase 10 — acceptance before public beta

Automated:

- [x] PostSteward TypeScript verify.
- [ ] embedded-runtime Python test matrix.
- [ ] installer dry-run/exact-version/idempotency/collision tests.
- [x] pairing replay/expiry/cross-workspace tests.
- [x] executor stale-generation/concurrency tests.
- [x] provider-relay idempotency/ambiguous-effect tests.
- [ ] original Post-Once non-mutation assertion.

Real controlled acceptance:

- [ ] fresh clean-machine public-domain install;
- [ ] machine pairing;
- [ ] X OAuth identity + one controlled effect/readback;
- [ ] Threads OAuth identity + one controlled effect/readback;
- [ ] LinkedIn selected member/Page OAuth + one controlled effect/readback;
- [ ] local schedule -> relay -> matching local/cloud receipt;
- [ ] agent operation through local interface;
- [ ] remote MCP inspection/control under scoped token;
- [ ] deactivation closes future effects;
- [ ] upgrade/rollback;
- [ ] migration/recovery drill;
- [ ] no second executor can write during the same generation.

## Phase 11 — launch

- [ ] production `poststeward.com/install.sh`;
- [ ] public signup mode intentionally enabled;
- [ ] stable release manifest pinned;
- [ ] install docs/support/privacy/terms reflect local runtime;
- [ ] status page reports cloud and release channel;
- [ ] beta feedback/incident path;
- [ ] publish launch only after external acceptance evidence is recorded.

## Current working branch

Implementation currently proceeds on:

```text
AyobamiH/poststeward
feat/installable-poststeward-runtime
```

Do not merge merely because individual commits are green. Merge only an exact reviewed
head after the integrated CI/architecture acceptance is clean.