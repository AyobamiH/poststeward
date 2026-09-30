# Advanced availability and GBP launch decision — 30 September 2026

The owner explicitly requested Advanced availability and agent purchasing. The subsequent price decision was **“Use GBP, decide price later”**. No £10.99 price, currency conversion, free paid entitlement or production charge is authorised by this decision.

## Milestones and exact boundaries

1. Enable the existing Advanced execution capability on production for workspaces with independently verified paid entitlement. Preserve explicit profile enablement, original grant validation, approved deterministic templates, inventory bounds, spacing, shared daily delivery limits and pause controls.
2. Replace hard-coded USD selling prices with a configurable GBP price. An absent amount is `null`, never zero or an invented default. Existing staging USD 5 sandbox acceptance remains historical evidence and is not relabelled GBP acceptance.
3. Freeze currency, total and Stripe Price ID on each new server quote. Subscription Checkout and the installed MPP SDK use those values; configuration changes cannot change an already issued quote. New GBP Stripe Prices must be monthly, live in production, exact amount and tax inclusive. Production test keys are refused. A redirect never grants entitlement; paid server evidence remains required.
4. Wire protected production `ADVANCED_PRICE_AMOUNT_PENCE`, `STRIPE_PRICE_ID`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`. Configure both amount and Price together. Read-only Stripe Price verification must pass before deployment. No values belong in source or chat.
5. Agent purchasing uses the existing scoped `billing_status` → `billing_quote` → `billing_checkout` route. An agent with owner-delegated billing authority can obtain a hosted Checkout URL; payer consent and any bank authentication still apply. The non-renewing calendar-month MPP pass implementation also uses exact GBP quotes, but merchant/wallet acceptance and protected settlement configuration remain external; MPP stays disabled.
6. Deploy and independently inspect the exact revision and customer-facing presentation. Full source → inventory → allocation → provider → readback → scheduled metrics acceptance, the documented 24-hour canary/SLO sample and new distributed runtime lifecycle acceptance remain separate live exercises. This release does not claim them completed.

## Product value

Free supports approved publication, explicit schedules, connections, agent grants and delivery receipts. Advanced adds monitored reviewed sources, replenishment from approved deterministic templates, rolling allocation with spacing controls, inspectable inventory and scheduled metrics. It is not generative content creation or an autonomous marketing strategist. Payment does not expand account or agent authority. Automation can pause when entitlement, grant, provider or executor authority expires.

## Missing launch input

The exact GBP monthly amount is intentionally undecided. New checkout remains unavailable until amount, matching Stripe Price and live merchant credentials are configured in the protected production environment. This does not block shipping the UI, exact-quote software or enabling the existing entitled execution capability. Availability is a reviewed product decision (`production_ready`), not a claim of full live Advanced acceptance (`live_verified`).

## Operational controls and research

Preserve the existing bounded staging canary and SLO tooling, publishing pause and owner profile pause. Production rollout is source-reviewed on protected main with the existing required CI and release smokes. Kubernetes lease/generation discipline and AWS idempotent API guidance informed existing fencing; Stripe idempotency and immutable price identifiers inform retaining the exact quote across interrupted retries. Official source notes captured on this date are stored with the workspace research evidence. No new provider publication or purchase is requested by deployment or visual verification.
