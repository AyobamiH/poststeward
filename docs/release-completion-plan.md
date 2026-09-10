# PostSteward release-completion plan

This plan continues from the merged recovery/effect-ledger milestone. It does not recreate working Cloudflare resources or claim external provider approvals, owner consent, payment settlement or a live social post that have not happened.

## Completion layers

1. **Repository and deployment provenance.** Automatic staging deployment must verify that its trigger SHA is the merge commit of a pull request into `main`; ordinary direct pushes are not accepted as deployment authority. The GitHub repository itself must still be protected with an account-level branch rule because repository code cannot stop an administrator from replacing its own workflow.
2. **One owner workspace experience.** The normal `/app` workspace must use the self-service provider OAuth system already implemented by the service, show provider/capability state, keep manual token import as an explicit fallback, and surface recovery state/actions instead of requiring raw API calls.
3. **Recovery acceptance without destructive automation.** Hosted deployment checks must verify owner-only recovery routes reject unauthenticated/cross-origin/agent access and that the public service reports the exact deployed revision. A real point-in-time restore remains an explicit owner action because it changes customer state.
4. **Provider capability gate.** X, Threads and LinkedIn OAuth remain disabled provider-by-provider until that provider's real app client ID and secret are present. LinkedIn controlled acceptance additionally requires the explicit member-readback capability. No other product's credentials are imported implicitly.
5. **Controlled live acceptance.** A successful live milestone requires a real owner OIDC completion proof, a real provider identity, an explicit account/content review, exactly one provider write and a separate matching readback. CI fixtures and deployment smoke never count as that evidence.
6. **Advanced/payment gate.** Advanced and machine payments remain disabled until real Stripe sandbox lifecycle evidence exists for checkout, signed webhooks, reconciliation, refund/dispute revocation and the eligible machine-payment path. Payment configuration alone is not release approval.
7. **Agent/browser gate.** Browser WebMCP continues to expose only catalogued operations. Public release requires a supported browser to report native registration and execute a read-only tool against the hosted authenticated workspace; simulated registration does not count.
8. **Operational gate.** Public release also requires account erasure, encryption-key rotation rehearsal, alert delivery, capacity/retention calibration and a reviewed custom-domain/WAF policy. Until then signup remains restricted and production remains unconfigured.

## Current external gates

The staging deployment currently has Google owner OIDC configured, but X, Threads and LinkedIn provider application IDs are absent. Those external applications and their user consent cannot be manufactured by repository code. The product must remain useful and fail closed while they are absent.

## Evidence discipline

Every release status must separate `implemented`, `deployed`, `hosted-verified`, and `live-external-verified`. A green build is never promoted to a claim of owner consent, provider publication, restore success, browser WebMCP support or payment settlement without the corresponding external receipt.
