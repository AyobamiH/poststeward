/** Read-only presentation. Existing application handlers retain every operation. */
export const receiptStates = {
  scheduled: ["Scheduled", "pending"], executing: ["Executing", "pending"],
  waiting_container: ["Waiting for Threads", "pending"], cancelled: ["Cancelled", "cancelled"],
  published_verified: ["Verified", "verified"], published_unverified: ["Needs readback", "warning"],
  ambiguous_effect: ["Ambiguous effect", "ambiguous"], failed: ["Failed", "failed"], drift_blocked: ["Drift blocked", "blocked"],
};
const providers = { x: "X", threads: "Threads", linkedin: "LinkedIn" };
const emptyCopy = {
  accounts: ["No social destinations connected", "Connect your first destination using the provider controls above."],
  projects: ["No publishing projects yet", "Create a project that binds approved work to explicit connected accounts."],
  receipts: ["No delivery evidence yet", "A receipt appears after work is reserved. A reservation is not proof of publication."],
  grants: ["No delegated agents", "Issue an expiring, scoped grant only when an agent needs access."],
  profiles: ["No automation profiles", "Add reviewed sources, templates, destinations and spacing before enabling a profile."],
  inventory: ["No reviewed inventory", "An enabled, reviewed profile must observe a source change before it can allocate work."],
  categories: ["No reviewed categories", "Categories describe the source families of your reviewed profiles."],
  deliveries: ["No automatic allocations", "Automatic allocations require a reviewed, enabled profile."],
};
export function grantState(grant, now = Date.now()) {
  if (grant.revoked_at) return ["Revoked", "revoked"];
  if (!Number.isFinite(grant.expires_at)) return ["Expiry unknown", "warning"];
  return grant.expires_at <= now ? ["Expired", "paused"] : ["Active", "connected"];
}
export function receiptState(state) { return Object.hasOwn(receiptStates, state) ? receiptStates[state] : ["Unknown state", "warning"]; }
export function operationSummary(value) {
  if (!value || typeof value !== "object") return "Response received. Inspect the existing evidence before taking another action.";
  if (value.error) return String(value.error.message || "Operation did not complete.");
  if (value.accepted && value.restartInProgress) return "Recovery transition accepted. The restart is still in progress.";
  if (value.reconciled === true) return "Recovery reconciliation completed. Publication remains subject to the current fence.";
  if (value.resumed === true) return "The server confirmed publication was resumed.";
  if (value.cancelled === true) return "Cancellation recorded. Inspect the existing record for its final state.";
  if (value.revoked === true) return "The server confirmed revocation.";
  const deliveries = Array.isArray(value) ? value : value.deliveries;
  if (Array.isArray(deliveries) && deliveries.length) return `${deliveries.length} record(s) returned. ${[...new Set(deliveries.map((d) => receiptState(d.status)[0]))].join(" · ")}. Inspect receipts for provider evidence.`;
  if (value.status) return `${receiptState(value.status)[0]}. Inspect the server evidence below.`;
  return "Response received. Inspect the returned record below; this is not a claim of verified publication.";
}
function node(tag, cls = "", text) {
  const el = document.createElement(tag); if (cls) el.className = cls;
  if (text !== undefined) el.textContent = String(text); return el;
}
function badge(label, state = "neutral") { const el = node("span", "ux-pill", label); el.dataset.state = state; return el; }
function time(value) { return Number.isFinite(value) ? new Date(value).toLocaleString() : "Not recorded"; }
function fields(values) {
  const dl = node("dl", "ux-fields");
  for (const [label, value] of values) { const cell = node("div"); cell.append(node("dt", "", label), node("dd", "", value ?? "Not recorded")); dl.append(cell); }
  return dl;
}
function title(row, label, badges) {
  row.querySelector(":scope > strong")?.remove();
  const head = node("div", "ux-record-heading"), tags = node("div", "ux-badges");
  tags.append(...badges.map((args) => badge(...args))); head.append(node("strong", "ux-record-title", label), tags); row.prepend(head);
}
function actions(row) {
  const controls = [...row.children].filter((el) => ["BUTTON", "A"].includes(el.tagName));
  if (controls.length) { const group = node("div", "ux-record-actions"); group.append(...controls); row.append(group); }
}
function enrichRows(id, items, render) {
  const root = document.getElementById(id); if (!root) return;
  const rows = [...root.querySelectorAll(":scope > .record")];
  if (rows.length !== items.length) return;
  rows.forEach((row, i) => { if (row.dataset.uxEnhanced) return; render(row, items[i]); actions(row); row.dataset.uxEnhanced = "true"; });
}
function emptyStates() {
  for (const [id, [title, body]] of Object.entries(emptyCopy)) {
    const root = document.getElementById(id);
    if (root?.children.length === 1 && root.firstElementChild.textContent.trim() === "Nothing here yet.") {
      const empty = node("div", "ux-empty-state"); empty.append(node("strong", "", title), node("p", "", body)); root.replaceChildren(empty);
    }
  }
}
function capabilityState(value) {
  const labels = { available: "Available to this grant", unavailable: "Unavailable", connection_required: "Connection required", external_approval_required: "Approval required", unknown: "Unknown" };
  return Object.hasOwn(labels, value?.state) ? labels[value.state] : "Unknown";
}
export function accountReadbackLabel(account, connection) {
  const capability = connection?.capabilities?.readback;
  if (
    account?.provider === "threads" &&
    account?.capabilities?.readback === true &&
    capability?.state === "unknown" &&
    capability?.reason === "scope_response_absent"
  )
    return "Baseline requested; provider did not echo scope";
  if (connection) return capabilityState(capability);
  return account?.capabilities?.readback === true
    ? "Reported supported; inspect receipt evidence"
    : "Unknown / unavailable";
}
function renderProviders(snapshot) {
  const anchor = document.getElementById("oauth-status"); if (!anchor) return;
  let grid = document.getElementById("ux-provider-grid");
  if (!grid) { grid = node("div", "ux-provider-grid"); grid.id = "ux-provider-grid"; anchor.after(grid); }
  grid.replaceChildren();
  const info = snapshot.oauthInfo;
  if (!info?.providers) { grid.append(node("p", "", "Provider status could not be loaded. No connection or capability is inferred.")); anchor.hidden = true; return; }
  for (const provider of Object.keys(providers)) {
    const config = info.providers[provider];
    const accounts = snapshot.accounts.filter((a) => a.provider === provider && a.active === true);
    const card = node("section", "ux-provider-card"); card.setAttribute("aria-label", providers[provider] + " capabilities");
    const head = node("div", "ux-provider-card-head");
    head.append(node("strong", "", providers[provider]), badge(config?.available === true ? "App configured" : config?.available === false ? "OAuth unconfigured" : "Status unknown", config?.available === false ? "warning" : "neutral"));
    card.append(head, node("p", "", `${accounts.length} connected account(s). Application configuration is not provider acceptance.`));
    const list = node("ul", "ux-capability-list");
    for (const key of ["publish", "readback", "refresh", "metrics"]) {
      const li = node("li"); li.append(node("span", "", key), node("span", "", capabilityState(config?.capabilities?.[key]))); list.append(li);
    }
    card.append(list); grid.append(card);
  }
  anchor.hidden = true;
}
function renderRecovery(value) {
  const root = document.getElementById("recovery-status"); if (!root) return;
  const fence = value?.control?.quarantined;
  const summary = node("div", "ux-recovery-summary");
  for (const [label, text] of [["Publication fence", fence === true ? "Quarantined" : fence === false ? "Open" : "Unknown"], ["Recovery plan", value?.plan?.state || (value ? "None" : "Unavailable")], ["Uncertain effects", value?.effects ? String(Number(value.effects.uncertain || 0) + Number(value.effects.containerUncertain || 0)) : "Unknown"]]) {
    const cell = node("div"); cell.append(node("span", "", label), node("strong", "", text)); summary.append(cell);
  }
  root.replaceChildren(summary);
  if (value?.plan) {
    const states = ["prepared", "armed", "reconciled", "resumed"];
    const current = value.plan.state === "reconciled" && fence === false ? "resumed" : value.plan.state;
    const journey = node("ol", "ux-recovery-journey"); journey.setAttribute("aria-label", "Current recovery plan progress");
    for (const state of states) { const item = node("li", "", state); if (current === state) item.setAttribute("aria-current", "step"); journey.append(item); }
    root.append(journey, fields([["Plan", value.plan.id], ["Target", time(value.plan.targetTime)], ["Reason", value.plan.reason]]));
  }
}
export function renderOwnerSnapshot(snapshot) {
  emptyStates();
  enrichRows("accounts", snapshot.accounts, (row, account) => {
    title(row, account.alias, [[providers[account.provider] || "Unknown provider"], [account.active === true ? "Connected" : "Disconnected", account.active === true ? "connected" : "disconnected"]]);
    row.querySelector(":scope > span")?.remove();
    const connection = snapshot.oauthInfo?.connections?.find((item) => item.alias === account.alias);
    row.append(fields([["Account", account.identity?.username], ["Stable author ID", account.identity?.id], ["Connection verified", time(account.verifiedAt)], ["Readback permission", accountReadbackLabel(account, connection)]]));
  });
  enrichRows("receipts", snapshot.receipts, (row, receipt) => {
    title(row, receipt.account, [[providers[receipt.provider] || "Unknown provider"], receiptState(receipt.status)]);
    row.dataset.receiptState = receipt.status;
    row.dataset.search = [receipt.account, receipt.provider, receipt.text, receipt.reason, receipt.id].join(" ").toLowerCase();
    row.querySelector(":scope > span")?.remove();
    const copy = row.querySelector(":scope > p");
    if (copy) { const details = node("details", "ux-exact-copy"); details.append(node("summary", "", "Inspect exact approved copy")); details.append(copy); row.append(details); }
    row.append(fields([["Scheduled for", `${time(receipt.dueAt)} · ${receipt.timezone || "zone not recorded"}`], ["Last record update", time(receipt.updatedAt)], ["Receipt ID", receipt.id], ["Provider creation ID", receipt.postId || "Not recorded"]]));
    if (receipt.reason) row.append(node("p", "", receipt.reason));
    if (receipt.status === "ambiguous_effect") row.append(node("p", "ux-state-warning", "The provider may have published. Do not republish to repair this uncertainty."));
    if (!Object.hasOwn(receiptStates, receipt.status)) row.append(node("p", "ux-state-warning", "Unfamiliar server state. Inspect evidence; do not assume success or retry."));
  });
  enrichRows("grants", snapshot.grants, (row, grant) => {
    title(row, grant.actor, [grantState(grant)]);
    row.append(fields([["Expires", time(grant.expires_at)]]));
  });
  enrichRows("profiles", snapshot.profiles.profiles, (row, profile) => {
    title(row, profile.id, [[profile.enabled ? "Running" : "Paused", profile.enabled ? "running" : "paused"]]);
    row.append(fields([["Source checked", time(profile.lastCheck)], ["Next observation", time(profile.nextRun)], ["Minimum spacing", `${profile.minSpacingMinutes} minutes`]]));
  });
  renderProviders(snapshot); renderRecovery(snapshot.recovery); filterReceipts();
}
export function showFeedback(value, error = false, target) {
  const root = document.getElementById("result"); if (!root) return;
  root.hidden = false; root.classList.add("ux-feedback"); root.classList.toggle("error", error);
  root.setAttribute("role", error ? "alert" : "status"); root.setAttribute("aria-atomic", "true");
  root.textContent = typeof value === "string" ? value : operationSummary(value);
  let details = document.getElementById("ux-result-evidence");
  if (!details) { details = node("details", "ux-raw-evidence"); details.id = "ux-result-evidence"; details.append(node("summary", "", "Inspect raw machine evidence"), node("pre")); }
  if (target?.isConnected && !target.contains(root)) target.after(root);
  root.after(details); details.hidden = !value || typeof value !== "object"; details.open = false;
  details.querySelector("pre").textContent = details.hidden ? "" : JSON.stringify(value, null, 2);
}
function ensureToolbar() {
  const root = document.getElementById("receipts"); if (!root || document.getElementById("ux-receipt-filter")) return;
  const toolbar = node("div", "ux-evidence-toolbar");
  const label = node("label", "", "Receipt state"), select = node("select"); select.id = "ux-receipt-filter";
  select.append(new Option("All states", ""));
  for (const [state, [text]] of Object.entries(receiptStates)) select.append(new Option(text, state));
  const searchLabel = node("label", "", "Search loaded evidence"), search = node("input"); search.type = "search"; search.id = "ux-receipt-search"; search.maxLength = 200;
  label.append(select); searchLabel.append(search); toolbar.append(label, searchLabel); root.before(toolbar);
  const count = node("p", "muted"); count.id = "ux-receipt-count"; count.setAttribute("role", "status"); toolbar.after(count);
  select.addEventListener("change", filterReceipts); search.addEventListener("input", filterReceipts);
}
function filterReceipts() {
  const root = document.getElementById("receipts"); if (!root) return;
  ensureToolbar();
  const state = document.getElementById("ux-receipt-filter").value, query = document.getElementById("ux-receipt-search").value.trim().toLowerCase();
  const rows = [...root.querySelectorAll(":scope > .record")]; let shown = 0;
  for (const row of rows) { row.hidden = Boolean((state && row.dataset.receiptState !== state) || (query && !(row.dataset.search || row.textContent.toLowerCase()).includes(query))); if (!row.hidden) shown++; }
  document.getElementById("ux-receipt-count").textContent = `${shown} of ${rows.length} loaded receipts. This view loads at most the latest 50, not the complete history.${rows.length && !shown ? " No loaded receipts match these filters." : ""}`;
}
function boot() {
  document.body.classList.add("ux-owner-ready"); ensureToolbar(); emptyStates();
  const observer = new MutationObserver(emptyStates);
  for (const root of document.querySelectorAll(".record-list")) observer.observe(root, { childList: true });
  window.addEventListener("pagehide", () => observer.disconnect(), { once: true });
}
if (typeof document !== "undefined") {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true }); else boot();
}
