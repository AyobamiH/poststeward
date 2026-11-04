/** Read-only engagement scan planning. No provider I/O or publishing authority. */
export interface EngagementScope {
  workspace: string;
  account: string;
  provider: "threads" | "x" | "linkedin";
  active: boolean;
  readbackGranted: boolean;
  rootIds: readonly string[];
  retryAt?: number;
}
export interface EngagementReadPlan {
  workspace: string;
  account: string;
  provider: EngagementScope["provider"];
  roots: string[];
  budgetMs: number;
}
export interface EngagementPlanResult {
  ready: EngagementReadPlan[];
  skipped: { workspace: string; account: string; reason: string }[];
}
const MAX_ROOTS = { threads: 160, x: 1, linkedin: 50 } as const;
const MIN_ACTIVE_MS = 4000;
/**
 * Allocate only among scopes with authority and receipt-backed roots. Never
 * let dormant, permission-held or backed-off accounts steal active scan time.
 * The budget is a local deadline, not a provider quota or permission grant.
 */
export function planEngagementReads(
  workspace: string,
  scopes: readonly EngagementScope[],
  now: number,
  totalBudgetMs: number,
): EngagementPlanResult {
  if (!workspace || !Number.isFinite(now) || !Number.isFinite(totalBudgetMs) || totalBudgetMs < 0)
    throw new Error("Invalid engagement scan parameters");
  const ready: EngagementReadPlan[] = [];
  const skipped: EngagementPlanResult["skipped"] = [];
  const seen = new Set<string>();
  for (const scope of scopes) {
    if (scope.workspace !== workspace) throw new Error("Cross-workspace engagement scope");
    if (!scope.account || seen.has(`${scope.provider}:${scope.account}`))
      throw new Error("Missing or duplicate engagement account binding");
    seen.add(`${scope.provider}:${scope.account}`);
    const reason = !scope.active ? "inactive" : !scope.readbackGranted ? "permission_required"
      : scope.retryAt !== undefined && scope.retryAt > now ? "provider_backoff"
      : !scope.rootIds.length ? "no_receipt_roots" : undefined;
    if (reason) {
      skipped.push({ workspace, account: scope.account, reason });
      continue;
    }
    const roots = [...new Set(scope.rootIds)].slice(0, MAX_ROOTS[scope.provider]);
    ready.push({ workspace, account: scope.account, provider: scope.provider, roots, budgetMs: 0 });
  }
  let remaining = totalBudgetMs;
  for (let i = 0; i < ready.length; i++) {
    const tail = ready.slice(i);
    const floor = Math.min(MIN_ACTIVE_MS, remaining / tail.length);
    const weights = tail.map((scope) => scope.provider === "x" ? 1 : scope.roots.length);
    const weightSum = weights.reduce((sum, weight) => sum + weight, 0);
    const budget = i === ready.length - 1 ? remaining
      : floor + Math.max(0, remaining - floor * tail.length) * weights[0] / Math.max(1, weightSum);
    ready[i].budgetMs = budget;
    remaining = Math.max(0, remaining - budget);
  }
  return { ready, skipped };
}
