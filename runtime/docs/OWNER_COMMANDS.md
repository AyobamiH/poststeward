# Post-Once owner commands: beginner and advanced

This is the owner-facing guide for the local Post-Once checkout. It is not a
PostSteward or OneClickPostFactory SaaS guide. You do not have to run every
command: existing timers already collect, allocate, publish and handle enabled
reply workflows. Begin with inspection. Use advanced changes only for a specific,
reviewed reason.

The complete command index below is generated from
[`src/ocpf_post/cli_catalog.py`](../src/ocpf_post/cli_catalog.py). Its command names,
purposes, consequence classes and inspection forms retain the native catalogue's
terminology. Hand-written instructions explain how to use them as an owner.
The [CLI reference](CLI_REFERENCE.md) and each command's help remain the detailed
syntax sources. New commands are automatically included when the index is
regenerated; the test suite rejects a stale index.

## Contents

- [Beginner: start here](#beginner-start-here)
- [Beginner: everyday routine](#beginner-everyday-routine)
- [Beginner: understand the results](#beginner-understand-the-results)
- [Advanced: investigation and deliberate changes](#advanced-investigation-and-deliberate-changes)
- [Complete beginner and advanced command index](#complete-beginner-and-advanced-command-index)
- [Maintaining this guide](#maintaining-this-guide)

## Beginner: start here

Use your **WSL Bash terminal**, not PowerShell. Do not copy a `johnh@web:...$`
prompt. Lines beginning with `#` are comments. `./ocpf-post` means the program in
the current checkout; it is deliberately not a different globally installed copy.

```bash
cd "$HOME/post-once"
TODAY="$(TZ=Europe/London date +%F)"
./ocpf-post --version
```

Use the actual clone directory when it has a different name. The reporting
scripts find the checkout containing them, so the repository can be cloned at a
different path. A repository clone supplies software, not your existing private
credentials, schedules or publication history.

`--version` reports the checkout CLI version. It does **not** by itself prove
which immutable release your background services are running. Pulling these docs
and reporting scripts does not require switching that runtime.

Open the existing console in a browser at `http://127.0.0.1:8767/`. Do not start a
second `./ocpf-post console` on that port. Inspecting or replaying the console's
picture does not pause the publishing workers.

To see the command lists directly in your terminal:

```bash
python3 -B scripts/owner-guide.py --level beginner
python3 -B scripts/owner-guide.py --level advanced
```

These two guide commands only print information. They do not execute the commands
shown in their output. `--level all` prints both lists.

### How to read options and placeholders

`--json` asks supported commands for structured output instead of prose; it does
not grant publishing permission. `--check` is essential on `run-due` to inspect
without executing publication. `--apply` and `--live` are not interchangeable;
their effects depend on the command. `--save` writes a report where supported.
Never append a flag just because another command accepts it.

For a specific item, replace the values below with exact IDs from your report.
Do not run item-specific examples with the placeholder strings unchanged.

```bash
CAMPAIGN='REPLACE_WITH_EXACT_CAMPAIGN_ID'
PROVIDER='x'  # Use x, threads, or linkedin.
SCHEDULE='REPLACE_WITH_EXACT_SCHEDULE_ID'
```

Get exact required arguments without running the operation:

```bash
./ocpf-post help campaign explain
./ocpf-post schedule inspect --help
./ocpf-post help
```

## Beginner: everyday routine

### 1. Check health and outstanding work

```bash
./ocpf-post health
./ocpf-post work status --json
```

Health is a recent-evidence view, not a London calendar-day post count. Work
status lists blockers, deadlines and suggested actions; it does not run them.
For `health`, the catalogue documents exit 0 as OK, 1 as warning, 2 as unknown and
3 as attention. A nonzero result here is not automatically a broken installation.
Other commands have their own exit codes; do not apply that mapping universally.

### 2. See today's posts, accounts, campaigns and vault origins

```bash
bash scripts/owner-daily.sh
```

This repository-owned helper saves a new private directory under your home
folder, containing `summary.txt` and `publications.json`. It prints their exact
paths and opens the summary in Notepad when WSL interop is available. No manual
download or script copy is required after pulling the repository.

For a historical London day, replace the example date with the required date:

```bash
bash scripts/owner-daily.sh --date 2026-09-21
```

For a terminal-only saved report:

```bash
bash scripts/owner-daily.sh --no-open
```

For JSON on standard output without saving files:

```bash
python3 -B scripts/owner-daily.py
```

The helper reads the existing completed-publication model. It reports X, Threads
and LinkedIn separately, with account, campaign, project, recorded vault/base
entry, source metadata, post ID, recorded URL and verification state. It counts
one completed logical campaign publication, not every part of a native thread.
Replies, partial effects and ambiguous effects are outside these completed totals.
A later readback is not counted as a second post.

Exact copy is shown only if the current local text matches the receipt's hash.
Origin metadata is from the **current local manifest**, not a reconstruction of
historical edits. Missing vault metadata means origin is unestablished, not proof
that the content never came from a vault. Missing ledger access fails rather
than pretending zero posts were published. No provider or Google readback occurs.
The helper respects existing state/config environment overrides and does not
load or print tokens. Saved reports can still contain private copy or identifiers;
review them before sharing.

### 3. Check what is booked next

```bash
./ocpf-post schedule list
./ocpf-post portfolio calendar --horizon-minutes 1440
./ocpf-post run-due --check
```

The calendar includes booked reservations and provisional forecasts. A forecast
is not a schedule, and a future schedule is not a publication failure. Keep
`--check` in the last command: **bare `run-due` can publish**.

### 4. Check supply and collected replies

```bash
./ocpf-post portfolio status
./ocpf-post replenish status
./ocpf-post vault status
./ocpf-post engagement status
```

The protected multi-day reserve differs from content runnable now. Content
expiring before the reserve horizon can be runnable today without contributing
to protected reserve. The 100/day/account limit is a safety ceiling, not a target,
not evidence of available supply, and not a requirement to fill every slot.

## Beginner: understand the results

A campaign is the approved content package. A project is the product promoted.
An account is the destination identity. A vault is a registered source document.
A schedule is a future reservation. A receipt records the publication outcome.
Do not substitute one for another when answering what actually happened.

| Result | Meaning | Owner response |
| --- | --- | --- |
| `published_verified` | Publication has recorded verification evidence. | Count once; do not send again. |
| `published_unverified` | Publication is recorded without verified readback. | Count as published, preserve the verification limitation; do not resend. |
| `scheduled` | A durable future reservation exists. | Inspect `run_at`; it may not be due yet. |
| `ambiguous_effect` or `partial_effect` | Consequence is uncertain or incomplete. | Inspect exact evidence; do not force duplicates or blind retries. |
| Missing metrics | Measurement is unavailable or absent. | Keep it unknown, not zero. |
| `empty` protected reserve | No protected items under the reserve calculation. | Inspect runnable inventory separately before concluding publishing stopped. |

For a native daily total, run the volume audit and use
`target.effect_provider_counts` and `target.publication_effects_on_day`, not
schedule totals. A partial current day must not be compared as though all future
schedules were overdue or both adjacent days were complete.

```bash
./ocpf-post portfolio volume-audit --date "$TODAY" --timezone Europe/London --json
```

For one campaign, the beginner index gives `campaign show`, `campaign explain`
and `receipt` examples. `receipt` is a latest matching receipt, not the complete
daily report. Add `--account-id` with the exact destination identity when needed.
`campaign receipts` is a reviewed-brief milestone report, not all publication origins.

## Advanced: investigation and deliberate changes

Advanced does not always mean dangerous: full evidence inspection and provider
reads may be slow, detailed, or require more context. The native consequence
classes distinguish reading, local writes, future work, external effects and
authority changes. **READ_ONLY does not universally mean offline**: provider
status and source-evidence commands can access a network service. Read their help.

### Automation and logs: inspection only

```bash
systemctl --user list-timers --all 'ocpf-post-*' --no-pager
journalctl --user -u ocpf-post-run-due.service --since today --no-pager
journalctl --user -u ocpf-post-portfolio-refill.service --since today --no-pager
journalctl --user -u ocpf-post-collection.service --since today --no-pager
journalctl --user -u ocpf-post-replies.service --since today --no-pager
journalctl --user -u ocpf-post-console.service -n 40 --no-pager
```

These are shell/systemd commands, not native Post-Once subcommands. Journal
`today` uses the host timezone; the daily publication report explicitly uses
Europe/London. Review raw journal output for private data before sharing it.
Do not stop or kill an executing publication service to make an inspection easier.

### Deeper evidence and planning

```bash
./ocpf-post schedule list --all
./ocpf-post operations --all
./ocpf-post runtime status
./ocpf-post state verify
./ocpf-post portfolio plan --horizon-minutes 75
./ocpf-post replenish lock-status --json
```

`state verify` is the explicit full forensic check; the live console's
critical-ledger-only view is narrower. `runtime status` can be expensive and may
include remote repository attestation. `operations --all` joins milestones, not
just today's posts. A dry plan remains a proposal, not a reservation. Never
remove a live lock based only on a status result.

### Before any change

First inspect exact help, the destination account, the campaign and existing
receipts. Use a preview only where the catalogue declares one. A fresh review
hash is bound to the specific operation; never reuse an old hash, bypass a
mismatch, or automatically retry a consequential failure.

For example, these commands show instructions and do not perform the operations:

```bash
./ocpf-post help portfolio refill
./ocpf-post help vault sync
./ocpf-post help campaign reconcile
./ocpf-post help runtime upgrade
```

`portfolio refill --apply` creates reservations. `vault sync --apply` may admit
new content and, with opt-in document-write authority, reconcile source-document
status. `replenish refresh --apply` changes admitted inventory. `portfolio
reconcile` and `replenish reconcile` are not harmless status commands even without
an `--apply` flag. Existing timers already drive normal operation.

Provider `--live`, bare `run-due`, and enabled reply processing can create external
posts. `alerts send` creates notification effects, not social posts. Command names
such as `connect`, `disable`, `cancel`, `draft` or `capture` do not imply preview
mode. Never use `--allow-duplicate` as a recovery shortcut.

`vault auth` is described by the native catalogue as read-only Google access;
[the writeback runbook](SUPPLY_RESILIENCE_AND_VAULT_WRITEBACK.md) additionally
documents expanded document-write consent when writeback is explicitly enabled.
Inspect the configured mode rather than treating every OAuth consent as read-only.

### Software updates are not an everyday reporting command

Use a clean control checkout and the usual reviewed branch/PR workflow. Do not
stash, reset or delete unrelated work just to get an update through. `git pull
--ff-only` updates code; it does not prove or automatically promote a pinned
background runtime. Pulling this guide and report helper needs **no runtime switch**.

For a real runtime upgrade, use the reviewed drain-first maintenance procedure:
pause future timer wake-ups, allow in-flight writers to finish, obtain a fresh
review, apply once through the existing guard, and restore the original wake-ups.
Do not take a state-fingerprint review while writers are still changing that
state, and do not bypass `Release switch review changed`. This guide does not
install a separate recovery helper or change the existing upgrade implementation.

## Complete beginner and advanced command index

Descriptions and safety forms below are generated from the native catalogue.
The first list gives safe inspection examples; the advanced lists are references,
not commands to paste as a batch. `--version` and `--help` are flags, not additional
leaf commands. Repository scripts and systemd commands are documented separately.

<!-- BEGIN OWNER COMMAND INDEX -->

Native catalogue coverage: **140 commands: 19 beginner and 121 advanced**.
The repository report/guide helpers and shell commands are additional tools, not new native CLI commands.

### Beginner command list

Use the setup and exact placeholders explained above. These examples inspect rather than publish.

| Command | Purpose from the CLI catalogue | Safety of this example |
| --- | --- | --- |
| `./ocpf-post health` | Inspect local publishing evidence, freshness, capacity and automation health. | READ_ONLY |
| `./ocpf-post doctor` | Check local prerequisites and stored configuration state. | READ_ONLY |
| `./ocpf-post work status --json` | Show one generated current-work projection with blockers, deadlines, dependencies and no-replay/no-renew flags. | READ_ONLY |
| `./ocpf-post portfolio volume-audit --date "$TODAY" --timezone Europe/London --json` | Compare one local day of durable schedules with receipt-backed publication effects and adjacent-day volume. | READ_ONLY |
| `./ocpf-post portfolio calendar --horizon-minutes 1440` | Join saved reservations, observed outcomes and provisional selections by account. | Inspect only; omit --save |
| `./ocpf-post schedule list` | List active schedules, or all schedule history with --all. | READ_ONLY |
| `./ocpf-post portfolio status` | Show eligible inventory, active reservations and daily targets. | READ_ONLY |
| `./ocpf-post replenish status` | Show runtime campaign inventory and GitHub source cursor state. | READ_ONLY |
| `./ocpf-post vault status` | Read vault configuration and local observations. | READ_ONLY |
| `./ocpf-post vault coverage --inventory docs/gtm/vault-expansion-20260910/inventory.json` | Reconcile expected vault projects and platform entries with local authority, freshness and intact imports. | READ_ONLY |
| `./ocpf-post engagement status` | Read reply collection status and optional inbox items. | READ_ONLY |
| `./ocpf-post engagement worker-status` | Inspect recurring reply decisions, budgets and model readiness. | READ_ONLY |
| `./ocpf-post registry list` | List registered GTM projects and campaign prefixes. | READ_ONLY |
| `./ocpf-post accounts list` | Inspect additional identities, activation and credential presence. | READ_ONLY |
| `./ocpf-post campaign show --campaign "$CAMPAIGN" --provider "$PROVIDER"` | Show one built-in campaign payload. | READ_ONLY |
| `./ocpf-post campaign explain --campaign "$CAMPAIGN" --provider "$PROVIDER" --json` | Explain local copy provenance, allocation exclusions, schedules and receipts. | READ_ONLY |
| `./ocpf-post receipt --campaign "$CAMPAIGN" --provider "$PROVIDER"` | Show the latest local X publication receipt. | READ_ONLY |
| `./ocpf-post schedule inspect "$SCHEDULE" --json` | Inspect one schedule and immutable event history without exposing scheduled copy. | READ_ONLY |
| `./ocpf-post help` | Discover the complete CLI capability catalogue in text or JSON. | READ_ONLY |

### Advanced command list

These are **command names**, not a run-all script. Required IDs, files and arguments are intentionally not invented. Inspect the exact form with `./ocpf-post help COMMAND_PATH` before using a row. The safety column comes from the native catalogue; no safe form means do not assume a preview exists.

#### Advanced inspection: deeper or provider-facing reads

Native consequence class: `READ_ONLY`.

| Command name | Purpose from the CLI catalogue | Inspection form or warning |
| --- | --- | --- |
| `./ocpf-post alerts status` | Inspect configured incident-notification delivery state. | Read only; may access provider or local state |
| `./ocpf-post campaign receipts` | Join reviewed brief schedules to publication receipts. | Read only; may access provider or local state |
| `./ocpf-post capabilities` | Show implementation, configuration, authority, evidence and acceptance state. | Read only; may access provider or local state |
| `./ocpf-post config show` | Show effective configuration origins and policy presence without secret values. | Read only; may access provider or local state |
| `./ocpf-post console` | Serve or snapshot the loopback-only read-only observability console. | Read only; may access provider or local state |
| `./ocpf-post credentials keyring status` | Inspect optional OS keyring/keychain backend and provider credential presence. | Read only; may access provider or local state |
| `./ocpf-post engagement conversation` | Read known ancestors of a collected inbound reply. | Read only; may access provider or local state |
| `./ocpf-post linkedin receipt` | Show the latest local LinkedIn receipt. | Read only; may access provider or local state |
| `./ocpf-post linkedin status` | Read back the authenticated LinkedIn account. | Read only; may access provider or local state |
| `./ocpf-post outcome-connectors status` | Inspect registered receipt-linked HTTPS outcome connectors and cursor state. | Read only; may access provider or local state |
| `./ocpf-post performance compare` | Compare latest snapshots across campaigns/providers. | Read only; may access provider or local state |
| `./ocpf-post performance review` | Compare receipt-linked metrics at matching post ages and destinations. | Read only; may access provider or local state |
| `./ocpf-post performance show` | Show the latest local performance snapshot. | Read only; may access provider or local state |
| `./ocpf-post portfolio experiment status` | Read effective trial targets and delivery gates. | Read only; may access provider or local state |
| `./ocpf-post portfolio plan` | Dry-plan the rolling portfolio horizon with diversity controls. | Read only; may access provider or local state |
| `./ocpf-post registry resolve` | Resolve one project-scoped account alias. | Read only; may access provider or local state |
| `./ocpf-post registry show` | Show one project and its stable account aliases. | Read only; may access provider or local state |
| `./ocpf-post replenish lock-status` | Inspect the source-operation coordination lease. | Read only; may access provider or local state |
| `./ocpf-post replenish source evidence` | Read exact-commit GitHub workflow and deployment observations. | Read only; may access provider or local state |
| `./ocpf-post replenish source list` | Read runtime source policies and activation state as JSON. | Read only; may access provider or local state |
| `./ocpf-post replenish source preview` | Read GitHub and preview exact static copy, bound identities and the event policy. | Read only; may access provider or local state |
| `./ocpf-post replenish source receipts` | Find matching scheduled publication receipts for a project's generated source campaigns. | Read only; may access provider or local state |
| `./ocpf-post replenish wait` | Wait a bounded interval for the source-operation lock to become available. | Read only; may access provider or local state |
| `./ocpf-post runtime status` | Attest the checkout, local state integrity and capability readiness. | Read only; may access provider or local state |
| `./ocpf-post setup admission` | Prove host capabilities and classify existing installation state before guided setup. | Read only; may access provider or local state |
| `./ocpf-post setup adoption-review` | Build an evidence-backed package for this project's own Post-Once-derived runtime adoption. | Read only; may access provider or local state |
| `./ocpf-post setup inspect-bundle` | Verify a healthy migration or dead-host recovery artifact without granting authority. | Read only; may access provider or local state |
| `./ocpf-post setup status` | Read the latest or named bootstrap setup session and control-store health. | Read only; may access provider or local state |
| `./ocpf-post state inventory` | Fingerprint and classify local state/config files without exposing contents. | Read only; may access provider or local state |
| `./ocpf-post state verify` | Validate structured state and fail closed on corrupt durable ledgers. | Read only; may access provider or local state |
| `./ocpf-post threads receipt` | Show the latest local Threads receipt. | Read only; may access provider or local state |
| `./ocpf-post threads status` | Read back the authenticated Threads account. | Read only; may access provider or local state |
| `./ocpf-post x status` | Read back the exact authenticated X account. | Read only; may access provider or local state |

#### Advanced: local evidence and report writes

Native consequence class: `LOCAL_STATE_WRITE`.

| Command name | Purpose from the CLI catalogue | Inspection form or warning |
| --- | --- | --- |
| `./ocpf-post campaign reconcile` | Preview or perform bounded GET-only reconciliation of existing publication effects. | omit --apply |
| `./ocpf-post engagement dismiss` | Dismiss a pending reply with a recorded reason. | No preview declared; read help and review before execution |
| `./ocpf-post engagement draft` | Store exact response text bound to collected account and reply context. | No preview declared; read help and review before execution |
| `./ocpf-post engagement reconcile` | Verify existing reply IDs and append exact-context readback evidence. | omit --apply |
| `./ocpf-post engagement sync` | Collect bounded inbound replies/comments to recent receipt-backed X/Threads/LinkedIn posts. | omit --apply |
| `./ocpf-post evidence check` | Preview or observe a scoped revision-bound HTTPS JSON application check. | omit --apply |
| `./ocpf-post operations` | Observe project or portfolio milestones and optionally save a private report. | omit --save |
| `./ocpf-post outcome-connectors sync` | Pull bounded authorised outcome pages and persist exact receipt-linked business events. | omit --apply |
| `./ocpf-post performance capture` | Read provider metrics and append a local performance snapshot. | No preview declared; read help and review before execution |
| `./ocpf-post performance capture-due` | Preview or capture metrics near a target post age. | omit --apply |
| `./ocpf-post performance feedback` | Build bounded account-specific selection preferences from comparable evidence. | omit --apply |
| `./ocpf-post performance outcomes` | Inspect or import explicit receipt-linked enquiries, sign-ups and sales. | omit --apply |
| `./ocpf-post portfolio experiment report` | Compare receipt-linked before/during metrics at equivalent post ages. | omit --save |
| `./ocpf-post portfolio refill` | Plan or create durable near-term portfolio reservations. | omit --apply |
| `./ocpf-post portfolio replay` | Compare baseline and fair selection from a frozen queue, including waiting and expiry risk. | omit --output |
| `./ocpf-post portfolio snapshot` | Capture stable allocator inputs and a reproducible baseline. | omit --output |
| `./ocpf-post portfolio watch` | Inspect automatic queue age, deadline and unresolved outcome supervision. | omit --apply |
| `./ocpf-post replenish observe` | Collect bounded source revisions independently of inventory pressure. | omit --apply |
| `./ocpf-post replenish reconcile` | Disable runtime campaigns superseded by a newer source snapshot. | No preview declared; read help and review before execution |
| `./ocpf-post setup beta` | Enroll, observe, inspect or decide an explicitly bounded local beta ring. | --action status |
| `./ocpf-post setup interactive` | Run the resumable fresh/explore terminal setup wizard. | No preview declared; read help and review before execution |
| `./ocpf-post setup onboard-x` | Bind the connected X identity to one user project and manual-only campaign, then advance Fresh verification. | omit --apply |
| `./ocpf-post setup recovery-point` | Preview or seal an immutable credential-free point for later dead-host recovery. | omit --apply |
| `./ocpf-post setup restore` | Verify and quarantine a healthy migration bundle or dead-host recovery point on a new installation. | No preview declared; read help and review before execution |
| `./ocpf-post setup resume` | Resume one incomplete setup session from its last committed revision. | No preview declared; read help and review before execution |
| `./ocpf-post setup start` | Start fresh, explore, or same-operator source setup from explicit inputs. | No preview declared; read help and review before execution |
| `./ocpf-post setup verify` | Verify target provider identity evidence and reconcile migration/recovery schedules. | No preview declared; read help and review before execution |
| `./ocpf-post setup verify-fresh` | Read-only verify a connected provider plus user-owned project/content before fresh activation. | No preview declared; read help and review before execution |
| `./ocpf-post state archive` | Preview or create a consistent credential-free state-evidence archive. | omit --apply |
| `./ocpf-post state migrate` | Preview or apply explicitly implemented schema migrations. | omit --apply |
| `./ocpf-post state recover-segment` | Preview or recover an interrupted critical-ledger segmentation transaction. | omit --apply |
| `./ocpf-post state segment` | Preview or move an immutable prefix of a critical ledger into numbered segments while preserving logical order. | omit --apply |

#### Advanced: future scheduling and admission changes

Native consequence class: `FUTURE_CONSEQUENCE`.

| Command name | Purpose from the CLI catalogue | Inspection form or warning |
| --- | --- | --- |
| `./ocpf-post accounts disable` | Disable future use of an additional account; existing effects remain recorded. | No preview declared; read help and review before execution |
| `./ocpf-post accounts enable` | Preview or enable an independently budgeted account after identity verification. | omit --apply |
| `./ocpf-post campaign brief` | Compile and preview or import reviewed evidence briefs with distinct editorial angles. | omit --apply |
| `./ocpf-post campaign import` | Preview or add approved runtime copy, with explicit optional allocator opt-in. | omit --apply |
| `./ocpf-post portfolio experiment reconcile` | Inspect or reconcile the scoped, expiring owner capacity trial. | omit --apply |
| `./ocpf-post portfolio experiment stop` | Permanently stop this trial without altering existing reservations. | No preview declared; read help and review before execution |
| `./ocpf-post portfolio policy` | Show or update the private portfolio policy. | omit --write-default and --selection |
| `./ocpf-post portfolio reconcile` | Cancel allocator-owned schedules whose campaign is no longer fresh. | No preview declared; read help and review before execution |
| `./ocpf-post replenish acknowledge-gap` | Review and archive a source gap before explicitly resuming observation from its head. | omit --apply |
| `./ocpf-post replenish recover-generated` | Preview or recover reviewed legacy generated copy as fresh successor inventory. | omit --apply |
| `./ocpf-post replenish refresh` | Admit saved source evidence per destination within inventory budgets. | omit --apply |
| `./ocpf-post replenish source disable` | Stop new replenishment from a runtime source. | No preview declared; read help and review before execution |
| `./ocpf-post replenish source enable` | Authorise a runtime source for future replenishment and allocator selection. | No preview declared; read help and review before execution |
| `./ocpf-post schedule cancel` | Cancel a schedule before it is claimed. | No preview declared; read help and review before execution |
| `./ocpf-post schedule create` | Create one durable future provider reservation. | No preview declared; read help and review before execution |
| `./ocpf-post vault sync` | Observe approved vault revisions and withdrawals; admit new packages within destination budgets. | omit --apply |

#### Advanced: publication, replies and notification delivery

Native consequence class: `EXTERNAL_PROVIDER_EFFECT`.

| Command name | Purpose from the CLI catalogue | Inspection form or warning |
| --- | --- | --- |
| `./ocpf-post alerts send` | Deliver new/resolved deduplicated operating incidents to the authorised endpoint. | omit --apply |
| `./ocpf-post engagement process` | Run the configured account-scoped model review and response worker. | omit --apply |
| `./ocpf-post engagement send` | Preview or explicitly send one reviewed reply with a durable outcome record. | omit --live |
| `./ocpf-post linkedin publish` | Dry-run or explicitly publish one LinkedIn campaign. | omit --live |
| `./ocpf-post publish` | Dry-run or explicitly publish one X campaign. | omit --live |
| `./ocpf-post run-due` | Execute due schedules through provider consequence paths. | --check |
| `./ocpf-post threads publish` | Dry-run or explicitly publish one Threads campaign. | omit --live |

#### Advanced: credentials, account authority and runtime maintenance

Native consequence class: `AUTHORITY_CHANGE`.

| Command name | Purpose from the CLI catalogue | Inspection form or warning |
| --- | --- | --- |
| `./ocpf-post accounts connect` | Verify credentials for an inactive additional account, including non-secret reuse of the existing LinkedIn member credential for a Page actor. | No preview declared; read help and review before execution |
| `./ocpf-post accounts import` | Preview or add inactive X/Threads/LinkedIn identities, project aliases and independent budgets. | omit --apply |
| `./ocpf-post alerts configure` | Preview or configure an inactive public-HTTPS incident notification destination. | omit --apply |
| `./ocpf-post alerts connect` | Store a private bearer credential for the alert destination. | No preview declared; read help and review before execution |
| `./ocpf-post alerts disable` | Disable future incident notifications while preserving incident state. | No preview declared; read help and review before execution |
| `./ocpf-post alerts enable` | Preview or enable deduplicated incident notification delivery. | omit --apply |
| `./ocpf-post credentials keyring migrate` | Preview or migrate one default provider credential from a private file into the OS keyring. | omit --apply |
| `./ocpf-post credentials keyring restore` | Preview or restore one provider credential from the OS keyring to a private mode-0600 file. | omit --apply |
| `./ocpf-post linkedin refresh` | Refresh LinkedIn provider authorisation when supported. | No preview declared; read help and review before execution |
| `./ocpf-post outcome-connectors connect` | Store a private bearer credential for an inactive outcome connector. | No preview declared; read help and review before execution |
| `./ocpf-post outcome-connectors disable` | Disable future outcome-source reads while preserving imported evidence. | No preview declared; read help and review before execution |
| `./ocpf-post outcome-connectors enable` | Preview or enable bounded pull-only outcome ingestion. | omit --apply |
| `./ocpf-post outcome-connectors import` | Preview or register an inactive bounded HTTPS outcome source. | omit --apply |
| `./ocpf-post registry import` | Preview or add a runtime project with expected account bindings. | omit --apply |
| `./ocpf-post replenish source import` | Preview or register an inactive runtime GitHub source policy. | omit --apply |
| `./ocpf-post runtime install` | Preview or install/reconcile Post-Once user automation and console units. | omit --apply |
| `./ocpf-post runtime reconcile` | Preview or safely restore user-unit paths to the current clean checkout. | omit --apply |
| `./ocpf-post runtime rollback` | Preview or switch software to the previously recorded compatible revision. | omit --apply |
| `./ocpf-post runtime uninstall` | Preview or remove Post-Once user units without deleting evidence. | omit --apply |
| `./ocpf-post runtime upgrade` | Preview or switch user-unit software to one exact locally available Git revision. | omit --apply |
| `./ocpf-post setup activate` | Preview or apply the unattended-publishing activation gate. | omit --apply |
| `./ocpf-post setup browser` | Serve the secure loopback-only browser renderer over the same SetupEngine. | close the browser without applying reviewed actions |
| `./ocpf-post setup connect-x` | Connect and read-back verify one X account for Fresh beta onboarding. | No preview declared; read help and review before execution |
| `./ocpf-post setup deactivate` | Preview or apply marker-first unattended-automation deactivation. | omit --apply |
| `./ocpf-post setup export` | Preview or seal a credential-free healthy migration bundle from a quiescent source. | omit --apply |
| `./ocpf-post threads refresh` | Refresh Threads provider authorisation when supported. | No preview declared; read help and review before execution |
| `./ocpf-post vault auth` | Connect read-only Google access using Desktop OAuth and PKCE. | No preview declared; read help and review before execution |
| `./ocpf-post vault credentials` | Install private operator-owned Google refresh credentials. | No preview declared; read help and review before execution |
| `./ocpf-post vault extend` | Review adding a provider destination without replacing existing vault authority. | omit --apply |
| `./ocpf-post vault register` | Preview or register designated document authority. | omit --apply |
| `./ocpf-post x auth` | Run X OAuth 2.0 Authorization Code + PKCE. | No preview declared; read help and review before execution |
| `./ocpf-post x logout` | Remove/revoke the stored X token. | No preview declared; read help and review before execution |
| `./ocpf-post x refresh` | Refresh the stored X access token. | No preview declared; read help and review before execution |

<!-- END OWNER COMMAND INDEX -->

## Maintaining this guide

After adding or changing a command in the native catalogue, regenerate the index:

```bash
python3 -B scripts/owner-guide.py --write
python3 -B scripts/owner-guide.py --check
```

`--write` changes this documentation file only; `--check` does not write. The
coverage test requires every catalogue path exactly once across beginner and
advanced index rows and rejects changed beginner safety contracts. Help listings
and report execution never dispatch the commands shown in documentation.

Focused regression tests:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests -p 'test_owner_*.py' -v
```

The report tests use simulated read interfaces and receipts. They are not a live
provider readback or proof of today's production counts. The guide tests use the
native catalogue. Normal repository CI remains responsible for full-suite checks.
