const providerLabels = { x: "X", threads: "Threads", linkedin: "LinkedIn" };
const receiptStateLabels = {
  scheduled: "Scheduled",
  executing: "Executing",
  waiting_container: "Waiting for Threads",
  cancelled: "Cancelled",
  published_verified: "Verified",
  published_unverified: "Needs readback",
  ambiguous_effect: "Ambiguous effect",
  failed: "Failed",
  drift_blocked: "Drift blocked",
};
const receiptPillState = {
  scheduled: "pending",
  executing: "pending",
  waiting_container: "pending",
  cancelled: "cancelled",
  published_verified: "verified",
  published_unverified: "warning",
  ambiguous_effect: "ambiguous",
  failed: "failed",
  drift_blocked: "blocked",
};
const emptyCopy = {
  accounts: [
    "No social destinations connected",
    "Connect one verified provider account above. PostSteward will keep its stable provider identity with the routing record.",
  ],
  projects: [
    "No publishing projects yet",
    "Create a project after at least one destination is connected. Projects bind approved work to explicit accounts.",
  ],
  receipts: [
    "No delivery evidence yet",
    "Receipts appear after a publication or schedule is reserved. A reservation will remain distinct from verified provider readback.",
  ],
  grants: [
    "No delegated agents",
    "Create a scoped, expiring grant when an agent needs direct PostSteward access. Owner controls are never delegated here.",
  ],
  profiles: [
    "No automation profiles",
    "Advanced profiles appear here only after reviewed source, template, destination and spacing rules are stored.",
  ],
  "github-sources": [
    "No private repositories linked",
    "Private GitHub access is optional. Link selected repositories only when reviewed source monitoring needs them.",
  ],
  categories: [
    "No reviewed categories yet",
    "Categories appear when Advanced profiles establish reviewed source families.",
  ],
  inventory: [
    "No reviewed inventory yet",
    "Observed source inventory appears after an enabled profile sees a reviewed source change.",
  ],
  deliveries: [
    "No automatic allocations yet",
    "Future automatic deliveries remain empty until a reviewed profile allocates work.",
  ],
  "checkpoint-list": [
    "No exact checkpoints listed",
    "Exact checkpoints are owner-only recovery evidence. Refresh or capture one only when the recovery contract requires it.",
  ],
};

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function providerMark(provider) {
  const node = make("span", "ux-provider-mark", provider === "linkedin" ? "in" : provider === "threads" ? "@" : "X");
  node.dataset.provider = provider;
  node.setAttribute("aria-hidden", "true");
  return node;
}

function pill(text, state = "neutral") {
  const node = make("span", "ux-pill", text);
  node.dataset.state = state;
  return node;
}

function titleGroup(provider, title) {
  const wrap = make("div", "ux-entity-title");
  if (providerLabels[provider]) wrap.append(providerMark(provider));
  wrap.append(make("strong", "", title));
  return wrap;
}

function heading(titleNode, badges = []) {
  const row = make("div", "ux-record-heading");
  const title = make("div", "ux-record-title");
  title.append(titleNode);
  const badgeWrap = make("div", "ux-badges");
  badgeWrap.append(...badges);
  row.append(title, badgeWrap);
  return row;
}

function replaceEmpty(root) {
  if (!root || root.children.length !== 1) return;
  const current = root.firstElementChild;
  if (!current || current.textContent?.trim() !== "Nothing here yet.") return;
  const copy = emptyCopy[root.id];
  if (!copy) return;
  const state = make("div", "ux-empty-state");
  state.append(make("strong", "", copy[0]), make("p", "", copy[1]));
  root.replaceChildren(state);
}

function enhanceAccount(row) {
  const strong = row.querySelector(":scope > strong");
  if (!strong) return;
  const [alias, provider] = strong.textContent.split(" · ").map((part) => part.trim());
  if (!providerLabels[provider]) return;
  const detail = row.querySelector(":scope > span");
  const detailText = detail?.textContent || "";
  const connected = detailText.includes("Connected") && !detailText.includes("Disconnected");
  const badges = [pill(connected ? "Connected" : "Disconnected", connected ? "connected" : "disconnected")];
  if (detailText.includes("OAuth")) badges.push(pill("OAuth"));
  if (detailText.includes("refresh")) badges.push(pill("refresh"));
  if (detailText.includes("readback")) badges.push(pill("readback", "verified"));
  strong.remove();
  row.prepend(heading(titleGroup(provider, alias), badges));
  row.dataset.provider = provider;
}

function enhanceReceipt(row) {
  const strong = row.querySelector(":scope > strong");
  if (!strong) return;
  const [provider, account, state] = strong.textContent.split(" · ").map((part) => part.trim());
  if (!receiptStateLabels[state]) return;
  strong.remove();
  row.prepend(
    heading(titleGroup(provider, account || "Delivery"), [
      pill(receiptStateLabels[state], receiptPillState[state]),
      pill(providerLabels[provider] || provider),
    ]),
  );
  row.dataset.receiptState = state;
  row.dataset.provider = provider;
}

