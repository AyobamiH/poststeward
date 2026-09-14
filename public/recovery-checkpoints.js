const $ = (id) => document.getElementById(id);
let session;

async function api(path, input, method = input === undefined ? "GET" : "POST") {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    mode: "same-origin",
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(20000),
    headers: {
      "Content-Type": "application/json",
      ...(session?.csrf ? { "X-CSRF-Token": session.csrf } : {}),
    },
    ...(input === undefined ? {} : { body: JSON.stringify(input) }),
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(value.error?.message || "Request failed.");
    error.code = value.error?.code;
    throw error;
  }
  return value;
}

function show(value, error = false) {
  $("checkpoint-result").hidden = false;
  $("checkpoint-result").textContent =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  $("checkpoint-result").classList.toggle("error", error);
}

function render(checkpoints) {
  const list = $("checkpoint-list");
  const select = $("checkpoint-select");
  list.replaceChildren();
  select.replaceChildren();
  for (const checkpoint of checkpoints) {
    const row = document.createElement("div");
    row.className = "record";
    const title = document.createElement("strong");
    title.textContent = `${new Date(checkpoint.capturedAt).toLocaleString()} · ${checkpoint.source}`;
    const details = document.createElement("span");
    details.textContent = `release ${checkpoint.release.slice(0, 12)} · root ${checkpoint.rootWrite} · state ${checkpoint.stateDigest.slice(0, 12)}`;
    row.append(title, details);
    list.append(row);
    select.append(
      new Option(
        `${new Date(checkpoint.capturedAt).toLocaleString()} · ${checkpoint.release.slice(0, 12)} · ${checkpoint.source}`,
        checkpoint.id,
      ),
    );
  }
  if (!checkpoints.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No exact checkpoints are available yet. Capture one before an incident, or keep the workspace active so PostSteward can capture bounded periodic checkpoints.";
    list.append(empty);
  }
  $("checkpoint-prepare-submit").disabled = checkpoints.length === 0;
}

async function refresh() {
  const value = await api("/api/recovery/checkpoints");
  render(value.checkpoints || []);
}

async function action(fn) {
  try {
    await fn();
  } catch (error) {
    show(error.message, true);
  }
}

$("checkpoint-capture").onclick = () =>
  action(async () => {
    const checkpoint = await api("/api/recovery/checkpoints", { capture: true });
    show({ checkpoint, note: "Exact checkpoint captured. No restore was prepared or executed." });
    await refresh();
  });

$("checkpoint-refresh").onclick = () => action(refresh);

$("checkpoint-prepare").onsubmit = (event) => {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  action(async () => {
    const result = await api("/api/recovery/prepare", {
      checkpoint: data.get("checkpoint"),
      reason: data.get("reason"),
    });
    show({
      ...result,
      next: "Open Workspace, type the exact RESTORE <workspace> confirmation shown there, then execute only after reviewing this immutable plan.",
    });
  });
};

$("approximate-prepare").onsubmit = (event) => {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  action(async () => {
    const at = new Date(data.get("at"));
    if (!Number.isFinite(at.getTime())) throw new Error("Choose a valid recovery target.");
    const result = await api("/api/recovery/prepare", {
      at: at.toISOString(),
      reason: data.get("reason"),
    });
    show({
      ...result,
      warning: "This plan used Cloudflare's approximate timestamp-to-bookmark path, not an exact stored checkpoint.",
    });
  });
};

try {
  session = await api("/api/session");
  $("recovery-session").textContent = `Workspace ${session.workspace}`;
  $("checkpoint-capture").disabled = false;
  $("checkpoint-refresh").disabled = false;
  await refresh();
} catch (error) {
  $("recovery-session").replaceChildren();
  const link = document.createElement("a");
  link.href = "/auth/login?return=%2Frecovery";
  link.textContent = "Sign in to open recovery controls";
  $("recovery-session").append(link);
  show(error.message, true);
}
