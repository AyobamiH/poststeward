# Install PostSteward

PostSteward is a distributed product: **PostSteward Cloud** supplies owner identity,
X/Threads/LinkedIn OAuth custody, machine coordination and the provider-effect relay;
the installed **PostSteward Local Runtime** supplies projects/campaigns, portfolio
planning, schedules, durable local evidence, Setup & Recovery and local agent
operation.

Provider OAuth secrets never need to be copied into the installed runtime.

## macOS / Linux / WSL2

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh | bash
```

The no-root installer resolves one reviewed release to an exact Git revision, verifies
the embedded runtime tree before promotion, installs a user-local release under the
PostSteward XDG namespace and creates the canonical `poststeward` command.

Useful installer modes:

```bash
# install without opening onboarding
curl -fsSL https://poststeward.com/install.sh | bash -s -- --no-onboard

# preview without mutation
curl -fsSL https://poststeward.com/install.sh | bash -s -- --dry-run

# beta channel
curl -fsSL https://poststeward.com/install.sh | bash -s -- --beta

# exact reviewed revision
curl -fsSL https://poststeward.com/install.sh | bash -s -- --version <40-char-sha>
```

## 1. Pair the machine

```bash
poststeward onboard
```

The runtime creates a short-lived pairing request and opens the owner workspace. The
browser shows the exact installation ID, host/platform and pairing code. Owner
approval returns a separate installation-bound runtime token once; it does **not**
grant provider/admin authority and does not turn on publishing automation.

## 2. Connect destinations in the owner workspace

Connect any combination of:

- **X** — OAuth 2.0 stable identity;
- **Threads** — OAuth plus long-lived refresh handling;
- **LinkedIn** — member identity or an explicitly reviewed organization/Page actor.

Application configuration, owner consent, connected stable identity, executor
authority and publication are separate states.

Provider credentials remain encrypted in PostSteward Cloud. The paired runtime receives
only non-secret account bindings and capability evidence.

## 3. Bind hosted destinations to local Fresh state

```bash
poststeward configure \
  --project example-project \
  --label "Example Project" \
  --timezone Europe/London \
  --pace regular \
  --text-file first-reviewed-post.txt
```

Preview is read-only apart from Fresh setup bookkeeping. It verifies the hosted
provider identities and prints an exact review SHA. Apply only that exact review:

```bash
poststeward configure \
  --project example-project \
  --label "Example Project" \
  --timezone Europe/London \
  --pace regular \
  --text-file first-reviewed-post.txt \
  --apply \
  --expected-sha256 <review-sha256>
```

The imported campaign remains manual-only; onboarding does not allocate, schedule or
publish it.

## 4. Inspect before activation

```bash
poststeward status --json
poststeward doctor --json
poststeward help --json
```

The owner workspace must explicitly hand the workspace executor from `hosted` to this
paired installation. Every handoff increments a cloud authority generation and is
review-digest bound.

Then review local activation:

```bash
poststeward activate --json
poststeward activate --apply --expected-sha256 <review-sha256> --json
```

Activation requires the local A–K marker **and** the matching live cloud executor
lease/generation.

## One provider-effect path

When local execution is active:

```text
local schedule / run-due
 -> exact local effect ID + text digest
 -> live executor lease + generation
 -> PostSteward Cloud provider relay
 -> existing D1 external-effect fence
 -> X / Threads / LinkedIn
 -> provider receipt/readback
 -> mirrored local evidence
```

The cloud does not run a competing scheduler in local mode. A stale machine, stale
generation, expired lease or changed payload is rejected before provider I/O.

## Humans and agents

Humans own OAuth consent, account/Page selection, machine pairing, executor changes,
recovery and administrative authority.

Agents can operate the local deterministic runtime and use owner-issued remote MCP/HTTP
grants. Admin authority is not delegated through agent grants.

Useful local surfaces include:

```bash
poststeward help --json
poststeward status --json
poststeward doctor --json
poststeward portfolio status --json
poststeward schedule list
poststeward receipts list
poststeward cloud mcp
```

## Deactivate and update

Deactivation is review-bound and closes cloud executor authority before local timers:

```bash
poststeward deactivate --reason "maintenance" --json
poststeward deactivate \
  --reason "maintenance" \
  --apply \
  --expected-sha256 <review-sha256> \
  --json
```

A cloud outage never prevents local risk reduction; it also never promotes hosted
execution automatically.

Change releases only while local publishing authority is inactive:

```bash
poststeward update --channel stable --dry-run
poststeward update --channel stable
```

## Original Post-Once boundary

The user's installation never points at the owner's historical `AyobamiH/post-once`
state, credentials or services. The embedded runtime provenance is recorded in the
PostSteward release and guarded by the A–K regression suite.

See [Architecture](../../docs/ARCHITECTURE.md) and
[Engineering insights](../../docs/ENGINEERING_INSIGHTS.md).
