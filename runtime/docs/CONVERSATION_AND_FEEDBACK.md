# Recurring conversations and performance feedback (0.20.0)

## What runs

The existing collector continues every 15 minutes. It collects incoming X and
Threads interactions, captures due metrics and builds performance feedback. A
separate `ocpf-post-replies.timer` runs the response worker every 15 minutes with
a six-minute service bound. Campaign publishing/refill and its account budgets
remain independent. The new response worker is disabled until configured.

## Replies to replies

Collection now includes our verified outgoing replies as targets, not only the
initial campaign posts. Each collected follow-up retains its immediate parent,
original campaign, account and conversation depth. `engagement conversation --id
INBOX_ID` shows the known earlier incoming comments and frozen outgoing responses.
Sending targets the actual new comment and rechecks its complete live context.
A new comment is a new interaction; it does not reuse the earlier response receipt.

Only local campaign publication receipts and verified response receipts establish
this chain. Other accounts, merely drafted replies, unverified or ambiguous
responses cannot extend it. Roots are bounded to 30 days; a publication or verified
response within seven days keeps its conversation active. Depth is limited to 20
response turns. Threads retains per-target pagination and rotating scans. X
retains mentions pagination; neither adapter claims complete platform coverage.
A 15-minute worker does not imply every Threads root is scanned every 15 minutes.

## Recurring response decisions

For configured accounts, the worker considers the oldest pending interactions:

1. Process known author opt-outs before sending anything.
2. Gather the known conversation chain and produce a useful draft or a disposition.
3. Make a separate model request to review that exact draft against the conversation.
4. Atomically bind the approved draft to the unchanged incoming context.
5. Apply account mode, daily budget, spacing and current opt-out checks.
6. Use the existing live identity, recent parent, context and durable send/readback path.

A closing acknowledgement can receive `no_response_needed`; an unsupported claim,
sensitive issue or uncertain response receives `review_required`. These decisions
are visible in `engagement worker-status`, rather than silently treated as replies.
A changed comment can be reconsidered. Another workflow's draft is preserved.
Published, ambiguous, rejected and interrupted sends never gain automatic retries.
If live preflight fails before the durable send attempt, the worker retains the
exact reviewed draft and may retry preflight after its backoff. It does not spend
another model review or overwrite that draft. The existing expiry and identity
checks still apply; any recorded possible effect remains terminal.
Models have no tools, shell, browser, credential or repository access. Incoming
text is untrusted data. Public conversation context is sent to the OpenAI API;
requests use `store=false`, without claiming zero provider retention. The model
is pinned to `gpt-4.1-mini-2025-04-14`. Independent requests do not guarantee perfect
judgement; this is bounded automation with observable review states.

Defaults: at most four considered items per cycle, 40 model requests per UTC day,
10 reply attempts per account/day and three minutes between attempts. Attempts,
including uncertain effects, count. Each request has a 25-second timeout, bounded
input/output and no redirects. Model-call budget is saved before each request.
These are reply budgets, separate from campaign publication targets.

## Activation on the existing host

```bash
cd ~/post-once &&
git pull --ff-only &&
python3 scripts/enable-conversation-loop.py --apply &&
python3 scripts/restore-local-runtime.py &&
./ocpf-post engagement worker-status
```

The setup prompts privately for an OpenAI API key if no dedicated credential is
present. A ChatGPT subscription or Google OAuth JSON is not that API credential.
Alternatively configure `OCPF_POST_OPENAI_API_KEY` for the service. A missing key
is reported and prevents composition/sending; it is not a successful connection.
The saved key is a private local file, separate from provider tokens and Git.
The setup does not send a social reply or prove that the API key works.

Threads is configured for automatic responses. X is configured for drafting and
explicit review: X's April 2026 rules require prior written approval from X for
AI-powered reply bots. An account policy can use `mode: automatic` for X only
with its `x_approval_reference` recording that separately obtained approval.
The software validates the presence of the record, not the authenticity of X's
external approval. The setup never invents that approval. LinkedIn member/Page comment collection and explicit reviewed sending are implemented. The recurring automatic model worker remains intentionally limited to its configured X/Threads policies; LinkedIn automatic sending is not enabled.

Inspect or run one cycle:

```bash
./ocpf-post engagement worker-status
./ocpf-post engagement conversation --id INBOX_ID
./ocpf-post engagement process --apply
```

A review-mode draft can use the existing `engagement send --id INBOX_ID
--expected-sha256 HASH --live` after review. It is never silently promoted by the
worker. Do not rerun an uncertain send.

Disable automatic processing without altering historical receipts:

```bash
python3 scripts/enable-conversation-loop.py --disable --apply
```

## New-source comparison supply and capture retries

New revisions start with the existing single insight. A deterministic two-thirds
topic sample designates one question or practical challenger. That challenger
requires the exact account/source-revision insight's verified receipt, matching
frozen attribution and at least 48 hours of spacing. At most one challenger per
project/provider is admitted in a rolling 24 hours. Existing admission limits,
source expiry and review authority still apply; old campaigns are never rewritten.
One third of topics remain insight-only. There are never three new active variants
per topic. Disabling feedback also disables new challenger admission.

