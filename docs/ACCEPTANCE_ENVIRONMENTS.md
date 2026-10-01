# Real-account acceptance environments

Run this after the model-routing delivery. The release under test is production
`2962f2ef944c4de35c119e662194d489a7d54a2c`, runtime tree
`a16fb14913e2220d74f79cf7942ec3911c02683775003d30ffd98d8737615849`.
Harness revision is recorded separately; never substitute a branch name for the
release users receive. Production remains available; original Post-Once is independent.

## What is prepared and what is not

`/workspace/acceptance/poststeward-2962f2e` is a private local acceptance bundle,
not an authenticated cloud workspace. The actual Google owner session, disposable
workspace, acceptance agent, customer model account and destinations have not been
provided. No AI expenditure is authorized. Four existing authored fixtures are reused:
feature, bug_fix, maintenance and ambiguous. They are test material, not product claims.
Human scores are empty; reference drafts and agent judgement are not human assessment.

The private fixture repository provisioning attempt is blocked by GitHub HTTP 403
`Resource not accessible by integration`. Its write-ahead journal remains preserved;
remote creation is not reported successful. A credential able to create a private
repository and write its contents/releases is needed. Do not broaden the production
GitHub App merely to provision fixture files. If an owner creates the repository
separately, grant the product GitHub App read-only Contents access to that one fixture
repository before selecting private sources. Confirm the failed resource state before
removing its checkpoint or trying a new fixture name.

The matrix in `plan.json` covers both routes and all four release cases, human review,
five native lifecycle runners, three persistent-host power/logout boundaries, and every
Mac/WSL × model-route × social-provider combination. Status vocabulary is exactly
`not provisioned`, `ready`, `running`, `passed`, `failed`, `blocked`. A row becomes
passed only after actual execution and reviewed evidence. Metadata readiness and a
successful API response are insufficient proof of charge attribution or publication.

## Initialize and free preflight

Requirements: Node 24+, curl with HTTPS/TLS validation, GitHub CLI only for optional
private source provisioning, and a private writable directory outside the repository.
No model key is needed for public preflight.

```bash
node scripts/acceptance/environment.mjs init /PRIVATE/acceptance --release 2962f2ef944c4de35c119e662194d489a7d54a2c
node scripts/acceptance/environment.mjs preflight /PRIVATE/acceptance
node scripts/acceptance/environment.mjs cleanup-plan /PRIVATE/acceptance
```

Initialization preserves existing bundles/checkpoints. Directories are 0700 and files
0600. Preflight checks live health and stable release/tree identity without inference.
The CLI holds an exclusive bundle lock; after a process crash inspect its checkpoint
before manually removing an abandoned lock. There is no blind paid mutation replay.

To provision a new private fixture repository under your authenticated GitHub owner:

```bash
node scripts/acceptance/provision-sources.mjs /PRIVATE/acceptance poststeward-acceptance-YOUR_UNIQUE_LABEL
```

This creates only a new private repository, four exact commits/tags and published
private fixture releases, with `release-evidence.md`. It never touches an existing
repository or contacts human reviewers. Each mutation is checkpointed. A failed
provisioning attempt must be inspected, not automatically repeated. Cleanup requires
owner review of the exact disposable repository and retained evidence; the helper
never deletes repositories or shared resources automatically.

## Owner and customer model account setup

1. Sign in with a designated real Google owner at https://app.poststeward.com/auth/login.
   Create an isolated workspace through the normal signup flow (existing first-100 and
   hourly limits apply). Record its exact UUID in private `plan.json.workspaceId`, set
   `disposableWorkspace:true`, and bind only designated acceptance destinations/project.
   Do not forge a login, reuse unrelated production schedules, or bypass tenant admission.
2. Use a genuine customer-owned Cloudflare account distinct from PostSteward hosting.
   Record only a non-secret account label and `customerAccountAttestedDistinct:true` in
   the plan. Enter inference/inspection tokens in protected owner settings; never in the
   plan, chat, screenshots, fixtures or logs. Account fingerprints identify evidence.
3. Configure Workers AI first. Workers AI Read on that customer's account is needed for
   catalogue/inference. For prepaid OpenAI, use a dedicated key-free AI Gateway, Gateway
   Run inference token and optional separate Gateway Read/billing inspection token. Run
   permissions are account-wide, not single-gateway; a dedicated account reduces scope.
   Require a positive gateway-wide cost spend rule and no automatic retries. See
   [MODEL_ROUTING.md](MODEL_ROUTING.md). Owners configure existing funds themselves:
   this setup never purchases credits, changes top-ups or existing billing arrangements.
