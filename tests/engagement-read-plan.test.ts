import { test } from "node:test";
import assert from "node:assert/strict";
import { planEngagementReads, type EngagementScope } from "../src/engagement-read-plan.ts";
const scope = (account: string, overrides: Partial<EngagementScope> = {}): EngagementScope => ({
  workspace: "customer-a", account, provider: "threads", active: true,
  readbackGranted: true, rootIds: ["receipt-1"], ...overrides,
});
test("dormant, permission-held and backed-off accounts cannot consume active scan time", () => {
  const result = planEngagementReads("customer-a", [
    scope("active"), scope("dormant", { active: false }),
    scope("denied", { readbackGranted: false }),
    scope("backoff", { retryAt: 200 }), scope("empty", { rootIds: [] }),
  ], 100, 60000);
  assert.deepEqual(result.ready.map((x) => [x.account, x.budgetMs]), [["active", 60000]]);
  assert.deepEqual(result.skipped.map((x) => x.reason), ["inactive", "permission_required", "provider_backoff", "no_receipt_roots"]);
});
test("fair time slices and bounded root counts across active accounts", () => {
  const result = planEngagementReads("customer-a", [
    scope("large", { rootIds: Array.from({ length: 200 }, (_, i) => `root-${i}`) }),
    scope("small"), scope("x", { provider: "x", rootIds: ["one", "two"] }),
  ], 100, 30000);
  assert.equal(result.ready[0].roots.length, 160);
  assert.equal(result.ready[2].roots.length, 1);
  assert.equal(result.ready.reduce((sum, x) => sum + x.budgetMs, 0), 30000);
  assert.ok(result.ready.every((x) => x.budgetMs >= 4000));
});
test("refuses cross-workspace or duplicate account scope", () => {
  assert.throws(() => planEngagementReads("customer-a", [scope("a"), scope("b", { workspace: "customer-b" })], 100, 1000), /Cross-workspace/);
  assert.throws(() => planEngagementReads("customer-a", [scope("a"), scope("a")], 100, 1000), /duplicate/);
});
test("zero budget and expired backoff remain deterministic", () => {
  const result = planEngagementReads("customer-a", [scope("a", { retryAt: 100 })], 100, 0);
  assert.equal(result.ready[0].budgetMs, 0);
});
