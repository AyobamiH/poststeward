---
schema: clawsweeper.project-vision.v1
project_id: poststeward
repository: AyobamiH/poststeward
---

# Project Vision

## Identity

PostSteward is a Cloudflare-hosted social-publishing product with an installable client for humans and AI agents.

## Purpose

Turn owner-approved content into provider publication, explicit schedules, and inspectable receipts while giving agents useful automation without delegating owner-only identity, consent, recovery, or administrative authority.

## Owns

- Workspace identity, sessions, scoped/revocable agent grants, and operation catalogue.
- Provider connections and publication/scheduling state for supported social platforms.
- Immutable review/approval, cancellation boundaries, idempotency, effect reconciliation, and receipts.
- Hosted HTTP, remote MCP, browser-agent operation surfaces, and the public installable `poststeward` client.
- Advanced campaign monitoring/allocation features when separately enabled and proven.

## Does Not Own

- The owner's historical `AyobamiH/post-once` production installation. PostSteward may reuse proven Post-Once setup/receipt design patterns, but it must not create a second independent provider-effect ledger for the same hosted workspace.
- Social-provider identity merely because credentials are configured.
- Provider consent or account authority without a completed owner connection.
- Public signup, Advanced automation, billing entitlement, or recovery capability merely because supporting infrastructure exists.

## Non-Negotiable Invariants

- Owner-only consent, provider OAuth, recovery, lifecycle, and administrative controls do not silently become delegable agent tools.
- Agent tokens are scoped, expiring, revocable, and cannot grant admin authority.
- Exact content/account/release identity is rechecked before consequential publication.
- Fingerprint dedupe, idempotency, stale-claim recovery, and ambiguous-effect preservation prevent blind repeat writes.
- Accepted live external effects are not repeated merely to refresh evidence.
- Availability, configuration, connection, entitlement, publication, readback, and verification remain separate states.

## Evidence of Done

A publication is complete only when the authorised operation reaches an honest durable terminal state and available provider evidence supports it. A deployment, credential, OAuth application, or payment event alone is not proof of a user outcome.

## Relationships

- post-once: separate owner/production local portfolio publisher and design lineage. PostSteward's public client is its own product surface; provider consequences remain authoritative in PostSteward's hosted workspace ledger.
- Social providers: external authorities for account identity and provider-side effects.
- GitHub/Stripe/Cloudflare: supporting integrations whose configuration does not by itself prove product acceptance.

## Canonical Sources

README.md, public/docs/install.md, docs/current-readiness.md, docs/deployment.md, docs/private-github-sources.md, docs/security.md, docs/operations-runbook.md, and generated operation documentation.

## Agent Rule

Preserve owner authority and evidence boundaries. Do not repeat live external effects simply to obtain a newer receipt.