4. **Set an explicit total USD AI test allowance and request count with the owner before
   generating.** Enter the approved amount/count/time in private plan spending fields;
   leave them at zero without approval. Match server maxJobs/day and job/day cost ceilings
   to the test allowance, with no extra jobs/grants in this workspace. Budgets are
   conservative estimates, not guaranteed provider invoice stops; shared-account calls
   and eventual gateway enforcement can overshoot. GBP subscription price is separate.
5. Save metadata-only validation for each chosen route. Confirm model, account fingerprint,
   configuration version, gateway mode, key precedence, positive shared account balance,
   reviewed price date and `inference unverified`. Do not interpret this as a paid test.
6. Issue a separate short-lived acceptance agent with `read` only for collection. Add
   `campaign:write` and explicitly enable model spending only if a public-source automated
   generation exercise is authorized. No `admin`, billing, publish or schedule is needed
   by this harness. Set its token through your protected environment's secret manager as
   `POSTSTEWARD_ACCEPTANCE_AGENT_TOKEN`; do not paste it into shell history.
7. Run private preflight. It checks token/workspace identity and metadata; no model call.
   Model readiness does not imply human review or provider-journey readiness.

## Actual generation through existing product authority

Private source disclosure is owner-only in PostSteward. For the private fixtures,
use the signed-in preparation UI. Select the fixture project/repository, the published
`acceptance-feature`, `acceptance-bug_fix`, `acceptance-maintenance` or
`acceptance-ambiguous` tag and `release-evidence.md`. Approve private disclosure and the
visible bounded paid request. Use fixture audience/objective from `fixtures.json`;
product context must say synthetic acceptance material, never a deployed capability.
Repeat deliberately for both selected model routes within the approved allowance.

The agent harness **cannot** bypass that owner-only disclosure boundary. For an
explicitly designated public fixture source only, set `sourcePrivacy:public` and use:

```bash
node scripts/acceptance/environment.mjs generate /PRIVATE/acceptance --case feature --route cloudflare_workers
node scripts/acceptance/environment.mjs generate /PRIVATE/acceptance --case feature --route cloudflare_gateway
```

Each invocation rechecks the exact deployed release, workspace and route; requires
explicit approved allowance, owner-delegated spend and current server limits; persists
its idempotency key/maximum test allowance before its single preparation allocation.
It uses the existing preparation operation, never a direct paid model call. Unknown
allocation outcomes retain allowance and block further automatic requests. Inspect the
stored key/job through product history; never mint a new key to force completion.

Read status in the owner UI. Collect its actual result (including an owner-created job):

```bash
node scripts/acceptance/environment.mjs collect /PRIVATE/acceptance --job RETURNED_JOB_ID
```

Files `campaign-CASE-ROUTE.json` contain pinned sources, strategy, exact source references,
drafts, checks, route/configuration, attempts, reported usage, reserved/settled/uncertain
allowance and owner digest/campaign when available. No credentials or public effects
are created. Reconcile account/gateway fingerprints and provider `Unified` attribution
with the customer's protected usage/billing dashboard. Capture permitted redacted
readback evidence; do not relabel estimated USD as an invoice. An ambiguous fixture
should ask for specific context rather than invent changes. Unsupported assertion or
malformed output blocks approval; failed generation is evidence, not a successful sample.

## Genuine human assessment

Give only the designated owner/reviewer the private source fixture and actual
`campaign-CASE-ROUTE.json`, together with audience, objective and exact drafts. The
initial `review-pack.md` describes missing outputs; the collected JSON is the real pack.
Use the deterministic comparator: `Development update for REPOSITORY: commit PINNED_SHA.
Review RELEASE_URL.` Substitute actual pinned evidence; do not call an authored reference
or comparator model-generated. Review useful feature, bug fix, maintenance and ambiguous
cases for both routes. Do not contact reviewers without separate instruction.

Record scores in `human-scores.csv`. A real human records reviewer label, yes human
attestation, ISO timestamp, each 1–5 score, actual editing minutes, publishable decision,
corrections and baseline comparison. Scale: 1 unusable/inaccurate, 2 substantial rewrite,
3 usable after material edits, 4 clear with minor edits, 5 publishable as written.

