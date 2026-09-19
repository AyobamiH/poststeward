# Operational alert delivery live evidence — 19 September 2026

Production operator-delivery acceptance is bound to workflow run `35440129849` on release `1f0831a912177e37e6997a2539f1b7a30fe75563`.

The control plane ran separately in staging and production. The production fire drill used the GitHub issue path as the out-of-band operator channel and exercised every reviewed alert class:

- `worker_5xx`
- `rate_limit_pressure`
- `owner_auth_failure`
- `provider_or_oauth_failure`
- `durable_object_or_d1_failure`
- `recovery_state`
- `stripe_reconciliation`
- `capacity_threshold`

Production result:

- every reviewed class delivered;
- configured delivery paths: `github_issue`;
- no missing alert classes;
- no configured-but-untested paths;
- the synthetic primary path was deliberately made unreachable;
- escalation to the distinct GitHub issue fallback was observed;
- the issue was created, read back, assigned to the operator and closed;
- evidence evaluator returned `ready=true`.

The drill created issue #135 and closed it after readback. It contained only bounded operational metadata and did not manufacture a customer incident or provider effect.

This evidence establishes that an operator can receive and acknowledge reviewed PostSteward operational alerts through a plane independent of the application-semantic D1 outbox.
