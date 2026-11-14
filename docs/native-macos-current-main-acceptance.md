# Opt-in native macOS acceptance on protected current main

The native `launchd` lifecycle and real installer/rollback tests require Darwin
Apple Silicon. Linux simulation and static CircleCI syntax checks are not proof.
The existing CircleCI `m4pro.medium` runner provides the native environment;
this change does not register a new runner or enable automatic paid main jobs.

## Exact-source dispatch contract

After the source is **merged** through protected main and owner approval is
recorded, first read the current GitHub `main` SHA and obtain specific approval
for **one** existing-resource CircleCI macOS acceptance run at that exact SHA.
From CircleCI's existing `poststeward` project, trigger a pipeline on branch
`main` with *both* pipeline parameters:

- `native_macos_main_acceptance` = `true`
- `native_macos_main_revision` = the exact 40-character, lowercase GitHub
  `main` commit SHA approved for the run

The parameter defaults are `false` and empty. Regular main pushes do not
schedule the macOS job. The existing two historical acceptance branches retain
their original separate branch-filtered workflow.

The first macOS step verifies Darwin/arm64, the exact checked-out CircleCI
revision, the reviewed GitHub SHA and the current branch **before** any
installation, `launchctl` invocation or other lifecycle effect. A missing,
malformed or mismatched expected revision aborts closed. The exercised flow
installs in an isolated temporary prefix with `--no-onboard`, checks inactive
authority and disarmed customer timers, tests `launchd`, interruption recovery,
rollback and reinstallation. No provider account is paired or published to.

Read back the specific CircleCI pipeline, job URL, final conclusion, source
SHA and the native platform evidence. Record those alongside GitHub merge,
release identity, installation and customer issue #197. This job proves the
native isolated lifecycle only. It is **not** customer owner pairing, real
provider publication/readback, unattended outage recovery with a live account,
or a sufficient Advanced SLO cohort. Do not promote the release without those
separate authorities and receipts.
