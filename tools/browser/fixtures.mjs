/** Synthetic records only. Never credentials or customer workspace evidence. */
const now = Date.now();
const accounts = ["x", "threads", "linkedin"].map((provider, i) => ({
  alias: `fixture_${provider}`,
  provider,
  active: true,
  identity: { username: `fixture_${provider}`, id: `stable-${i}` },
  verifiedAt: now - 60_000,
  capabilities: { oauth: true, readback: provider !== "linkedin", refresh: provider !== "linkedin" },
}));
const states = [
  "scheduled",
  "executing",
  "waiting_container",
  "published_verified",
  "published_unverified",
  "ambiguous_effect",
  "failed",
  "drift_blocked",
  "cancelled",
  "future_unknown",
];
const receipts = states.map((status, i) => ({
  id: `receipt-${i}`,
  provider: accounts[i % 3].provider,
  account: accounts[i % 3].alias,
  status,
  text:
    i === 5
      ? '<img src=x onerror="window.unsafe=true"> reviewed text ' + "long".repeat(70)
      : `Synthetic reviewed copy ${i}`,
  dueAt: now + i * 1000,
  updatedAt: now,
  createdAt: now - 60_000,
  timezone: "UTC",
  reason: i === 5 ? "Provider outcome uncertain. Do not republish." : undefined,
  ...(status.startsWith("published") ? { postId: `post-${i}` } : {}),
}));
const profiles = [
  {
    id: "fixture-profile",
    family: "development",
    enabled: false,
    repository: "fixture/repository",
    branch: "main",
    path: "README.md",
    intervalMinutes: 15,
    minSpacingMinutes: 60,
    nextRun: now + 3_600_000,
    lastCheck: now - 60_000,
  },
];

export const fixture = {
  "/api/session": {
    workspace: "SYNTHETIC-UX-FIXTURE",
    actor: "synthetic-owner",
    csrf: "synthetic",
    scopes: ["admin"],
  },
  "/api/grants": [
    { actor: "Expired fixture", scopes: '["read"]', expires_at: now - 1000 },
    { actor: "Active fixture", scopes: '["read"]', expires_at: now + 3_600_000 },
    {
      actor: "Revoked fixture",
      scopes: '["publish"]',
      expires_at: now + 3_600_000,
      revoked_at: now - 1000,
    },
  ],
  "/api/connections/oauth/status": {
    providers: Object.fromEntries(
      ["x", "threads", "linkedin"].map((provider) => [
        provider,
        {
          available: provider === "threads",
          capabilities: {
            publish: { state: "connection_required" },
            readback: {
              state:
                provider === "linkedin"
                  ? "external_approval_required"
                  : "connection_required",
            },
          },
        },
      ]),
    ),
    connections: [],
  },
  "/api/recovery/status": {
    control: { quarantined: true },
    effects: { uncertain: 1, containerUncertain: 0 },
    plan: {
      id: "fixture-plan",
      state: "prepared",
      targetTime: now - 50_000,
      reason: "Synthetic plan, not an executed restore",
    },
  },
  "/api/recovery/checkpoints": {
    checkpoints: [
      {
        id: "fixture-checkpoint",
        capturedAt: now - 60_000,
        source: "automatic",
        release: "a".repeat(40),
        rootWrite: "next",
        stateDigest: "b".repeat(64),
      },
    ],
  },
  "/api/lifecycle/status": { deletion: null },
  "/api/pilot/status": {
    owner: {
      id: "fixture-proof",
      workspace: "SYNTHETIC-UX-FIXTURE",
      authenticatedAt: now,
      expiresAt: now + 3_600_000,
    },
    record: null,
    completed: false,
  },
  "/api/sources/github/status": {
    configuration: { available: false },
    installations: [],
    repositories: [],
  },
  "/api/operations/workspace_status": {
    plan: "free",
    publishingPaused: false,
  },
  "/api/operations/accounts_list": accounts,
  "/api/operations/projects_list": Array.from({ length: 10 }, (_, i) => ({
    id: `project-${i}`,
    name: `Synthetic project ${i}`,
    accounts: accounts.map((account) => account.alias),
  })),
  "/api/operations/receipts_list": receipts,
  "/api/operations/automation_inspect": {
    profiles,
    deliveries: receipts.slice(0, 3),
  },
  "/api/operations/billing_status": {
    sandbox: true,
    methods: { checkout: { available: false } },
    portalAvailable: false,
  },
};
