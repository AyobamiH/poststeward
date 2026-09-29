# Local operational health report

```bash
./ocpf-post health
./ocpf-post health --json
./ocpf-post health --hours 48 --source-age-minutes 90 --json
```

Run on the same machine and under the same user/config/state environment as the
publishing timers. No provider calls, token refresh, source fetch, posting,
reconciliation, reservation creation or repair is performed. The only child
process is a bounded `systemctl --user show` for the scheduler and refill units.

The report inspects:

- overdue schedules (default grace: 5 minutes), transient provider-preflight
  deferrals, stale executing claims (30 minutes), recent failures and safety guard blocks;
- stored payload hashes, current campaign drift, active/terminal overlap,
  repeated active reservations and multiple provider IDs for one campaign/account;
- schedule-to-receipt consistency and truthful verified/unverified states;
- inventory, the current rolling plan and configured daily targets;
- allocator reservation eligibility now and at due time, superseded runtime
  inventory and local source observation age (default: 60 minutes);
- runtime-generated campaigns reaching published receipts, and observed project,
  lane and topic counts;
- user timer load/active state and last service result;
- machine-readable command catalogue availability.

A schedule with `status=scheduled`, `failure_class=provider_unavailable` and a
future `retry_at` is reported as `preflight_deferred` warning rather than overdue.
That state means a read-only provider check failed before any publish request
began, the reservation remains active and the runner is deliberately observing
its persisted backoff. Once `retry_at` has passed, the original overdue rule
applies if the schedule still has not progressed, which keeps a dead/missed timer
visible as attention.

Receipt transitions from provisional to verified count as one external effect.
A later verification does not move an old publication into the reporting window.
Identity comparisons include the account. Ambiguity remains visible regardless
of the recent-failure window. Raw payloads, credentials, account IDs, service logs
and exception messages are not printed.

## Exit status

Since 0.13.0, health also reads the capacity experiment and engagement ledger.
Stale trial evidence cannot increase capacity. Reply collection failures/staleness,
waiting replies and unresolved send outcomes are visible without provider calls.
See [capacity and replies](GROWTH_EXPERIMENT.md) for coverage and limitations.

| Code | Meaning |
| --- | --- |
| 0 | No issue found in the inspected local scope |
| 1 | Warning: inventory, freshness, preflight deferral or safety-block observations need review |
| 2 | Unknown: incomplete, corrupt, unavailable or insufficient evidence |
| 3 | Attention: failures, overdue work, uncertain effects or inconsistent evidence |

The complete findings remain in JSON even when a higher-priority status determines
the exit code. `--skip-timers` supports non-systemd environments and explicitly
leaves timer health unknown. Thresholds accept positive integers only.

## Evidence limits

This is an observation, not a repair loop or a guarantee that providers are healthy.
No runtime publication evidence means unknown. LinkedIn `published_unverified`
is reported as such, not upgraded or counted as a failed publication. This command
does not capture fresh analytics; use the separate performance commands for that.

A preflight-deferral warning is also not publication evidence. It proves only that
the scheduler persisted a known pre-consequence availability failure and its next
retry boundary. It does not prove the provider is currently healthy or that the
future consequence will succeed.

Capacity is a rolling planning snapshot. Unreserved planned slots are not proof
the next timer will reserve them. Inventory shortfall is a warning, not a claim
that every daily slot has already been missed. Publication mix is descriptive,
not a quality judgement or proof of hard diversity-policy violations. Current
manifest metadata may differ from what existed when an old post was published.

Local source observations cannot prove the current upstream README/head. A source
fetch failure may leave an older successful observation; its age is the signal.
Corrupt evidence is reported as unavailable rather than silently discarded.
Concurrent writes to the main evidence files make cross-file conclusions
provisional; rerun after the current timer finishes. The report does not lock or
pause the publisher.

Keep the output local until reviewed. It contains project/campaign identifiers and
operational status, even though credentials and raw content are omitted.

Version 0.8.0 capacity also exposes the configured calendar day and allocation
budget used/remaining/target-met fields. These count reservations and conservative
terminal effects; they are distinct from rolling verified-publication totals.
Exact duplicate payloads to the same destination no longer inflate eligible
inventory. See EVIDENCE_AND_COPY.md for scope and inspection commands.

## Automatic queue supervision (0.12.0)

