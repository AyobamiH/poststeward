# PostSteward protected CI queue and evidence policy

## Purpose

Release confidence comes from executed checks and exact-source receipts, not time
spent waiting for a runner. This policy **does not** change GitHub main-branch
protection, owner approval boundaries, installed runtime, or production authority.

Draft PRs do not consume protected CI resources; the explicit ready_for_review event starts all required checks once the PR is ready.
The mandatory status is never considered passed while a draft PR is open.

The existing single-owner, isolated/self-hosted runner fleet remains the
execution plane. No additional paid runners or services are required.

## Source and test gates

| Change | Required evidence | Queue behaviour |
| --- | --- | --- |
| Protected main push | Full Node/dependency audit, full Python 3.12 suite, complete Python 3.10 suite, focused 3.12 packaging/authority smoke, browser | All runs retained |
| Code, workflow, installer, runtime, Cloud or generated asset PR | Same broad checks as main; owner-specific review, security and release authority remain separate | An exact same-repository, owner-authored newer PR run supersedes the previous run in that workflow only |
| Strict documentation-only PR (root README/CONTRIBUTING or docs/*.md paths) | Independently verified PR merge-parent SHA, exact changed-path allowlist, git whitespace check; no unnecessary runtime or browser | Same owner-scoped supersession rules |
| Fork, untrusted actor, non-merge checkout, failed path inspection or ambiguous path | Full source verification; never trust PR labels or changed-file claims alone | No automatic cancellation of in-progress checks |

The status name **verify** stays required by the existing active main ruleset.
It still executes the full Python 3.12 regression suite on every code-bearing
PR and protected main push. Python 3.10 is tested fully in its own isolated
job. The separately named Python 3.12 matrix job executes targeted Fresh
onboarding, Cloud and activation acceptance instead of repeating the entire
suite. Browser verification remains full for code-bearing changes.

A passing documentation-only check explicitly means **no code-bearing files
changed in the inspected, trusted merge**, not that full runtime or browser
tests ran. An uncertain comparison always selects the full profile. Do not use
this mode for a README/doc change bundled with executable files.

## Superseded work and owner guard

GitHub Actions workflow concurrency groups are scoped by PR number **and
workflow name**. Only *pending* owner-authored PR workflow checks are coalesced by
GitHub. Running work is NEVER automatically cancelled. A PR number is used
as the group key only when the PR belongs to AyobamiH, its head repository
is canonical and the triggering actor is AyobamiH; all other events use
unique run IDs. Protected main, workflow dispatches, other PRs, forks and
independent workflows retain their separate evidence. Historical exact-source receipts are not rewritten or described as
successes after cancellation.

For pre-existing queued runs (before this workflow change), the separate
queue reporter offers a narrowly scoped, *opt-in* cancellation operation.
It refuses:
- active or partially executed jobs, completed evidence, same-head checks,
  non-owner PRs, other repositories and unrelated workflows;
- an unapproved or stale protected-main revision;
- any run without fresh PR-head, run-state and queued-job evidence.

No automatic cancellation daemon, timer or new privileged agent is installed.

## Queue health and release priority

Run locally with an authenticated GitHub CLI:

```bash
node scripts/ci-queue-analysis.mjs --json > /tmp/poststeward-queue.json
```

The output includes queued/running/completed counts, per-workflow backlog,
oldest and median pending age, sampled median time to start/execute,
owner PR source drift, safe supersession candidates, and release-priority
candidates (explicit `release-critical` label or bounded CI/integration/fix
PR title). This is a **read-only report by default**.

When an owner explicitly authorises cancellation of *superseded queued
owner PR jobs*, the bounded one-time command is:

```bash
node scripts/ci-queue-analysis.mjs --apply-cancel-stale \
  --expected-main <exact-current-protected-main-40-character-sha>
```

It re-reads the protected main and each PR/run/jobs immediately before
every cancellation and changes at most five confirmed superseded queued
workflow runs. Never schedule this command unattended.

GitHub Actions does not provide a priority-sort API for a self-hosted runner
queue. The report surfaces candidate priority but **does not claim to
reorder queued jobs**. Only actual removal of obsolete runs and reduced
execution work free existing capacity. Current-head safety checks must not
be cancelled merely because a different PR is more urgent.

## Baseline and acceptance

On 13 November 2026 at 11:22 UTC, a bounded live report found 23 queued
runs; older historical observation/deploy runs substantially inflated the
overall median queued age. The sampled completed-job median waiting time was
11.13 minutes and median execution time 4.55 minutes. The report found zero
safely cancellable superseded owner PR runs; therefore no cancellation was
performed.

CI closeout requires current-source workflow acceptance: executed `verify`,
Python 3.10, focused Python 3.12 and code-bearing browser checks, plus
security/owner review and normal protected integration. Documentation-only
intent must be corroborated by exact changed paths and the recorded
classification. A green build is not an installed release or verified
customer operation: customer #197 and all separate release, provider,
crash/outage, native macOS and SLO gates remain independent.
