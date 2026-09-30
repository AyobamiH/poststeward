from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any

from ocpf_post import __version__

SCHEMA_VERSION = 1

@dataclass(frozen=True)
class Command:
    path: str
    summary: str
    consequence: str
    external_effect: bool = False
    safe_form: str | None = None
    examples: tuple[str, ...] = ()
    notes: str = ""


COMMANDS: tuple[Command, ...] = (
    Command("engagement reconcile", "Verify existing reply IDs and append exact-context readback evidence.", "LOCAL_STATE_WRITE", safe_form="omit --apply"),
    Command("performance outcomes", "Inspect or import explicit receipt-linked enquiries, sign-ups and sales.", "LOCAL_STATE_WRITE", safe_form="omit --apply"),
    Command("accounts list", "Inspect additional identities, activation and credential presence.", "READ_ONLY"),
    Command("accounts import", "Preview or add inactive X/Threads/LinkedIn identities, project aliases and independent budgets.", "AUTHORITY_CHANGE", safe_form="omit --apply"),
    Command("accounts connect", "Verify credentials for an inactive additional account, including non-secret reuse of the existing LinkedIn member credential for a Page actor.", "AUTHORITY_CHANGE", notes="--reuse-default is accepted only for LinkedIn organization/organizationBrand actors and stores no copied access token; the Page is still verified independently before enablement."),
    Command("accounts enable", "Preview or enable an independently budgeted account after identity verification.", "FUTURE_CONSEQUENCE", safe_form="omit --apply"),
    Command("accounts disable", "Disable future use of an additional account; existing effects remain recorded.", "FUTURE_CONSEQUENCE"),
    Command("operations", "Observe project or portfolio milestones and optionally save a private report.", "LOCAL_STATE_WRITE", False, "omit --save", examples=("ocpf-post operations --project post-once", "ocpf-post operations --all --save"), notes="--all joins each registered project's generated, brief and vault schedules to exact receipts. Saved by the bounded collector; no provider call, publication or repair."),
    Command("campaign receipts", "Join reviewed brief schedules to publication receipts.", "READ_ONLY"),
    Command("campaign reconcile", "Preview or perform bounded GET-only reconciliation of existing publication effects.", "LOCAL_STATE_WRITE", safe_form="omit --apply", notes="--apply performs read-only provider GETs and writes separate reconciliation evidence; it never publishes, retries, resets or deletes."),
    Command("vault register", "Preview or register designated document authority.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="--enable trusts approved entries from this document's editors; apply requires reviewed hash."),
    Command("vault extend", "Review adding a provider destination without replacing existing vault authority.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="Apply requires a review hash covering current policy, account identities and document copy. Existing destinations cannot be replaced."),
    Command("vault sync", "Observe approved vault revisions and withdrawals; admit new packages within destination budgets.", "FUTURE_CONSEQUENCE", safe_form="omit --apply"),
    Command("vault status", "Read vault configuration and local observations.", "READ_ONLY"),
    Command("vault coverage", "Reconcile expected vault projects and platform entries with local authority, freshness and intact imports.", "READ_ONLY", examples=("ocpf-post vault coverage --inventory docs/gtm/vault-expansion-20260910/inventory.json",), notes="No Google read or repair. HOLD projects stay outside expected publishing coverage; imports do not prove publication."),
    Command("vault credentials", "Install private operator-owned Google refresh credentials.", "AUTHORITY_CHANGE"),
    Command("vault auth", "Connect read-only Google access using Desktop OAuth and PKCE.", "AUTHORITY_CHANGE"),
    Command("evidence check", "Preview or observe a scoped revision-bound HTTPS JSON application check.", "LOCAL_STATE_WRITE", safe_form="omit --apply"),
    Command("performance review", "Compare receipt-linked metrics at matching post ages and destinations.", "READ_ONLY"),
    Command("performance capture-due", "Preview or capture metrics near a target post age.", "LOCAL_STATE_WRITE", safe_form="omit --apply"),
    Command("campaign brief", "Compile and preview or import reviewed evidence briefs with distinct editorial angles.", "FUTURE_CONSEQUENCE", safe_form="omit --apply", examples=("ocpf-post campaign brief --file examples/post-once-evidence-brief.json",), notes="Apply requires the reviewed input hash. Manual-only by default; --allocate opts into existing scheduling policy. Evidence references are not independently verified; full rendered copy needs review."),
    Command("replenish source receipts", "Find matching scheduled publication receipts for a project's generated source campaigns.", "READ_ONLY", examples=("ocpf-post replenish source receipts --project post-once",), notes="Local JSON across source revisions; no provider call. A direct or brief-imported campaign does not complete the generated-source milestone. Pending is not failure."),
    Command("replenish source evidence", "Read exact-commit GitHub workflow and deployment observations.", "READ_ONLY", examples=("ocpf-post replenish source evidence --project post-once",), notes="Optional --sha must be a full commit SHA. JSON report; no cursor, copy or authority changes. Missing observations are not negative proof or production verification."),
    Command("campaign explain", "Explain local copy provenance, allocation exclusions, schedules and receipts.", "READ_ONLY", examples=("ocpf-post campaign explain --campaign POSTONCE-060-001 --provider x --json",), notes="Local snapshot only. No GitHub/provider calls. Expected account binding is not live identity verification."),
    Command("replenish source import", "Preview or register an inactive runtime GitHub source policy.", "AUTHORITY_CHANGE", safe_form="omit --apply", examples=("ocpf-post replenish source import --file source.json",), notes="Add-only registration. --apply requires the reviewed input_sha256. Does not fetch GitHub or enable replenishment."),
    Command("replenish source list", "Read runtime source policies and activation state as JSON.", "READ_ONLY"),
    Command("replenish source preview", "Read GitHub and preview exact static copy, bound identities and the event policy.", "READ_ONLY", examples=("ocpf-post replenish source preview --project post-once",), notes="Returns review_sha256. No campaigns, cursors or schedules are written."),
    Command("replenish source enable", "Authorise a runtime source for future replenishment and allocator selection.", "FUTURE_CONSEQUENCE", notes="Requires --expected-sha256 from a fresh source preview. Pins repository identity and baselines existing commits. No immediate social post."),
    Command("replenish source disable", "Stop new replenishment from a runtime source.", "FUTURE_CONSEQUENCE", notes="Existing campaigns and reservations remain authorised; cancel unwanted schedules separately."),
    Command("registry import", "Preview or add a runtime project with expected account bindings.", "AUTHORITY_CHANGE", safe_form="omit --apply", examples=("ocpf-post registry import --file project.json",), notes="--apply requires the reviewed input SHA-256. Add-only local authority; does not connect accounts or import credentials."),
    Command("campaign import", "Preview or add approved runtime copy, with explicit optional allocator opt-in.", "FUTURE_CONSEQUENCE", safe_form="omit --apply", examples=("ocpf-post campaign import --file campaign.json",), notes="--apply requires the reviewed input SHA-256. No provider call. --allocate explicitly authorises future selection by the running portfolio allocator. Omit --allocate for manual-only copy."),
    Command("health", "Inspect local publishing evidence, freshness, capacity and automation health.", "READ_ONLY", examples=("ocpf-post health", "ocpf-post health --json"), notes="No provider calls or repair. Exit 0=ok, 1=warning, 2=unknown, 3=attention. See docs/HEALTH.md."),
    Command("doctor", "Check local prerequisites and stored configuration state.", "READ_ONLY", examples=("ocpf-post doctor",)),
    Command("setup interactive", "Run the resumable fresh/explore terminal setup wizard.", "LOCAL_STATE_WRITE", examples=("ocpf-post setup", "ocpf-post setup interactive"), notes="Bare 'ocpf-post setup' is an alias. Creates only bootstrap-local setup state; never connects providers, installs services or enables publishing."),
    Command("setup admission", "Prove host capabilities and classify existing installation state before guided setup.", "READ_ONLY", examples=("ocpf-post setup admission --json", "ocpf-post setup admission --production --json"), notes="Reads bootstrap/runtime authority evidence only. Production mode additionally requires a reachable systemd user manager; no provider call or mutation occurs."),
    Command("setup browser", "Serve the secure loopback-only browser renderer over the same SetupEngine.", "AUTHORITY_CHANGE", safe_form="close the browser without applying reviewed actions", examples=("ocpf-post setup browser", "ocpf-post setup browser --no-open"), notes="The listener is fixed to 127.0.0.1 and uses a process-local session plus CSRF/origin checks. Starting the renderer changes no authority; browser forms preserve each underlying setup command's preview/review/apply gate."),
    Command("setup beta", "Enroll, observe, inspect or decide an explicitly bounded local beta ring.", "LOCAL_STATE_WRITE", safe_form="--action status", examples=("ocpf-post setup beta --action status", "ocpf-post setup beta --action decide --beta-id <id>"), notes="Beta evidence is private, append-only and hash-chained. Rehearsal cannot unlock runtime adoption review; only an owner-canary decision can become bootstrap-runtime-adoption ready. The command never enables publishing authority by itself."),
    Command("setup adoption-review", "Build an evidence-backed package for this project's own Post-Once-derived runtime adoption.", "READ_ONLY", examples=("ocpf-post setup adoption-review --beta-id <id>",), notes="Returns BLOCKED unless the owner-canary beta decision is BOOTSTRAP_RUNTIME_ADOPTION_READY. AyobamiH/post-once and the operator's existing local Post-Once are permanently outside the mutation path."),
    Command("setup start", "Start fresh, explore, or same-operator source setup from explicit inputs.", "LOCAL_STATE_WRITE", examples=("ocpf-post setup start --mode fresh --operator-label 'Example Ltd' --timezone Europe/London --pace regular --json", "ocpf-post setup start --mode migrate --operator-label 'Example Ltd' --json"), notes="The source identity can be used for healthy migration or immutable recovery points. Dead-host recovery itself starts from setup restore --recovery."),
    Command("setup status", "Read the latest or named bootstrap setup session and control-store health.", "READ_ONLY", examples=("ocpf-post setup status --json",), notes="Opens an existing setup database read-only and does not create one when missing."),
    Command("setup resume", "Resume one incomplete setup session from its last committed revision.", "LOCAL_STATE_WRITE", examples=("ocpf-post setup resume --timezone Europe/London --pace active --json",), notes="Uses durable revision-checked transitions; never restarts or replaces an existing session silently."),
    Command("setup export", "Preview or seal a credential-free healthy migration bundle from a quiescent source.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="--apply retires only the bootstrap source authority model after writer-unit quiescence is observed, then seals an immutable bundle. It never stops services, copies credentials or calls a provider."),
    Command("setup recovery-point", "Preview or seal an immutable credential-free point for later dead-host recovery.", "LOCAL_STATE_WRITE", safe_form="omit --apply", notes="Requires a quiescent source and exact review hash. Sealing never retires the source, changes provider credentials, re-arms schedules or enables publishing."),
    Command("setup inspect-bundle", "Verify a healthy migration or dead-host recovery artifact without granting authority.", "READ_ONLY", notes="Use --recovery for recovery points. Verification materializes only bounded quarantine state; production roots are untouched."),
    Command("setup restore", "Verify and quarantine a healthy migration bundle or dead-host recovery point on a new installation.", "LOCAL_STATE_WRITE", notes="--recovery requires an explicit source-loss time and acceptable data-loss window. Recovery marks the old installation unknown and never activates the target."),
    Command("setup verify", "Verify target provider identity evidence and reconcile migration/recovery schedules.", "LOCAL_STATE_WRITE", notes="Use --recovery for a dead-host target. Milestone G stops at RECOVERY_REVIEW_READY with stale-host authority explicitly unresolved; no service activation or provider consequence."),
    Command("setup verify-fresh", "Read-only verify a connected provider plus user-owned project/content before fresh activation.", "LOCAL_STATE_WRITE", examples=("post-once setup verify-fresh --provider x --expected-account-id <id> --json",), notes="Provider observation is read-only: no refresh or publication. The exact immutable account ID must match and standalone runtime state must contain a user-owned project plus an enabled source or imported campaign. Historical packaged owner defaults never satisfy this gate."),
    Command("setup connect-x", "Connect and read-back verify one X account for Fresh beta onboarding.", "AUTHORITY_CHANGE", examples=("post-once setup connect-x --client-id <id> --prompt-client-secret",), notes="Runs OAuth Authorization Code + PKCE, then proves the exact account through a read-only identity request. A prompted/file client secret is persisted only after successful authorization. No post, schedule, source activation or unattended-automation activation occurs."),
    Command("setup onboard-x", "Bind the connected X identity to one user project and manual-only campaign, then advance Fresh verification.", "LOCAL_STATE_WRITE", safe_form="omit --apply", examples=("post-once setup onboard-x --project example --label 'Example' --text-file first-post.txt --json",), notes="Preview is non-mutating. --apply performs reviewed add-only local project/campaign imports with allocator opt-in disabled, then uses read-only provider readiness to advance to verification_ready. It never publishes or activates unattended automation."),
    Command("setup activate", "Preview or apply the unattended-publishing activation gate.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="Apply requires an exact review hash. State/config promotion and timer staging occur while the local automation marker is inactive; the marker write is the final cutover. Recovery requires structured resolution of every recovery-review blocker."),
    Command("setup deactivate", "Preview or apply marker-first unattended-automation deactivation.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="Apply writes the local automation marker inactive before disabling timers. Durable receipts/schedules are never rolled backwards."),
    Command("x auth", "Run X OAuth 2.0 Authorization Code + PKCE.", "AUTHORITY_CHANGE", notes="Changes local provider authorisation state."),
    Command("x status", "Read back the exact authenticated X account.", "READ_ONLY"),
    Command("x refresh", "Refresh the stored X access token.", "AUTHORITY_CHANGE"),
    Command("x logout", "Remove/revoke the stored X token.", "AUTHORITY_CHANGE"),
    Command("campaign show", "Show one built-in campaign payload.", "READ_ONLY", examples=("ocpf-post campaign show --campaign OCPF-001 --provider x",)),
    Command("publish", "Dry-run or explicitly publish one X campaign.", "EXTERNAL_PROVIDER_EFFECT", True, "omit --live", ("ocpf-post publish --campaign OCPF-001", "ocpf-post publish --campaign OCPF-001 --live"), "--live creates an external provider consequence; terminal receipts block blind duplicates."),
    Command("receipt", "Show the latest local X publication receipt.", "READ_ONLY"),
    Command("threads status", "Read back the authenticated Threads account.", "READ_ONLY"),
    Command("threads refresh", "Refresh Threads provider authorisation when supported.", "AUTHORITY_CHANGE"),
    Command("threads publish", "Dry-run or explicitly publish one Threads campaign.", "EXTERNAL_PROVIDER_EFFECT", True, "omit --live", notes="--live creates an external provider consequence."),
    Command("threads receipt", "Show the latest local Threads receipt.", "READ_ONLY"),
    Command("linkedin status", "Read back the authenticated LinkedIn account.", "READ_ONLY"),
    Command("linkedin refresh", "Refresh LinkedIn provider authorisation when supported.", "AUTHORITY_CHANGE"),
    Command("linkedin publish", "Dry-run or explicitly publish one LinkedIn campaign.", "EXTERNAL_PROVIDER_EFFECT", True, "omit --live", notes="--live creates an external provider consequence."),
    Command("linkedin receipt", "Show the latest local LinkedIn receipt.", "READ_ONLY"),
    Command("schedule create", "Create one durable future provider reservation.", "FUTURE_CONSEQUENCE", False, None, ("ocpf-post schedule create --provider x --campaign DONESTATE-002 --at '2026-09-09 12:15'",), "Does not publish immediately."),
    Command("schedule list", "List active schedules, or all schedule history with --all.", "READ_ONLY", examples=("ocpf-post schedule list", "ocpf-post schedule list --all --json")),
    Command("schedule inspect", "Inspect one schedule and immutable event history without exposing scheduled copy.", "READ_ONLY", examples=("ocpf-post schedule inspect sch_3a68dde9d8c8494db9dd7a5ace5254b1 --json",), notes="Local state only. Redacts scheduled post text and never calls a provider or mutates the schedule."),
    Command("schedule cancel", "Cancel a schedule before it is claimed.", "FUTURE_CONSEQUENCE", False, None, notes="Prevents the scheduled external consequence when cancellation succeeds."),
    Command("run-due", "Execute due schedules through provider consequence paths.", "EXTERNAL_PROVIDER_EFFECT", True, "--check", ("ocpf-post run-due --check",), "Without --check this may create external posts."),
    Command("registry list", "List registered GTM projects and campaign prefixes.", "READ_ONLY"),
    Command("registry show", "Show one project and its stable account aliases.", "READ_ONLY"),
    Command("registry resolve", "Resolve one project-scoped account alias.", "READ_ONLY"),
    Command("performance capture", "Read provider metrics and append a local performance snapshot.", "LOCAL_STATE_WRITE", False, None),
    Command("performance show", "Show the latest local performance snapshot.", "READ_ONLY"),
    Command("performance compare", "Compare latest snapshots across campaigns/providers.", "READ_ONLY"),
    Command("portfolio status", "Show eligible inventory, active reservations and daily targets.", "READ_ONLY", examples=("ocpf-post portfolio status",)),
    Command("portfolio experiment reconcile", "Inspect or reconcile the scoped, expiring owner capacity trial.", "FUTURE_CONSEQUENCE", False, "omit --apply", notes="--apply starts once when existing fair policy and receipt gates match; changes effective capacity, never base policy or posts."),
    Command("portfolio experiment status", "Read effective trial targets and delivery gates.", "READ_ONLY"),
    Command("portfolio experiment report", "Compare receipt-linked before/during metrics at equivalent post ages.", "LOCAL_STATE_WRITE", False, "omit --save", notes="Descriptive only. Missing metrics/conversions stay unknown. --save writes the private report."),
    Command("portfolio experiment stop", "Permanently stop this trial without altering existing reservations.", "FUTURE_CONSEQUENCE", False),
    Command("engagement conversation", "Read known ancestors of a collected inbound reply.", "READ_ONLY"),
    Command("engagement worker-status", "Inspect recurring reply decisions, budgets and model readiness.", "READ_ONLY"),
    Command("engagement process", "Run the configured account-scoped model review and response worker.", "EXTERNAL_PROVIDER_EFFECT", True, "omit --apply", notes="--apply may send through enabled automatic account policies; X requires a recorded written provider approval. No tools available to model; ambiguous effects never retry."),
    Command("performance feedback", "Build bounded account-specific selection preferences from comparable evidence.", "LOCAL_STATE_WRITE", safe_form="omit --apply", notes="Requires five matched topics within an account/lane/template. A winner gains three points above the template base, restoring legacy variant discounts where present (at most eleven total). Existing fairness, expiry and alternate exploration remain. No copy or quota changes."),
    Command("engagement status", "Read reply collection status and optional inbox items.", "READ_ONLY"),
    Command("engagement sync", "Collect bounded inbound replies/comments to recent receipt-backed X/Threads/LinkedIn posts.", "LOCAL_STATE_WRITE", False, "omit --apply", notes="--apply uses existing host credentials, verifies account and stores replies; never sends. Missing scopes remain unavailable."),
    Command("engagement draft", "Store exact response text bound to collected account and reply context.", "LOCAL_STATE_WRITE", False),
    Command("engagement send", "Preview or explicitly send one reviewed reply with a durable outcome record.", "EXTERNAL_PROVIDER_EFFECT", True, "omit --live", notes="--live requires --expected-sha256 and fresh exact context. Crash/ambiguous/terminal effects block repeats. No automatic replies."),
    Command("engagement dismiss", "Dismiss a pending reply with a recorded reason.", "LOCAL_STATE_WRITE", False),
    Command("portfolio plan", "Dry-plan the rolling portfolio horizon with diversity controls.", "READ_ONLY", examples=("ocpf-post portfolio plan --horizon-minutes 720",)),
    Command("portfolio refill", "Plan or create durable near-term portfolio reservations.", "LOCAL_STATE_WRITE", False, "omit --apply", ("ocpf-post portfolio refill", "ocpf-post portfolio refill --apply"), "--apply writes schedules; it never publishes directly."),
    Command("portfolio reconcile", "Cancel allocator-owned schedules whose campaign is no longer fresh.", "FUTURE_CONSEQUENCE", False),
    Command("portfolio policy", "Show or update the private portfolio policy.", "FUTURE_CONSEQUENCE", False, "omit --write-default and --selection", notes="--selection fair|legacy changes future allocation; preserves cadence and existing reservations. --write-default resets policy only with --force when present."),
    Command("portfolio snapshot", "Capture stable allocator inputs and a reproducible baseline.", "LOCAL_STATE_WRITE", False, "omit --output", notes="No runtime state mutation; optional new private JSON artifact. No credentials or social copy text."),
    Command("portfolio replay", "Compare baseline and fair selection from a frozen queue, including waiting and expiry risk.", "LOCAL_STATE_WRITE", False, "omit --output", examples=("ocpf-post portfolio replay --file queue.json",), notes="Entirely offline; optional new private JSON artifact. Never creates schedules or posts."),
    Command("portfolio watch", "Inspect automatic queue age, deadline and unresolved outcome supervision.", "LOCAL_STATE_WRITE", False, "omit --apply", notes="Every applied refill observes before and after allocation. --apply records local evidence only. Alerts are in health/journal, not email or push. Exit 0=ok, 2=unknown/stale/unavailable, 3=attention."),
    Command("replenish status", "Show runtime campaign inventory and GitHub source cursor state.", "READ_ONLY"),
    Command("replenish lock-status", "Inspect the source-operation coordination lease.", "READ_ONLY", examples=("ocpf-post replenish lock-status --json",), notes="Point-in-time lease observation only. Never breaks or deletes an active lock."),
    Command("replenish wait", "Wait a bounded interval for the source-operation lock to become available.", "READ_ONLY", examples=("ocpf-post replenish wait --timeout 30 --json",), notes="Does not reserve the lock after it becomes available; the subsequent operation must acquire it normally."),
    Command("replenish acknowledge-gap", "Review and archive a source gap before explicitly resuming observation from its head.", "FUTURE_CONSEQUENCE", safe_form="omit --apply", notes="Apply requires the exact review hash. Unobserved history is explicitly acknowledged; old evidence, campaigns and schedules are retained."),
    Command("replenish observe", "Collect bounded source revisions independently of inventory pressure.", "LOCAL_STATE_WRITE", safe_form="omit --apply", notes="Saved observations and pending evidence do not create campaigns or advance consumed-event cursors."),
    Command("portfolio calendar", "Join saved reservations, observed outcomes and provisional selections by account.", "LOCAL_STATE_WRITE", safe_form="omit --save", notes="Read-only unless --save writes the local report. Forecasts are not reservations."),
    Command("portfolio volume-audit", "Compare one local day of durable schedules with receipt-backed publication effects and adjacent-day volume.", "READ_ONLY", examples=("ocpf-post portfolio volume-audit --date 2026-09-20 --json",), notes="Local ledgers only. No provider call, refill, reservation, replay or causal claim; non-published schedule outcomes remain explicit."),
    Command("replenish recover-generated", "Preview or recover reviewed legacy generated copy as fresh successor inventory.", "FUTURE_CONSEQUENCE", False, "omit --apply", examples=("ocpf-post replenish recover-generated --review-file linkedin_recoverable.json --provider linkedin --json",), notes="Apply requires the exact review_sha256 from a fresh preview. Creates successor COPY-READY inventory with fresh scoped admission only; never rewrites legacy manifests, schedules posts or calls a social provider."),
    Command("replenish refresh", "Admit saved source evidence per destination within inventory budgets.", "FUTURE_CONSEQUENCE", False, "omit --apply", notes="--apply consumes saved fresh observations per destination without GitHub reads or expiry renewal. Observed and consumed revisions are separate. --project limits admission to one enabled source."),
    Command("replenish reconcile", "Disable runtime campaigns superseded by a newer source snapshot.", "LOCAL_STATE_WRITE"),
    Command("outcome-connectors status", "Inspect registered receipt-linked HTTPS outcome connectors and cursor state.", "READ_ONLY"),
    Command("outcome-connectors import", "Preview or register an inactive bounded HTTPS outcome source.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="Registration stores endpoint/source authority only; credentials and activation are separate."),
    Command("outcome-connectors connect", "Store a private bearer credential for an inactive outcome connector.", "AUTHORITY_CHANGE"),
    Command("outcome-connectors enable", "Preview or enable bounded pull-only outcome ingestion.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="Remote events still must match exact verified publication identities."),
    Command("outcome-connectors disable", "Disable future outcome-source reads while preserving imported evidence.", "AUTHORITY_CHANGE"),
    Command("outcome-connectors sync", "Pull bounded authorised outcome pages and persist exact receipt-linked business events.", "LOCAL_STATE_WRITE", safe_form="omit --apply", notes="Public HTTPS only, durable cursor after ingest, idempotent replay; no causal inference or social-provider consequence."),
    Command("alerts status", "Inspect configured incident-notification delivery state.", "READ_ONLY"),
    Command("alerts configure", "Preview or configure an inactive public-HTTPS incident notification destination.", "AUTHORITY_CHANGE", safe_form="omit --apply"),
    Command("alerts connect", "Store a private bearer credential for the alert destination.", "AUTHORITY_CHANGE"),
    Command("alerts enable", "Preview or enable deduplicated incident notification delivery.", "AUTHORITY_CHANGE", safe_form="omit --apply"),
    Command("alerts disable", "Disable future incident notifications while preserving incident state.", "AUTHORITY_CHANGE"),
    Command("alerts send", "Deliver new/resolved deduplicated operating incidents to the authorised endpoint.", "EXTERNAL_PROVIDER_EFFECT", True, "omit --apply", notes="External effect is notification delivery only; it cannot publish, repair, retry or acknowledge social consequences."),
    Command("capabilities", "Show implementation, configuration, authority, evidence and acceptance state.", "READ_ONLY"),
    Command("work status", "Show one generated current-work projection with blockers, deadlines, dependencies and no-replay/no-renew flags.", "READ_ONLY", examples=("ocpf-post work status", "ocpf-post work status --json"), notes="Consolidates existing local evidence only; it never performs the suggested actions."),
    Command("config show", "Show effective configuration origins and policy presence without secret values.", "READ_ONLY"),
    Command("credentials keyring status", "Inspect optional OS keyring/keychain backend and provider credential presence.", "READ_ONLY"),
    Command("credentials keyring migrate", "Preview or migrate one default provider credential from a private file into the OS keyring.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="Apply requires an exact review hash; the file is removed only after keyring readback verification."),
    Command("credentials keyring restore", "Preview or restore one provider credential from the OS keyring to a private mode-0600 file.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="The private file is fsync'd before keyring authority is removed."),
    Command("state inventory", "Fingerprint and classify local state/config files without exposing contents.", "READ_ONLY"),
    Command("state verify", "Validate structured state and fail closed on corrupt durable ledgers.", "READ_ONLY"),
    Command("state archive", "Preview or create a consistent credential-free state-evidence archive.", "LOCAL_STATE_WRITE", safe_form="omit --apply"),
    Command("state migrate", "Preview or apply explicitly implemented schema migrations.", "LOCAL_STATE_WRITE", safe_form="omit --apply", notes="There is no implicit repair; corrupt evidence blocks migration."),
    Command("state segment", "Preview or move an immutable prefix of a critical ledger into numbered segments while preserving logical order.", "LOCAL_STATE_WRITE", safe_form="omit --apply", notes="Apply requires an exact review hash and shares the writer lock with new ledger appends."),
    Command("state recover-segment", "Preview or recover an interrupted critical-ledger segmentation transaction.", "LOCAL_STATE_WRITE", safe_form="omit --apply", notes="Recovery completes or rolls back only the structural move; ledger rows are never edited."),
    Command("runtime status", "Attest the checkout, local state integrity and capability readiness.", "READ_ONLY"),
    Command("runtime install", "Preview or install/reconcile Post-Once user automation and console units.", "AUTHORITY_CHANGE", safe_form="omit --apply"),
    Command("runtime uninstall", "Preview or remove Post-Once user units without deleting evidence.", "AUTHORITY_CHANGE", safe_form="omit --apply"),
    Command("runtime reconcile", "Preview or safely restore user-unit paths to the current clean checkout.", "AUTHORITY_CHANGE", safe_form="omit --apply"),
    Command("runtime upgrade", "Preview or switch user-unit software to one exact locally available Git revision.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="Requires state integrity and an exact review hash; never fetches Git or rolls evidence backwards."),
    Command("runtime rollback", "Preview or switch software to the previously recorded compatible revision.", "AUTHORITY_CHANGE", safe_form="omit --apply", notes="Software rollback only; publication/schedule evidence remains current."),
    Command("console", "Serve or snapshot the loopback-only read-only observability console.", "READ_ONLY", examples=("ocpf-post console", "ocpf-post console --snapshot")),
    Command("help", "Discover the complete CLI capability catalogue in text or JSON.", "READ_ONLY", examples=("ocpf-post help", "ocpf-post help --json", "ocpf-post help portfolio refill --json")),
)


def catalogue() -> dict[str, Any]:
    from ocpf_post.cli_surface import command_surface
    surface = command_surface()
    return {
        "schema_version": SCHEMA_VERSION,
        "cli_version": __version__,
        "consequence_classes": {
            "READ_ONLY": "Reads local/provider state without intended mutation.",
            "LOCAL_STATE_WRITE": "Mutates post-once local/runtime state but does not itself create a provider post.",
            "FUTURE_CONSEQUENCE": "Creates/cancels authority for a future provider consequence.",
            "EXTERNAL_PROVIDER_EFFECT": "Can create an external provider/network consequence; command notes distinguish social publishing from notification delivery.",
            "AUTHORITY_CHANGE": "Changes provider authorisation/token state.",
        },
        "commands": [
            {**asdict(command), "arguments": surface.get(command.path, {}).get("arguments", [])}
            for command in COMMANDS
        ],
    }


def scoped(path_parts: list[str]) -> list[Command]:
    prefix = " ".join(path_parts).strip()
    if not prefix:
        return list(COMMANDS)
    exact = [c for c in COMMANDS if c.path == prefix]
    children = [c for c in COMMANDS if c.path.startswith(prefix + " ")]
    return exact + children


def _argument_summary(row: dict[str, Any]) -> str:
    label = ", ".join(row.get("flags") or []) or str(row.get("name") or "")
    parts = [label]
    if row.get("required"):
        parts.append("required")
    choices = row.get("choices")
    if choices:
        parts.append("choices=" + "|".join(str(value) for value in choices))
    default = row.get("default")
    if default not in (None, False, "", []):
        parts.append("default=" + str(default))
    return " · ".join(parts)


def render_text(path_parts: list[str]) -> str:
    rows = scoped(path_parts)
    if not rows:
        raise ValueError(f"Unknown help path: {' '.join(path_parts)}")
    from ocpf_post.cli_surface import command_surface
    surface = command_surface()
    lines = [f"post-once {__version__}", "Agent-operable publishing control plane", ""]
    lines.append("CONSEQUENCE CLASSES: READ_ONLY | LOCAL_STATE_WRITE | FUTURE_CONSEQUENCE | EXTERNAL_PROVIDER_EFFECT | AUTHORITY_CHANGE")
    lines.append("")
    for command in rows:
        marker = " [EXTERNAL EFFECT]" if command.external_effect else ""
        lines.append(f"{command.path:<28} {command.consequence:<24}{marker}")
        lines.append(f"  {command.summary}")
        for argument in surface.get(command.path, {}).get("arguments", []):
            lines.append(f"  ARG {_argument_summary(argument)}")
            if argument.get("help"):
                lines.append(f"      {argument['help']}")
        if command.safe_form:
            lines.append(f"  Safe inspection form: {command.safe_form}")
        if command.notes:
            lines.append(f"  Note: {command.notes}")
        for example in command.examples:
            lines.append(f"  Example: {example}")
        lines.append("")
    lines.append("Machine-readable discovery: ocpf-post help --json")
    lines.append("Manual: man ocpf-post")
    lines.append("Operational model: docs/OPERATING_MODEL.md")
    return "\n".join(lines).rstrip() + "\n"


def render_json(path_parts: list[str]) -> str:
    data = catalogue()
    rows = scoped(path_parts)
    if not rows:
        raise ValueError(f"Unknown help path: {' '.join(path_parts)}")
    data["commands"] = [asdict(c) for c in rows]
    data["scope"] = " ".join(path_parts) or None
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