Comparison arms are separate so disjoint question/practical topics cannot be
treated as head-to-head evidence. Each arm still needs five exact topic/revision
pairs with the original exposure and age gates. Legacy attribution stays usable
within its existing cohort. New sampling starts on fresh source revisions;
pre-existing insight manifests are not silently assigned experiment metadata.

Metrics capture records an attempt before the provider read. Within the original
22–26 hour window it allows at most five attempts, delayed 15, 30, 60 and 60 minutes,
respecting exposed Retry-After values and a one-hour 429 delay. Completed results
share their attempt identity; a crash cannot refill the budget. A successful
observation deduplicates. Missed windows and exhausted budgets remain visible.

## Performance-based selection

Metrics capture now uses the exact receipt's provider/account/post identity,
even if a campaign's current destination changed. Snapshots freeze editorial
attribution only when the current manifest's payload hash matches the published
receipt. Historical snapshots lacking that attribution remain in the record but
do not manufacture evidence for a preference.

The feedback builder compares generated `insight`, `question` and `practical`
variants within the same provider, account, lane and rendering-template version.
Each comparison pairs the same topic from the same project and source revision.
At least five matched topics are required, with verified publication receipts,
observations at 22–26 hours old and known exposure of at least 100 per post.
Topics can come from different projects on that account. This makes the threshold
reachable with the catalogue's two commercial topics per project without pooling
unrelated project engagement rates. Unmatched topics cannot decide the winner.
Duplicate snapshots count once; multiple posts for one exact topic/variant are
excluded from pairing. Unknown values, future observations, old samples,
unknown template versions and mismatched payloads are excluded visibly.

Each post's rate is (likes + reposts + quotes) / impressions or views. Replies
are deliberately excluded from the score so our own automatic responses cannot
inflate the feedback signal. These are descriptive associations, not causal lift,
leads, revenue, universal best posting times or proof of better copy.

A variant must beat every other eligible variant on the median paired-topic
relative difference by over 20% of the larger rate, equivalent to more than 25%
uplift against the smaller positive rate. It receives three priority points above
the shared template base. Legacy Q/P discounts of four/eight points are restored
only for that evidenced winner, so the total adjustment is at most eleven points.
Without this correction a three-point boost could never beat the legacy ordering.
Existing project fairness, waiting age,
deadline handling, urgent work, lane quotas and topic controls remain ahead of
this preference. Alternate served turns for each project use the original score
for exploration. Snapshot engine v7 freezes each candidate's boost and expiry for replay.
Signals expire after 24 hours, including during future slot selection, and are
rebuilt from recent evidence. Evidence pairs never mix source revisions. A
preference concerns only the versioned rendering structure, so it can inform
other current approved topics using that template; product claims still pass
the existing source, payload and freshness checks. No scheduled or
published copy, approval, expiry, receipt, quota or destination is rewritten.

This is a real, bounded selection change when evidence qualifies. It is not model
training, automatic rewriting of approved vault copy or a promised engagement gain.
Sparse evidence means no selection change. The worker does not pretend to learn
from unavailable LinkedIn personal analytics.

```bash
./ocpf-post performance feedback
./ocpf-post performance feedback --apply
./ocpf-post performance feedback --disable --apply
./ocpf-post performance feedback --enable --apply
```

Operations reports retain the worker cycle and performance feedback. Local
incidents retain review-required and uncertain reply outcomes. No outgoing alert
recipient is configured by this release.

## Established acceptance and remaining evidence

The model connection and first automatic nested Threads response were verified on
12 September. Those milestones remain closed. Remaining work includes sustained
cycles, the two queued outcomes and existing unverified X readback.
- X's separate written approval before automatic AI replies on X.
- Enough comparable performance snapshots, a recorded allocation preference and
  later outcomes before claiming an improvement in engagement.

Implementation tests use isolated local state and fake providers/models. They do
not establish later host outcomes or measured reply usefulness. The operating-loop register retains all 21 IDs.

References: [X automation rules](https://help.x.com/en/rules-and-policies/x-automation),
[OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[model documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

## Coverage, usefulness and outcome recovery

Threads targets half of known targets per eligible cycle, capped at 50 requests
and an account share of the sync time budget. For 82 fast targets this plans 41
pages per cycle; a roughly 30-minute sweep is a target, not a guarantee. Reports
show completed/page budgets, oldest scan age and targets older than 30 minutes.
Slow providers and pagination retain cursors and make partial coverage visible.

Drafting and independent review must identify a concrete new contribution:
an example, actionable check, relevant trade-off or necessary clarification.
Agreement, praise and paraphrase should produce skip. The same pinned model,
credential, account policy and send protections remain. This quality rubric is
an implementation improvement; usefulness still needs real observed evaluation.

`./ocpf-post engagement reconcile` previews existing uncertain reply IDs.
Adding `--apply` reads those IDs and appends local verification evidence. Author,
parent and exact frozen text must match. Missing IDs stay unresolved; no resend
or model call occurs. Historical draft and attempt evidence is retained.
