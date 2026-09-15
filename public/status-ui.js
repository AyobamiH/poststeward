const $ = (id) => document.getElementById(id);

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pill(text, state = "neutral") {
  const node = make("span", "ux-pill", text);
  node.dataset.state = state;
  return node;
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
  if (["live_verified", "healthy", "enabled"].includes(state)) return "verified";
  if (["blocked_external", "external_setup_required", "disabled_policy", "unavailable_capability"].includes(state)) return "warning";
  if (["failed", "unsafe", "error"].includes(state)) return "failed";
  return "neutral";
}

function renderSummary(readiness) {
  const root = $("status-summary");
  const providers = Object.values(readiness.providers || {}).filter((provider) => provider?.oauth).length;
  const rows = [
    ["Environment", readiness.environment || "unknown"],
    ["Release", typeof readiness.release === "string" ? readiness.release.slice(0, 12) : "unknown"],
    ["Owner admission", readiness.access?.signupMode || "unknown"],
    ["Provider OAuth", `${providers}/3 configured`],
    ["Advanced master", readiness.payments?.advancedEnabled ? "enabled" : "disabled"],
    ["Advanced rollout", readiness.payments?.advancedRolloutMode || "disabled"],
  ];
  root.replaceChildren();
  for (const [label, value] of rows) {
    const cell = make("div");
    cell.append(make("span", "", label), make("strong", "", value));
    root.append(cell);
  }
}

function renderProviders(readiness) {
  const root = $("provider-status");
  root.replaceChildren();
  for (const provider of ["x", "threads", "linkedin"]) {
    const value = readiness.providers?.[provider] || {};
    const row = make("div", "status-row");
    const label = provider === "x" ? "X" : provider[0].toUpperCase() + provider.slice(1);
    row.append(
      make("strong", "", label),
      make("p", "", value.oauth ? "OAuth application is configured in this runtime." : "OAuth application is not configured in this runtime."),
      pill(value.oauth ? "configured" : "not configured", value.oauth ? "verified" : "warning"),
    );
    root.append(row);
  }
}

function renderGates(ledger) {
  const root = $("release-gates");
  root.replaceChildren();
  for (const gate of ledger.gates || []) {
    const row = make("div", "status-row");
    const summary = gate.summary || `${gate.scope || "release"} gate`;
    row.append(
      make("strong", "", gate.id),
      make("p", "", summary),
      pill(gate.state || "unknown", stateStyle(gate.state)),
    );
    root.append(row);
  }
}

async function boot() {
  try {
    const [readiness, ledger] = await Promise.all([
      readJson("/readiness.json"),
      readJson("/release-gates.json"),
    ]);
    renderSummary(readiness);
    renderProviders(readiness);
    renderGates(ledger);
    $("status-note").textContent = "This page reports the deployed runtime and reviewed release-gate ledger. It does not infer external provider approval from source code.";
  } catch (error) {
    $("status-note").textContent = error instanceof Error ? error.message : "Runtime status is temporarily unavailable.";
  }
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
else void boot();