function enhanceProfile(row) {
  const strong = row.querySelector(":scope > strong");
  if (!strong) return;
  const match = /^(.*?) · (Running|Paused)$/.exec(strong.textContent.trim());
  if (!match) return;
  strong.textContent = match[1];
  strong.remove();
  row.prepend(
    heading(make("strong", "", match[1]), [
      pill(match[2], match[2] === "Running" ? "running" : "paused"),
    ]),
  );
}

function enhanceGrant(row) {
  const strong = row.querySelector(":scope > strong");
  const detail = row.querySelector(":scope > span");
  if (!strong || !detail) return;
  const revoked = detail.textContent.trim().startsWith("Revoked ·");
  strong.remove();
  row.prepend(
    heading(make("strong", "", strong.textContent), [
      pill(revoked ? "Revoked" : "Active", revoked ? "revoked" : "connected"),
    ]),
  );
}

function enhanceGeneric(row, id) {
  if (id === "accounts") enhanceAccount(row);
  else if (id === "receipts") enhanceReceipt(row);
  else if (id === "profiles") enhanceProfile(row);
  else if (id === "grants") enhanceGrant(row);
  row.dataset.uxEnhanced = "true";
}

function enhanceRecordLists() {
  for (const root of document.querySelectorAll(".record-list")) {
    replaceEmpty(root);
    for (const row of root.querySelectorAll(":scope > .record:not([data-ux-enhanced])"))
      enhanceGeneric(row, root.id);
  }
  applyReceiptFilter();
}

function ensureReceiptToolbar() {
  const root = document.getElementById("receipts");
  if (!root || document.getElementById("ux-receipt-filter")) return;
  const toolbar = make("div", "ux-evidence-toolbar");
  const statusLabel = make("label", "", "Receipt state");
  const select = document.createElement("select");
  select.id = "ux-receipt-filter";
  for (const [value, label] of [
    ["", "All states"],
    ["published_verified", "Verified"],
    ["published_unverified", "Needs readback"],
    ["ambiguous_effect", "Ambiguous effect"],
    ["scheduled", "Scheduled"],
    ["executing", "Executing"],
    ["waiting_container", "Waiting for Threads"],
    ["failed", "Failed"],
    ["drift_blocked", "Drift blocked"],
    ["cancelled", "Cancelled"],
  ]) select.append(new Option(label, value));
  statusLabel.append(select);
  const searchLabel = make("label", "", "Search evidence");
  const search = document.createElement("input");
  search.id = "ux-receipt-search";
  search.type = "search";
  search.placeholder = "Account, copy, reason…";
  searchLabel.append(search);
  toolbar.append(statusLabel, searchLabel);
  root.before(toolbar);
  select.addEventListener("change", applyReceiptFilter);
  search.addEventListener("input", applyReceiptFilter);
}

function applyReceiptFilter() {
  const root = document.getElementById("receipts");
  if (!root) return;
  const state = document.getElementById("ux-receipt-filter")?.value || "";
  const query = (document.getElementById("ux-receipt-search")?.value || "").trim().toLowerCase();
  for (const row of root.querySelectorAll(":scope > .record")) {
    const stateMatch = !state || row.dataset.receiptState === state;
    const queryMatch = !query || row.textContent.toLowerCase().includes(query);
    row.hidden = !(stateMatch && queryMatch);
  }
}

async function fetchJson(path) {
  const response = await fetch(path, {
    credentials: "same-origin",
    mode: "same-origin",
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(15000),
    headers: { Accept: "application/json" },
  });
  if (!response.ok) return;
  return response.json().catch(() => undefined);
}

function providerState(config, connections) {
  if (!config?.available) return ["Not configured", "paused"];
  if (connections.some((item) => item.status === "healthy")) return ["Connected", "connected"];
  if (connections.some((item) => item.needsReauthorization || item.status === "reauthorization_required"))
    return ["Reconnect", "warning"];
  if (connections.some((item) => ["expired", "identity_drift", "refresh_failed"].includes(item.status)))
    return ["Attention", "warning"];
  return ["OAuth ready", "verified"];
}

function capability(label, value, detail = "") {
  const item = make("li");
  item.append(make("span", "", label), make("code", "", value ? detail || "yes" : "no"));
  return item;
}

async function renderProviderMatrix() {
  const anchor = document.getElementById("oauth-status");
  if (!anchor) return;
  const value = await fetchJson("/api/connections/oauth/status");
  if (!value?.providers) return;
  let grid = document.getElementById("ux-provider-grid");
  if (!grid) {
    grid = make("div", "ux-provider-grid");
    grid.id = "ux-provider-grid";
    anchor.after(grid);
  }
  grid.replaceChildren();
  for (const provider of ["x", "threads", "linkedin"]) {
    const config = value.providers[provider] || {};
    const connections = (value.connections || []).filter((item) => item.provider === provider);
    const [stateText, state] = providerState(config, connections);
    const card = make("section", "ux-provider-card");
    const head = make("div", "ux-provider-card-head");
    const title = make("div", "ux-provider-card-title");
    title.append(providerMark(provider), make("span", "", providerLabels[provider]));
    head.append(title, pill(stateText, state));
    const list = make("ul", "ux-capability-list");
    list.append(
      capability("Provider app", !!config.available, config.available ? "configured" : "missing"),
      capability("Independent readback", !!config.readback, config.readback ? "available" : "not approved"),
      capability("Connected accounts", connections.length > 0, String(connections.length)),
    );
    if (Array.isArray(config.requiredScopes) && config.requiredScopes.length) {
      const scope = make("li");
      scope.append(make("span", "", "Required scopes"), make("code", "", String(config.requiredScopes.length)));
      list.append(scope);
    }
    card.append(head, list);
    grid.append(card);
  }
  anchor.hidden = true;
}

