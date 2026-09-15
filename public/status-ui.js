const $ = (id) => document.getElementById(id);
function make(tag, cls = "", text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = String(text);
  return el;
}
function pill(text, state = "neutral") {
  const el = make("span", "ux-pill", text);
  el.dataset.state = state;
  return el;
}
async function readJson(path) {
  const response = await fetch(path, {
    method: "GET",
    mode: "same-origin",
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(12000),
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`Unable to read ${path}.`);
  return response.json();
}
function stateStyle(state) {
  if (state === "live_verified") return "verified";
  if (["blocked_external", "external_setup_required", "disabled_policy", "unavailable_capability"].includes(state)) return "warning";
  if (["failed", "unsafe", "error"].includes(state)) return "failed";
  return "neutral";
}
function renderSummary(value) {
  const root = $("status-summary");
  const known = Object.values(value.providers || {}).filter(
    (provider) => typeof provider?.oauth === "boolean",
  );
  const rows = [
    ["Environment", value.environment || "unknown"],
    ["Release", typeof value.release === "string" ? value.release.slice(0, 12) : "unknown"],
    ["Owner admission", value.access?.signupMode || "unknown"],
    [
      "Provider OAuth",
      known.length === 3
        ? `${known.filter((provider) => provider.oauth).length}/3 configured`
        : "status incomplete",
    ],
    [
      "Advanced master",
      value.payments?.advancedEnabled === true
        ? "enabled"
        : value.payments?.advancedEnabled === false
          ? "disabled"
          : "unknown",
    ],
    [
      "Advanced rollout",
      value.runtimeCapabilities?.policies?.advancedRolloutMode ||
        value.payments?.advancedRolloutMode ||
        "unknown",
    ],
  ];
  root.replaceChildren();
  for (const [label, data] of rows) {
    const cell = make("div");
    cell.append(make("span", "", label), make("strong", "", data));
    root.append(cell);
  }
}
function renderProviders(value) {
  const root = $("provider-status");
  root.replaceChildren();
  for (const [provider, label] of [
    ["x", "X"],
    ["threads", "Threads"],
    ["linkedin", "LinkedIn"],
  ]) {
    const available = value.providers?.[provider]?.oauth;
    const row = make("div", "status-row");
    row.append(
      make("strong", "", label),
      make(
        "p",
        "",
        available === true
          ? "The OAuth application is configured in this runtime. This does not establish provider acceptance or account authority."
          : available === false
            ? "The OAuth application is not configured in this runtime."
            : "Provider application status is unknown.",
      ),
      pill(
        available === true
          ? "configured"
          : available === false
            ? "not configured"
            : "unknown",
        available === false ? "warning" : "neutral",
      ),
    );
    root.append(row);
  }
}
function renderGates(ledger) {
  const root = $("release-gates");
  root.replaceChildren();
  const gates = Array.isArray(ledger?.gates) ? ledger.gates : [];
  if (!gates.length) {
    root.append(make("p", "docs-empty", "Reviewed release-gate evidence is unavailable. No healthy state is inferred."));
    return;
  }
  for (const gate of gates) {
    const row = make("div", "status-row");
    row.append(
      make("strong", "", gate.id || "unknown_gate"),
      make("p", "", gate.summary || `${gate.scope || "release"} gate`),
      pill(gate.state || "unknown", stateStyle(gate.state)),
    );
    root.append(row);
  }
}
function unavailable() {
  $("status-note").textContent =
    "Runtime status is unavailable. No provider, gate, rollout or healthy state is inferred.";
  for (const id of ["status-summary", "provider-status", "release-gates"]) {
    const root = $(id);
    root.replaceChildren(
      make("p", "docs-empty", "Unavailable. Inspect /readiness.json or retry later; this view does not assume success."),
    );
  }
}
async function boot() {
  try {
    const [readiness, ledger] = await Promise.all([
      readJson("/readiness.json"),
      readJson("/release-gates.json"),
    ]);
    if (!readiness || typeof readiness.release !== "string") throw new Error("Incomplete runtime evidence");
    renderSummary(readiness);
    renderProviders(readiness);
    renderGates(ledger);
    $("status-note").textContent =
      "This page reports the deployed runtime and reviewed release-gate ledger. Configuration does not imply external provider approval or uptime.";
  } catch {
    unavailable();
  }
}
if (document.readyState === "loading")
  document.addEventListener("DOMContentLoaded", boot, { once: true });
else void boot();