Every applied refill now maintains [queue supervision](QUEUE_SUPERVISION.md).
Health reads that state without creating or refreshing it. Waiting longer than
the configured threshold, approaching expiry and blocked eligibility produce
warnings; expired unpublished work and uncertain/inconsistent outcomes produce
attention. Missing or stale monitoring produces unknown, never a healthy empty
queue. Reports include per-project/provider waiting counts and oldest observed
waiting age. Local incidents are automatic. Optional bounded external alert delivery is available only after an authorised HTTPS destination is configured and enabled; local incident state remains authoritative.

## Portfolio outcome follow-through

Version 0.14.0 saves `operations --all --save` after each refill. See
[automatic portfolio outcome reports](OPERATIONS_REPORT.md) for exact receipt
matching, current named capacity blockers and snapshot freshness boundaries.
This report complements health; it does not probe providers or test timer state.

## Reply worker and collector outcomes (0.20.0)

When reply policy is enabled, health inspects the replies timer and service as
well as the three original unit pairs. A completed cycle older than 45 minutes
requires attention; a missing/invalid timestamp remains unknown. A deliberately
disabled worker does not require active reply units.

Collector subprocess exit success is distinct from provider success. Summaries
retain idle, insufficient evidence, partial coverage, attention and unknown
outcomes. Invalid or oversized output is unknown. Local incidents do not treat
idle or insufficient comparison evidence as a failed collector.

The collector retains 192 cycle observations. The open-work report requires at
least 24 hours of timely completions before reporting sustained observation;
gaps, errors and partial coverage remain explicit. No outgoing alert recipient
has been configured. See [open-work closure](OPEN_WORK_CLOSURE.md).


## Platform integrity and capability readiness, 0.26.0

Health now includes durable state-integrity verification, enabled outcome-connector failures, configured alert-delivery failures and the shared capability projection. Optional unconfigured integrations remain pending rather than making the entire runtime unhealthy. If an optional capability is explicitly activated and its evidence path becomes blocked, that activated capability can raise attention.

`doctor --deep` surfaces the actual health attention codes in its text summary so an aggregate `attention` result is actionable without requiring a second JSON-only command.
## Root-cause attention versus retained queue history

Health treats an uncertain external effect as one root safety condition even when the same effect is visible in receipt, schedule and queue projections. The durable receipt ambiguity remains `attention`; a matching ambiguous schedule and queue projection remain visible as warnings so they do not masquerade as separate faults.

`expired_unpublished` is a closed queue-history state: publication authority expired without a matching receipt. It remains visible during queue retention but is a warning, not a live platform attention condition. No expiry is extended and no replacement publication is authorised.

`doctor --deep` prints bounded safe identity examples for attention roots using fields such as campaign, provider, account and schedule ID. It never prints post copy or credentials.

## LinkedIn read-permission gates

LinkedIn publication and inbound-read authority are reported separately from write authority. If local token metadata explicitly lacks the required restricted read scope, health reports a warning instead of repeatedly treating the account as a generic provider outage.

For current LinkedIn surfaces:

- member post readback requires `r_member_social`;
- member comment collection requires `r_member_social_feed`;
- organisation/Page post readback requires `r_organization_social`;
- organisation/Page comment collection requires `r_organization_social_feed` plus the appropriate Page role.

The scope check is local metadata inspection only. It does not refresh a token, contact LinkedIn, grant authority or prove that a recorded scope is accepted live. Unknown scope metadata keeps the existing provider-observation path.

## Threads no-ID ambiguity forensics

A Threads `ambiguous_effect` without a provider post ID is never retried merely to obtain an ID. The reconciliation worker may perform a bounded GET-only search of the authenticated account's own Threads posts around the recorded ambiguity time. Resolution requires all of the following:

- the exact stored account;
- the exact frozen payload hash and exact text;
- a narrow time window around the recorded ambiguous schedule event;
- exactly one candidate from the account's own post list;
- a second known-ID GET whose ID, author and text all match.

Zero candidates, multiple candidates, truncated listing windows or exact-ID readback mismatches remain unresolved and become manual-review-only after that bounded forensic attempt. Their separate evidence records `automatic_retry=false`, so unattended collection reports the retained review boundary rather than repeating the same historical provider search. A fully targeted campaign/provider/schedule command can deliberately re-check without gaining resend authority. Provider availability/permission failures keep their bounded retry/backoff semantics. The original schedule and receipt remain immutable; successful forensic evidence is stored separately in `publication-readbacks.json`. The forensic path uses existing token material and does not refresh, publish, reply, delete, extend expiry or alter capacity.

When separate evidence is verified, health downgrades the historical ambiguity to a warning rather than erasing it. When terminal forensic evidence remains unresolved, health exposes the last forensic status plus the no-auto-retry/manual-review disposition.
