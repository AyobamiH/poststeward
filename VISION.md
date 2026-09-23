---
schema: clawsweeper.project-vision.v1
project_id: poststeward
repository: AyobamiH/poststeward
---

# Project Vision

## Identity

PostSteward is a hosted Cloudflare-based social-publishing product for AI agents.

## Purpose

Turn owner-approved content into provider publication, explicit schedules, and inspectable receipts while giving agents useful automation without delegating owner-only identity, consent, recovery, or administrative authority.

## Owns

- Workspace identity, sessions, scoped/revocable agent grants, and operation catalogue.
- Provider connections and publication/scheduling state for supported social platforms.
- Immutable review/approval, cancellation boundaries, idempotency, effect reconciliation, and receipts.
- Hosted HTTP, remote MCP, and supported browser-agent operation surfaces.
- Advanced campaign monitoring/allocation features when separately enabled and proven.

## Does Not Own

- post-once, the owner's separate local publishing utility.
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

- post-once: separate local portfolio publisher.
- Social providers: external authorities for account identity and provider-side effects.
- GitHub/Stripe/Cloudflare: supporting integrations whose configuration does not by itself prove product acceptance.

## Canonical Sources

README.md, docs/current-readiness.md, docs/deployment.md, docs/private-github-sources.md, docs/security.md, docs/operations-runbook.md, and generated operation documentation.

## Agent Rule

Preserve owner authority and evidence boundaries. Do not repeat live external effects simply to obtain a newer receipt.
