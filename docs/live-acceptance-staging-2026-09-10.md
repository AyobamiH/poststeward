# Five-workstream staging receipt — 10 September 2026

[PR #22](https://github.com/AyobamiH/poststeward/pull/22) is merged and deployed to https://poststeward-staging.woeinvests.workers.dev. The implementation is verified; the five live acceptance gates remain open.

## Engineering evidence

| Evidence | Result |
| --- | --- |
| Final reviewed head | `41cb13ef6b70041d7425fe1062d0625ad90665ab` |
| Merge / deployed runtime | `93215b0467986e4f17f2c6777c1bb69551d66bd9` |
| Final PR CI | [34537062391](https://github.com/AyobamiH/poststeward/actions/runs/34537062391), job 103071037365: passed |
| Final push CI | [34537057486](https://github.com/AyobamiH/poststeward/actions/runs/34537057486): passed |
| Tests | 185/185, no failures, skips or cancellations |
| TypeScript, generated docs, Worker build | Passed |
| Production and full dependency audits | Both zero known vulnerabilities |
| Deployment | [34537259784](https://github.com/AyobamiH/poststeward/actions/runs/34537259784), verify job 103071664474 and deploy job 103072032384: passed |
| Cloudflare version | `2b789a06-8c56-47c0-a9be-dfb35dc3b419` |
| Database | Existing migrations current; no new migration required |
| Hosted observation time | 2026-09-10 22:26:23–22:26:29 UTC |

The first PR CI passed. Its duplicate push run exposed an existing edge-rate test crossing a fixed minute boundary. The final test covers a possible rollover without changing the limiter; both final CI runs passed.

| Hosted report | Passed assertions |
| --- | ---: |
| HTTP/access boundary | 26 |
| Owner acceptance surfaces | 12 |
| Unauthenticated browser viewports | 2 |
| Recovery/provider OAuth | 13 |
| Account lifecycle | 5 |
| Total | 58 |

The reports verified the exact revision, restricted signup, disabled Advanced/MPP and unauthenticated denial of the new private-source probe. Deployment performed no consent, publication, installation, restore, settlement or deletion.

Configured-capability observations:

```json
{
  "stripeSandbox": false,
  "githubPrivateSources": false,
  "providerOAuth": { "x": false, "threads": false, "linkedin": false }
}
```

## Browser and connector observations

The cloud browser rendered the deployed /app controls. Once requests settled, it showed the sign-in link and rejected unauthenticated access. The private-source and native execution buttons remained disabled.

The browser preflight reported that it exposes WebMCP. This establishes exposure of the registration API only. No owner-authenticated native registration, execution or separate browser-agent invocation took place.

Earlier, /pilot loaded successfully, but automatic approval review initially denied Google navigation for missing account/session-specific authority. The owner subsequently granted that exact authority. Google then opened successfully. After the secure sign-in handoff, the cloud-browser URL policy blocked PostSteward /auth/callback; the owner's screenshot showed ERR_BLOCKED_BY_CLIENT. A separate read-only visit to /app still reported unauthenticated access. The initial approval blocker is resolved; the callback policy blocker remains. No callback URL or OAuth credential is included in this receipt.

Stripe is connected and the owner explicitly selected the account in test mode. Read-only inventory showed no products or webhook endpoints. A dedicated PostSteward staging product and USD 5/month Price were created, then independently retrieved: both active and livemode=false; the Price uses USD 500 cents, one-month recurrence and inclusive tax behaviour. No customer, Checkout Session, subscription, payment or webhook endpoint has been created. Account-specific identifiers are kept out of this public receipt. Restricted API-key creation and protected GitHub environment-secret administration are not exposed by the working integrations.

## Remaining live gates

| Workstream | Current state | Exact next action |
| --- | --- | --- |
| Owner + provider publication | No owner session; all provider applications unconfigured | Resolve the cloud-browser callback policy using the recorded owner approval; configure one provider, obtain grant, prepare exact destination/text review, approve one publication and verify separate readback |
| Private GitHub | Free owner probe deployed; no real private grant/read | Configure the dedicated staging App triple, complete owner selection/OAuth, probe one private path, revoke and recheck |
| PITR | Non-destructive boundary passed; no restore | Owner chooses workspace/target, reviews the prepared plan/digest, then authorises execute, reconciliation and resume |
| Native WebMCP | Registration API exposure observed; no live invocation | Sign in, run the native check, then independently invoke workspace_status through the browser-agent tool interface and verify logout denial |
| Stripe sandbox | Test account approved; dedicated Product/Price created and read back; hosted sandbox still disabled | Reuse the created test Price, establish secure credential storage, provision the signed webhook, configure protected staging values, deploy opt-in, then verify settlement/replay/refund/dispute and cleanup |

Eligible-wallet MPP remains separately disabled. Fixtures are not live evidence. See [the complete execution plan](live-acceptance-plan.md) for dependency order, configuration, failure handling and recovery rules. This receipt does not establish production launch or real-money settlement.

## Follow-up billing corrections

The Stripe integration review identified that a dispute webhook can contain a charge ID without customer/workspace metadata. The follow-up change resolves that charge after signature/mode verification and maps its customer before reconciliation. Lookup failures remain retryable; quarantine and event deduplication remain enforced. It also persists Checkout integration identifiers with new quotes while preserving pre-upgrade retry payloads. These are engineering changes awaiting the follow-up CI/deployment receipt, not live settlement evidence. See [the exact sandbox setup and acceptance sequence](stripe-sandbox-acceptance.md).
