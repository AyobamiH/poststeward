#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ocpf_post import __version__  # noqa: E402
from ocpf_post.cli_catalog import COMMANDS, catalogue  # noqa: E402
from ocpf_post.cli_surface import command_surface  # noqa: E402
from ocpf_post.completion_gen import (  # noqa: E402
    bash_completion as parser_bash_completion,
    fish_completion as parser_fish_completion,
    zsh_completion as parser_zsh_completion,
)


def cli_reference() -> str:
    lines = [
        "# post-once CLI Reference",
        "",
        "> GENERATED FILE. Do not edit by hand. Run `python3 scripts/generate-cli-docs.py`.",
        "",
        "Canonical runtime discovery: `ocpf-post help --json`.",
        "",
        "| Command | Consequence | External effect | Safe inspection form | Purpose |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in COMMANDS:
        lines.append(
            f"| `{c.path}` | `{c.consequence}` | {'yes' if c.external_effect else 'no'} | "
            f"{f'`{c.safe_form}`' if c.safe_form else '—'} | {c.summary} |"
        )
    lines += ["", "## Consequence classes", ""]
    for name, description in catalogue()["consequence_classes"].items():
        lines.append(f"- **`{name}`** — {description}")
    lines += ["", "## Examples", ""]
    for c in COMMANDS:
        for example in c.examples:
            lines.append(f"- `{example}`")
    return "\n".join(lines) + "\n"


def agent_cli() -> str:
    return """# Agent CLI Contract

> GENERATED FILE. Do not edit by hand. Run `python3 scripts/generate-cli-docs.py`.

Agents must discover the current surface before acting:

```bash
ocpf-post help --json
```

For a scoped task, request only the relevant branch:

```bash
ocpf-post help portfolio --json
ocpf-post help schedule --json
ocpf-post help run-due --json
```

## Decision rule

1. Prefer `READ_ONLY` commands for inspection.
2. Treat `LOCAL_STATE_WRITE` as mutation of post-once state, even when no provider call occurs.
3. Treat `FUTURE_CONSEQUENCE` as authority over a future external effect.
4. Treat `AUTHORITY_CHANGE` as provider credential/authorisation state mutation.
5. Treat `EXTERNAL_PROVIDER_EFFECT` as a real external consequence. Use the documented safe form first when one exists.
6. Never infer success from command invocation. Read schedule state, receipts or performance evidence as appropriate.

## Publishing boundaries

- `portfolio plan` is inspection only.
- `portfolio refill --apply` creates durable reservations but does not publish.
- `schedule create` creates a future reservation but does not publish immediately.
- `run-due` is the automated provider consequence boundary; `run-due --check` is inspection only.
- provider `publish --live` commands can create immediate external posts.
- terminal or ambiguous receipts exist to prevent blind duplicate effects.
- provider failures are classified for diagnosis only; a class such as rate-limited, usage-capped, posting-limited, automation-restricted or auth-rejected never grants replay authority.
- account-wide definite failures may open a bounded ledger-derived provider/account write circuit. Existing failed posts remain terminal; only schedules that have not begun consequence may be deferred safely.
- three consecutive generic forbidden failures on one provider/account, with no intervening success, also open the bounded circuit because repeated systemic failure is actionable even when the provider returns only `about:blank`.
- candidate-specific failures such as duplicate content do not open an account-wide circuit.
- console admission status is a fresh read-only evaluation; persisted hysteresis provenance is shown separately when it differs.

## Documentation definition of done

A public command, flag, consequence boundary or workflow change is incomplete until canonical command metadata and generated documentation are updated. CI runs the generator in `--check` mode and validates the parser surface against the catalogue.

## Platform preflight

Before consequential or repair-oriented work, inspect the platform itself:

```bash
ocpf-post state verify
ocpf-post capabilities
ocpf-post work status --json
ocpf-post runtime status
```

These commands distinguish implementation, configuration, authority, evidence and acceptance. They never repair state or grant provider authority.
"""


def operating_model() -> str:
    return """# post-once Operating Model

> GENERATED FILE. Do not edit by hand. Run `python3 scripts/generate-cli-docs.py`.

## Operator mental model

Post-Once scheduling is automatic after eligible inventory reaches the allocator.

```text
Supply
-> Admission
-> Automatic schedule
-> Execute
-> Provider
-> Verify
```

The living console derives this operator model and its current **why volume is low**
diagnosis from the same read-only SSE snapshot used by the execution map. Multi-day
reserve status/runway, admission state, durable schedules, provider receipts and
verification evidence are projected together rather than copied into a second
dashboard model. Admission shows a fresh read-only evaluation separately from its
persisted hysteresis state, so an old controller decision cannot masquerade as a
current gate. Definite provider rejections are classified by safe stage, HTTP status
and provider problem/code where available without exposing provider prose or
creating retry authority. Account-wide definite failures feed a bounded,
ledger-derived provider write circuit that suppresses new write attempts and defers
already-scheduled work before consequence. Three consecutive generic forbidden
failures with no intervening success also open the circuit, while candidate-specific
failures do not.

The live console validates only authority-bearing critical ledgers and reuses the
same snapshot's receipt, engagement, outcome and alert projections. It deliberately
does not walk and fingerprint the complete state/config tree on every live refresh;
`ocpf-post state verify` remains the explicit full forensic check. Snapshot JSON
includes `projection_timing_ms` outside the state revision so performance can be
diagnosed without manufacturing state changes.

A low publication count does not mean scheduling is manual. Protected multi-day
reserve is distinct from current eligible, planned and scheduled work: reserve can
be empty while today's already-eligible work continues. Only current inventory and
schedule evidence can establish that the scheduler has nothing to reserve. The 100
logical-publications/account/day limit is a safety circuit breaker, not a content
generator or posting target.

The diagnosis explains current local evidence only. Historical volume questions
still require dated schedule-versus-receipt evidence. Use
`ocpf-post portfolio volume-audit --date YYYY-MM-DD --json` to compare the target
local day with its adjacent days and expose non-published schedule outcomes without
calling a provider; the console does not infer causality from today's reserve state.

## Automated portfolio path

Steady originals is an explicit owner-reviewed release policy. It reuses saved
per-account daily amounts while retaining the separate 100/account/day safety
ceiling. More fresh reserve does not create more daily release opportunities.
When this policy is active, reserve protection is sized from that saved account
pace, shared across its configured project routes; historical publication bursts
remain descriptive evidence and cannot enlarge future reserve demand. One complete
thread remains one original; thread parts and API requests are separate units.
Existing bookings are honoured, not rewritten. See `docs/STEADY_RELEASE.md` and
`scripts/steady-release.py` for preview and activation.

The normal portfolio supply path is vault-first. ChatGPT Work maintains reviewed
Google Drive/Docs GTM vaults from current product truth and durable consumption
telemetry, including useful audience-recognition content during technically quiet
periods. A lack of repository movement does not stop market continuity.

```text
current product/repository truth + Post-Once consumption
-> ChatGPT Work editorial review
-> canonical Google Drive/Docs vault
-> reviewed APPROVED entry
-> vault collector/sync
-> scoped Post-Once admission
-> accepted inventory
-> fair service selection
-> durable schedule
-> run-due
-> provider receipt/readback
-> performance and audience evidence
-> bounded learning
```

The deterministic GitHub evidence replenisher remains an independent supply path.
Optional OpenAI API authoring is a bounded fallback after vault/saved-source supply;
it is not required for normal owner operation and one daily allowance applies
portfolio-wide rather than once per project.

Runtime onboarding starts with `registry import --file project.json` and
`campaign import --file campaign.json`. These commands preview by default;
`--apply --expected-sha256 HASH` saves the reviewed file. Project import adds
expected local account bindings, not credentials or verified identity. Campaigns
remain manual-only unless `--allocate` explicitly authorises future allocator
selection. See `docs/RUNTIME_ONBOARDING.md`.

Source onboarding uses `replenish source import` to register an inactive policy,
`replenish source preview` to inspect GitHub and rendered copy, and
`replenish source enable --expected-sha256 HASH` to authorise future replenishment.
`replenish source disable` stops new generation; existing campaigns and schedules
retain their prior authority. See `docs/RUNTIME_SOURCES.md`.

```text
allowlisted GitHub sources
-> replenish observe --apply (independent 15-minute collector)
-> saved revision evidence
-> replenish refresh --apply (scoped inventory admission)
-> runtime COPY-READY campaigns
-> replenish reconcile
-> portfolio reconcile
-> portfolio refill --apply
-> durable schedules
-> run-due
-> provider receipt/readback
-> performance capture
```

`run-due` remains the campaign publisher. A separate opt-in conversation worker can send reviewed replies through its own durable receipt path. The publisher wakes every minute; refill remains every 15 minutes with a 75-minute reservation horizon. `portfolio calendar` joins saved reservations, observed outcomes and provisional selections. Source/vault/metrics/reply collection runs separately with bounded stages; reports and local incidents are described in `docs/RUNTIME_IMPROVEMENTS.md`.

## Manual one-shot path

```text
campaign show / provider status
-> publish dry run
-> publish --live
-> receipt
```

## Manual future-schedule path

```text
schedule create
-> schedule list
-> run-due --check
-> run-due
-> schedule/receipt evidence
```

## Threads consequence path

Threads text publication uses Meta's single consequential request:

```text
POST /me/threads with auto_publish_text=true
-> durable provider id receipt
-> readback
-> verified receipt when id/text agree
```

Uncertainty from the first POST blocks a blind retry. The historical two-stage
container/publish flow is not the current text path. See `docs/THREADS_PUBLISHING.md`.

## Extending a vault feed

`vault extend --vault-id ID --provider threads --account ALIAS` previews an add-only
provider destination and current approved copy. Apply the exact review hash to
extend an enabled vault. Provider-specific approved sections are read only when
that provider is authorised; existing destinations and historical receipts remain
intact. The independent collector syncs future approved revisions, even while new inventory admission is paused. See `docs/VAULT_SYNC.md`.

## Planning controls

Portfolio planning combines freshness, lane quotas, same-topic cooling, project/family diversity and cross-platform topic separation. Planning is consequence-free; reservations are created only by `portfolio refill --apply`.

## Reviewed evidence briefs

`campaign brief --file brief.json` previews exact copy compiled from reviewed
claims, evidence and distinct editorial angles. `--apply --expected-sha256 HASH`
imports through the existing campaign path; `--allocate` is explicit. References
are not independently verified and semantic quality still needs editorial review.
`replenish source receipts --project PROJECT` separately tracks matching generated
source schedules and publication receipts across revisions. See
`docs/REVIEWED_EVIDENCE_BRIEFS.md`. Direct/brief imports do not close that milestone.

## Evidence and copy inspection

`replenish source evidence --project PROJECT` reads exact-commit GitHub workflow
and deployment observations. It changes no source cursor or publishing authority.
`campaign explain --campaign CAMPAIGN --provider x --json` joins local copy,
provenance, allocation exclusions and publication receipts without network calls.
Automatic allocation excludes exact copy already reserved/recorded for the same
provider/account and shows calendar-day budget usage. This is not a global daily
cap or semantic similarity detection. See `docs/EVIDENCE_AND_COPY.md`.

## Knowledge contract

Run `ocpf-post work status --json` for one generated current-work projection
covering editorial continuity, deadlines, readback/manual-review gates, learning,
external integrations and time-based evidence. It is read-only: suggested actions
never execute merely because they appear in this view.

When source admission is already running, use `ocpf-post replenish lock-status --json`
or bounded `ocpf-post replenish wait --timeout 30 --json` rather than shell polling
or breaking lock files. Commands that advertise `--json` preserve a structured
error envelope on failure.

Run `ocpf-post health --json` to inspect local publication evidence, freshness,
capacity, source observations and user timer state without publishing or repair.
See `docs/HEALTH.md` for scope, thresholds and exit codes, and
`docs/CONTINUITY.md` for the evidence-driven development direction.

The command catalogue in `src/ocpf_post/cli_catalog.py` is the canonical discovery metadata. `ocpf-post --help`, `ocpf-post help`, `ocpf-post help --json`, and these generated files must agree. CI rejects parser/catalogue drift and generated-document drift.

## Build-to-market control plane, 0.27.0

Editorial continuity now tracks usable stock runway, expiry runway, recent provider
effects, near-term schedules and a bounded market-cold condition per route. These
are saturation/continuity signals, not publication quotas. The read-only living
console displays the deterministic repository-evidence path and the ChatGPT
Work/Drive-vault path as separate inputs that converge at Post-Once admission.

Release metadata is checked in CI across package, runtime, README, current-state and
changelog surfaces so a release cannot silently retain an older current-version
claim.

## Platform consolidation, 0.26.0

Post-Once now exposes one platform-level inspection path: `state verify` for durable-ledger integrity, `capabilities` for implemented/configured/authorised/observed/accepted readiness, and `runtime status` for checkout/state/capability attestation. The loopback console consumes the same readiness state.

Outcome connectors and alert delivery are separately configured capabilities. Their implementation does not imply a live endpoint, authority or successful delivery. LinkedIn organisation/Page actors are separately scoped from member publishing and remain subject to provider permission and reviewed enablement.
"""


def _roff(value: str) -> str:
    return value.replace("\\", "\\\\").replace("-", "\\-")


def _command_tree() -> dict:
    result = {}
    for command in COMMANDS:
        node = result
        for part in command.path.split():
            node = node.setdefault(part, {})
    return result


def man_page() -> str:
    lines = [
        f".TH OCPF-POST 1 \"21 September 2026\" \"post-once {__version__}\" \"Post-Once Manual\"",
        ".SH NAME",
        "ocpf-post \\- receipt-backed social publishing, scheduling and portfolio operations",
        ".SH SYNOPSIS",
        ".B ocpf-post",
        ".I command",
        ".RI [ options ]",
        ".SH DESCRIPTION",
        "Post-Once is a fail-closed operator control plane. Commands are classified by consequence and durable external effects are receipt-backed.",
        "Use \\fBocpf-post help COMMAND --json\\fR for machine-readable argument/default metadata.",
        ".SH AUTOMATIC PORTFOLIO MODEL",
        "Normal portfolio scheduling is automatic after eligible inventory reaches the allocator:",
        "Supply -> Admission -> Automatic schedule -> Execute -> Provider -> Verify.",
        "A low publication count can therefore come from upstream reserve or admission pressure even when the scheduler is healthy.",
        "The 100 logical-publications/account/day boundary is a safety circuit breaker, not a content generator or posting target.",
        "The living console derives its current why-volume-is-low diagnosis from the same read-only snapshot; historical causality still requires dated schedule-versus-receipt evidence.",
        "Admission is freshly evaluated read-only on each operator snapshot while persisted hysteresis provenance is shown separately. Provider rejection classes are diagnostic only and never grant automatic replay authority.",
        "Account-wide definite provider failures feed a bounded ledger-derived write circuit. The circuit suppresses new reservations and defers only pre-consequence scheduled work; three consecutive generic forbidden failures also open it, while candidate-specific failures do not.",
        ".SH COMMANDS",
    ]
    for command in COMMANDS:
        lines.extend([
            ".TP",
            f"\\fBocpf\\-post {_roff(command.path)}\\fR",
            _roff(command.summary) + f" Consequence: {command.consequence}."
            + (f" Safe inspection: {command.safe_form}." if command.safe_form else "")
            + (f" {command.notes}" if command.notes else ""),
        ])
    lines.extend([
        ".SH SAFETY",
        "Published, ambiguous and executing effects are not blind-retry authority. Inspect receipts/readback before any replacement consequence.",
        ".SH DISCOVERY",
        "Run \\fBocpf-post help --json\\fR for the complete catalogue and parser arguments.",
        ".SH FILES",
        "~/.config/oneclickpostfactory/post-once/ contains local authority/configuration. ~/.local/state/oneclickpostfactory/post-once/ contains durable local evidence by default.",
        ".SH SEE ALSO",
        "ocpf-post help, ocpf-post doctor, ocpf-post health, ocpf-post capabilities, ocpf-post state verify, ocpf-post runtime status",
    ])
    return "\n".join(lines) + "\n"


def bash_completion() -> str:
    tree = _command_tree()
    top = " ".join(sorted(tree))
    lines = [
        "# GENERATED by scripts/generate-cli-docs.py",
        "_ocpf_post_complete() {",
        "  local cur",
        "  COMPREPLY=()",
        "  cur=\"${COMP_WORDS[COMP_CWORD]}\"",
        "  if [[ $cur == -* ]]; then COMPREPLY=( $(compgen -W \"--help --version --json --apply --live --check\" -- \"$cur\") ); return; fi",
        f"  if (( COMP_CWORD == 1 )); then COMPREPLY=( $(compgen -W \"{top}\" -- \"$cur\") ); return; fi",
        "  case \"${COMP_WORDS[1]}\" in",
    ]
    for family, children in sorted(tree.items()):
        if children:
            values = " ".join(sorted(children))
            lines.append(f"    {family}) COMPREPLY=( $(compgen -W \"{values}\" -- \"$cur\") ) ;;")
    lines.extend(["  esac", "}", "complete -F _ocpf_post_complete ocpf-post"])
    return "\n".join(lines) + "\n"


def zsh_completion() -> str:
    tree = _command_tree()
    top = " ".join(sorted(tree))
    return "#compdef ocpf-post\n# GENERATED by scripts/generate-cli-docs.py\n_arguments \"1:command:(" + top + ")\" \"*::arg:->args\"\n"


def fish_completion() -> str:
    tree = _command_tree()
    lines = ["# GENERATED by scripts/generate-cli-docs.py", "complete -c ocpf-post -f"]
    for name in sorted(tree):
        lines.append(f"complete -c ocpf-post -n '__fish_use_subcommand' -a '{name}'")
    for family, children in sorted(tree.items()):
        for child in sorted(children):
            lines.append(f"complete -c ocpf-post -n '__fish_seen_subcommand_from {family}' -a '{child}'")
    return "\n".join(lines) + "\n"


FILES = {
    ROOT / "docs" / "CLI_REFERENCE.md": cli_reference,
    ROOT / "docs" / "AGENT_CLI.md": agent_cli,
    ROOT / "docs" / "OPERATING_MODEL.md": operating_model,
    ROOT / "man" / "ocpf-post.1": man_page,
    ROOT / "completions" / "ocpf-post.bash": lambda: parser_bash_completion(COMMANDS, command_surface()),
    ROOT / "completions" / "_ocpf-post": lambda: parser_zsh_completion(COMMANDS, command_surface()),
    ROOT / "completions" / "ocpf-post.fish": lambda: parser_fish_completion(COMMANDS, command_surface()),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, renderer in FILES.items():
        expected = renderer()
        if args.check:
            actual = path.read_text(encoding="utf-8") if path.exists() else None
            if actual != expected:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
    if stale:
        print("Generated CLI documentation is stale:")
        for path in stale:
            print(f"  {path}")
        print("Run: python3 scripts/generate-cli-docs.py")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