Assess factual/source accuracy, audience relevance/clarity, originality/usefulness,
brand voice/channel fit, strategy/CTA and editing needed. Any unsupported factual claim
or private-data disclosure is a failed quality item regardless of other scores. Proposed
launch threshold: factual score 5, other scores >=4, <=10 editing minutes and actual human
publishability approval. Ambiguous-source success means an honest request for context,
not invented publication. Thresholds are acceptance criteria, not fabricated scores;
the owner can deliberately review them before the study. An LLM score never completes
these rows. Keep blank/unavailable human outcomes pending.

## Real native environments and full journey

The existing native workflow is reused: macOS 15/26 Apple Silicon + Intel and Windows
2025 running real WSL 2 Ubuntu 24.04. It installs the exact production release, separately
records the harness SHA, OS/kernel/Python/architecture, tests guarded install/lifecycle,
upgrade/rollback/failure repair/uninstall and retains native logs for seven days.
It contains no customer credentials and deliberately remains unpaired.

```bash
gh workflow run platform-acceptance.yml --repo AyobamiH/poststeward --ref HARNESS_REF -f release_revision=2962f2ef944c4de35c119e662194d489a7d54a2c
```

Native guarded lifecycle is a distinct claim from owner/provider acceptance. Temporary
CI hosts cannot prove persistent logout/reboot/host-shutdown behavior or provide a
reviewed interactive owner account. Use authorized physical/remote Mac hosts of both
architectures and an actual Windows WSL 2 host for the complete journey. WSL uses its
Linux filesystem and reachable user systemd; native PowerShell is not claimed supported.
macOS user LaunchAgents stop on logout; WSL/Windows shutdown and host sleep interrupt
operation. Do not promise waking or an always-on publisher.

On each host: retrieve `https://poststeward.com/install.sh` over HTTPS, inspect it, install
with `--version` the exact SHA, record install receipt and `poststeward doctor --json`.
Run normal loopback setup/pairing; signed-in owner approves the displayed installation
and generation. Configure and activate only this acceptance runtime, review the hosted→local
executor handoff, prepare actual model copy, complete human review, owner approve its
exact digest, download the approved private export and use the existing reviewed local
`poststeward preparation import`. Review project/account/variant mapping and local schedule
proposal before activation. Preserve cloud and local authority/receipt generations.

**Public canary publication requires separately approved exact destination AND copy.**
Prepare the immutable campaign, account identity, exact parts/digests and schedule time
first. The existing owner UI approves agent-created deliveries; this harness contains
no publish/schedule/approve/payment operation. Setup permission is not public-post consent.
After an approved exercise, collect the explicit receipt with `collect --receipt ID`,
plus local receipt/doctor evidence and independently observed provider content/identity.
A creation ID alone is not verified readback. Never retry an ambiguous effect.

Provider matrix: X member and Threads member need designated owner OAuth destinations;
LinkedIn member readback needs restricted permission, and Page publication requires
approved Community Management app/organization authority. Current production LinkedIn
OAuth is unavailable. Keep Page/member rows blocked until actual external permissions
and independently observed results exist; manual readback is labelled manual.

## Isolated failure fixtures and cleanup

Automated fixture proofs already cover funding/key conflicts, revoked authority,
unavailable models, invalid output, parallel budgets, explicit/prohibited fallback,
cancellation and uncertainty. These are not live failure exercises. Prepare dedicated
customer-only tokens/gateway configurations for live negative checks; never revoke shared
keys or drain credits to manufacture failure. Missing/zero credit or scope inspections
can be metadata-only. Revocation of an acceptance-only token requires owner review and
restoration; an uncertain request is not intentionally charged without the test allowance.

Use `record --id MATRIX_ID --status STATUS --evidence PRIVATE_PATH_OR_RUN_URL` for actual
checkpoints. A passed row additionally requires `--executed --reviewed-evidence`; these
are operator attestations and do not create independent verification. Preserve the
private plan, actual outputs, sources, scores and local/cloud receipts. Follow its
cleanup checklist through existing owner controls, revoking only acceptance grants and
connections, releasing unclaimed jobs and retaining uncertain charges/immutable evidence.
Do not delete files/ledgers to make a retry pass, and do not alter unrelated schedules.
