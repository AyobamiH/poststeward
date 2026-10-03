# Installable-runtime integration checkpoint

Historical 30 September checkpoint. Convergence and branded distribution have since
been deployed, and public signup deliberately enabled. For current status and remaining
real owner/provider gates, use [DEVELOPMENT_COMPLETION.md](DEVELOPMENT_COMPLETION.md).
The 404 and unmerged-branch observations below describe that earlier checkpoint only.

This records the work resumed on 30 September 2026 from
`feat/installable-poststeward-runtime` at
`59d2d20d018b1189a2e2ec18735758cd0d08791c`. The normative product contract remains
[ARCHITECTURE.md](ARCHITECTURE.md), including one executor per workspace generation
and hosted provider credential custody.

## Reproduced integration regressions

The last port changed two contracts without updating their control-plane fixtures:

- Source-pipeline coordination now owns the source/admission lock order. The test
  patched a removed `admission_runtime.local_store` symbol before reaching its
  contention assertion. It now tests the typed paused result at the public wrapper.
  Existing admission tests exercise real lock contention and writer ordering.
- Emergency generation defaults to Work/Docs; paid API generation requires explicit
  `OCPF_POST_GENERATIVE_SUPPLY_MODE=api`. The portfolio-budget test now selects that
  mode. A complementary test proves the default reports no paid API budget and does
  not query API usage.

Both Python 3.10 and 3.12 completed all 1,247 embedded tests after committing these
fixture corrections. The hosted suite also completed 538 tests before adding the
installer acceptance coverage below. These counts are checkpoint evidence, not
proof of deployment or external provider behavior.

## Installer findings and protection

`tests/runtime-installer.test.mjs` exercises the actual shell installer against a
Git archive of the current committed head and generated channel metadata. Only
HTTPS transport is replaced with fixture delivery; runtime execution, tree hashing,
installation, receipt writes and CLI diagnosis are real.

The checks found and repaired two installed-product defects:

1. Depth-first manifest hashing differed from the installer's globally sorted path
   order. Channel installation rejected valid runtime archives. The generator now
   globally sorts paths and rejects symbolic-link entries.
2. The installed launcher retained its `current` symlink as the runtime root. Product
   diagnosis and activation require a plain release directory. `pwd -P` now resolves
   the managed symlink before entering the runtime.

Additional checks cover dry-run non-mutation, exact-revision reinstall and retained
state, command collisions, manifest expiry, digest mismatch, altered retained
runtime, and active/malformed/unknown authority markers during release changes.
Unreadable authority evidence now blocks a release change instead of being treated
as inactive. Installed CLI checks deliberately inject old Post-Once/OCPF root
variables and assert PostSteward-owned paths and unchanged original-state evidence.

At checkpoint `332f6b1ce4e66b7ce44daa611c1a67a5241223da`, the full hosted verification
completed 548 tests and both Python versions completed 1,247 tests. Installing that
exact revision from its real public GitHub archive into isolated writable XDG roots
also succeeded. The installed `doctor --json` reported `ATTENTION` with
`RUNTIME_PAIRING_REQUIRED`, inactive automation and no publication receipt. This
network-backed archive installation did not use fixture transport or skip candidate
verification; it did not use the unavailable production-domain installer.

GitHub CI at that checkpoint passed both Python jobs but exposed a high-severity
development dependency advisory in Miniflare's pinned `undici@7.29.0`. The
development toolchain now overrides Undici to patched `7.30.0` while retaining the
Wrangler/Miniflare pins and the high-severity audit gate. The npm-provided archive
integrity is retained in the lockfile. Remaining moderate advisories must be kept
distinct from this high-severity gate and from demonstrated runtime behavior.

Generated `public/releases/*.json` files are build outputs, ignored by Git. They must
be generated from the exact release being bundled; a committed stale channel
manifest is not release authority.

Manifest/installer hashing also excludes generated Ruff and mypy caches. A local
build after static analysis must have the same runtime digest as its Git archive;
dedicated generator and retained-release tests protect this reproducibility rule.

The PR-triggered browser suite also found homepage install-panel contrast failures
and horizontal overflow at 320/390px. The panel now uses the existing semantic
surface/text tokens and wraps its command within a shrinkable hero column. Its
synthetic owner fixtures include the new read-only runtime installation/executor
endpoints, with explicit hosted-generation and disabled-handoff assertions for an
unpaired workspace. Unexpected API requests and all external effects remain
forbidden by the browser harness.

## Repeatable validation

Use separate virtual environments for the embedded runtime and the historical
repositories, because they share a Python distribution identifier. Do not modify or
install into the owner's original Post-Once environment.

```bash
# From the PostSteward checkout, using Node 24:
npm ci
npm run verify

# In separate Python 3.10 and 3.12 environments:
python -m pip install -e './runtime[dev]' 'setuptools>=68' wheel
cd runtime
python -m unittest discover -s tests -p 'test_*.py'
```

Run embedded release/activation tests on a committed, clean runtime subtree. Their
dirty-release refusal is intentional; do not mock or disable it to validate an
uncommitted candidate. On machines with a read-only home directory, set writable
XDG config/state/cache roots outside the checkout. Keep TLS and artifact digest
verification enabled.

## Open acceptance gates

On 30 September 2026, direct HTTPS requests to both
`https://poststeward.com/install.sh` and
`https://poststeward.com/releases/stable.json` returned HTTP 404. The public-domain
one-line installation path is therefore **not accepted**. An exact-revision GitHub
archive install and fixture channel install must be reported separately from that
production-domain outcome.

The convergence branch is a review candidate, not a public-beta or production
completion claim. Remaining gates include:

- review and required CI on the exact pushed integration head;
- deliberate deployment of the matching cloud code, forward D1 migrations and
  generated stable/beta metadata, followed by public-domain clean-machine install;
- real owner-approved pairing, verified provider identity, and reviewed executor
  handoff before local activation;
- one deliberately authorized campaign effect per selected provider, independent
  readback and matching local/cloud receipts; do not replay previously accepted
  historical effects just to refresh evidence;
- live remote-agent/local-executor bridge acceptance; implementation and adversarial
  tests are tracked in the distributed delivery milestones;
- real deactivation, upgrade/rollback, migration/recovery, and stale-machine drills;
- macOS/WSL/native Windows platform acceptance and intentional signup/launch policy.

No unattended publishing timers, live provider posts, executor handoffs, production
deployment or launch are established by the automated checks in this checkpoint.