function recoveryEffectLabel(effects) {
  if (!effects || typeof effects !== "object") return "Unavailable";
  const uncertain = Number(effects.uncertain || 0) + Number(effects.containerUncertain || 0);
  if (uncertain > 0) return `${uncertain} uncertain effect${uncertain === 1 ? "" : "s"}`;
  const active = Number(effects.intent || 0) + Number(effects.containerIntent || 0);
  if (active > 0) return `${active} write intent${active === 1 ? "" : "s"}`;
  const recorded = Number(effects.created || 0) + Number(effects.verified || 0) + Number(effects.unverified || 0) + Number(effects.containerCreated || 0);
  return recorded > 0 ? `${recorded} recorded external effect${recorded === 1 ? "" : "s"}` : "No effect ledger entries";
}

async function renderRecoverySummary() {
  const root = document.getElementById("recovery-status");
  if (!root) return;
  const value = await fetchJson("/api/recovery/status");
  if (!value) return;
  const summary = make("div", "ux-recovery-summary");
  const quarantine = make("div");
  quarantine.append(make("span", "", "Publication fence"), make("strong", "", value.control?.quarantined ? "Quarantined" : "Open"));
  const plan = make("div");
  plan.append(make("span", "", "Recovery plan"), make("strong", "", value.plan?.state || "None"));
  const effects = make("div");
  effects.append(make("span", "", "Effect ledger"), make("strong", "", recoveryEffectLabel(value.effects)));
  summary.append(quarantine, plan, effects);
  root.replaceChildren(summary);
}

function operationSummary(value) {
  if (!value || typeof value !== "object") return "Operation completed.";
  if (value.error?.message) return value.error.message;
  if (value.accepted && value.restartInProgress) return "Recovery transition accepted. The workspace restart is in progress.";
  if (value.reconciled) return "Recovered workspace state reconciled successfully.";
  if (value.resumed) return "Publishing resumed after recovery reconciliation.";
  if (value.cancelled) return "Prepared recovery was cancelled before restore.";
  if (value.revoked) return "Agent authority was revoked.";
  if (value.signedOut) return "Signed out.";
  if (value.id) return `Operation completed. Record ${value.id} was returned.`;
  return "Operation completed. Inspect the durable evidence below if you need the exact machine response.";
}

let feedbackBusy = false;
function enhanceFeedback() {
  if (feedbackBusy) return;
  const root = document.getElementById("result");
  if (!root || root.hidden) return;
  const text = root.textContent || "";
  if (root.dataset.uxSummary === text) return;
  root.classList.add("ux-feedback");
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    const details = document.getElementById("ux-result-evidence");
    if (details) details.hidden = true;
    return;
  }
  feedbackBusy = true;
  const summary = operationSummary(value);
  root.dataset.uxSummary = summary;
  root.textContent = summary;
  let details = document.getElementById("ux-result-evidence");
  if (!details) {
    details = make("details", "ux-raw-evidence");
    details.id = "ux-result-evidence";
    details.append(make("summary", "", "Inspect raw machine evidence"), make("pre"));
    root.after(details);
  }
  details.hidden = false;
  details.querySelector("pre").textContent = text;
  feedbackBusy = false;
}

function stylePageStates() {
  for (const node of document.querySelectorAll("#notice, #receipt-status, #archive-result, #checkpoint-result, #github-probe-result"))
    node.classList.add("ux-feedback");
}

function scheduleOwnerRefresh() {
  setTimeout(() => {
    enhanceRecordLists();
    enhanceFeedback();
    void renderProviderMatrix();
    void renderRecoverySummary();
  }, 250);
}

function boot() {
  document.body.classList.add("ux-owner-ready");
  ensureReceiptToolbar();
  enhanceRecordLists();
  enhanceFeedback();
  stylePageStates();
  void renderProviderMatrix();
  void renderRecoverySummary();

  const observer = new MutationObserver(() => {
    enhanceRecordLists();
    enhanceFeedback();
  });
  for (const root of document.querySelectorAll(".record-list, #result"))
    observer.observe(root, { childList: true, subtree: true, characterData: true });

  document.getElementById("refresh")?.addEventListener("click", scheduleOwnerRefresh, true);
  for (const id of [
    "recovery-prepare",
    "recovery-execute",
    "recovery-reconcile",
    "recovery-resume",
    "recovery-undo",
    "recovery-cancel",
  ]) document.getElementById(id)?.addEventListener("click", scheduleOwnerRefresh, true);
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
}
