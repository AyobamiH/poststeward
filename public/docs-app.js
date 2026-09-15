const $ = (id) => document.getElementById(id);

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
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
  if (!response.ok) throw new Error(`Unable to load ${path}.`);
  return response.json();
}

function setRuntime(value) {
  const root = $("docs-runtime");
  if (!root || !value) return;
  const providerCount = Object.values(value.providers || {}).filter((provider) => provider?.oauth).length;
  const rows = [
    ["Environment", value.environment || "unknown"],
    ["Owner admission", value.access?.signupMode || "unknown"],
    ["Provider OAuth", `${providerCount}/3 configured`],
    ["Advanced", value.payments?.advancedEnabled ? "master enabled" : "master disabled"],
    ["Rollout", value.payments?.advancedRolloutMode || "disabled"],
    ["Release", typeof value.release === "string" ? value.release.slice(0, 12) : "unknown"],
  ];
  root.replaceChildren();
  for (const [label, data] of rows) {
    const cell = make("div");
    cell.append(make("span", "", label), make("strong", "", data));
    root.append(cell);
  }
}

function statePill(text, state = "neutral") {
  const node = make("span", "ux-pill", text);
  node.dataset.state = state;
  return node;
}

function operationState(operation) {
  const effects = Array.isArray(operation.effects) ? operation.effects : [];
  if (!effects.length || effects.every((effect) => effect === "none" || effect === "read"))
    return "neutral";
  return "warning";
}

function renderOperation(operation) {
  const card = make("article", "operation-card");
  card.dataset.name = String(operation.name || "").toLowerCase();
  card.dataset.tier = String(operation.tier || "").toLowerCase();
  card.dataset.scope = String(operation.scope || "").toLowerCase();
  card.id = `operation-${operation.name}`;

  const head = make("div", "operation-card-head");
  const title = make("div");
  title.append(make("h2", "", operation.name || "Unnamed operation"));
  title.append(make("p", "", operation.description || ""));
  const meta = make("div", "operation-meta");
  meta.append(
    statePill(operation.tier || "unknown"),
    statePill(operation.scope || "unknown"),
    statePill(
      Array.isArray(operation.effects) && operation.effects.length
        ? operation.effects.join(" + ")
        : "read-only",
      operationState(operation),
    ),
  );
  head.append(title, meta);

  const details = document.createElement("details");
  const summary = make("summary", "", "Contract and example");
  const list = make("dl", "browser-error-meta");
  const pairs = [
    ["Inspect with", operation.inspection || "See operation result"],
    ["Retry", operation.retry || "Follow returned evidence"],
  ];
  for (const [label, value] of pairs) {
    const item = make("div");
    item.append(make("dt", "", label), make("dd", "", value));
    list.append(item);
  }
  const pre = make("pre");
  pre.textContent = JSON.stringify(operation.example || {}, null, 2);
  details.append(summary, list, pre);
  card.append(head, details);
  return card;
}

function applyOperationFilter() {
  const query = ($("operation-search")?.value || "").trim().toLowerCase();
  const tier = ($("operation-tier")?.value || "").toLowerCase();
  const scope = ($("operation-scope")?.value || "").toLowerCase();
  let shown = 0;
  for (const card of document.querySelectorAll(".operation-card")) {
    const queryMatch = !query || card.textContent.toLowerCase().includes(query);
    const tierMatch = !tier || card.dataset.tier === tier;
    const scopeMatch = !scope || card.dataset.scope === scope;
    card.hidden = !(queryMatch && tierMatch && scopeMatch);
    if (!card.hidden) shown += 1;
  }
  const empty = $("operations-empty");
  if (empty) empty.hidden = shown !== 0;
  const count = $("operation-count");
  if (count) count.textContent = `${shown} operation${shown === 1 ? "" : "s"}`;
}

async function loadOperations() {
  const root = $("operations-grid");
  if (!root) return;
  try {
    const operations = await readJson("/catalog.json");
    root.replaceChildren(...operations.map(renderOperation));
    const tiers = [...new Set(operations.map((item) => item.tier).filter(Boolean))].sort();
    const scopes = [...new Set(operations.map((item) => item.scope).filter(Boolean))].sort();
    const tierSelect = $("operation-tier");
    const scopeSelect = $("operation-scope");
    for (const tier of tiers) tierSelect?.append(new Option(tier, tier));
    for (const scope of scopes) scopeSelect?.append(new Option(scope, scope));
    applyOperationFilter();
  } catch (error) {
    root.replaceChildren(make("p", "docs-empty", error instanceof Error ? error.message : "Unable to load the operation catalogue."));
  }
}

function copyCode(button) {
  const block = button.closest(".docs-code-wrap")?.querySelector("pre");
  if (!block) return;
  navigator.clipboard?.writeText(block.textContent || "").then(
    () => {
      const original = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = original), 1200);
    },
    () => {},
  );
}

async function boot() {
  try {
    setRuntime(await readJson("/readiness.json"));
  } catch {
    const root = $("docs-runtime");
    if (root) root.replaceChildren(make("p", "docs-empty", "Runtime readiness is temporarily unavailable. Machine clients should treat /help.json and operation errors as authoritative."));
  }
  await loadOperations();
  for (const id of ["operation-search", "operation-tier", "operation-scope"])
    $(id)?.addEventListener(id === "operation-search" ? "input" : "change", applyOperationFilter);
  for (const button of document.querySelectorAll("[data-copy-code]"))
    button.addEventListener("click", () => copyCode(button));
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
else void boot();
