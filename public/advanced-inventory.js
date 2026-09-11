const $ = (id) => document.getElementById(id);
let csrf = "";

async function request(path, input) {
  const response = await fetch(path, {
    method: input === undefined ? "GET" : "POST",
    credentials: "same-origin",
    mode: "same-origin",
    cache: "no-store",
    redirect: "manual",
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    },
    ...(input === undefined ? {} : { body: JSON.stringify(input) }),
    signal: AbortSignal.timeout(20_000),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error?.message || `Request failed (${response.status}).`);
  return body;
}

const invoke = (name, input = {}) => request(`/api/operations/${name}`, input);

function text(parent, value, strong = false) {
  const node = document.createElement(strong ? "strong" : "span");
  node.textContent = value;
  parent.append(node);
}

function records(id, items, render) {
  const root = $(id);
  root.replaceChildren();
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Nothing here yet.";
    root.append(p);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "record";
    render(row, item);
    root.append(row);
  }
}

function button(parent, label, handler, disabled = false) {
  const control = document.createElement("button");
  control.type = "button";
  control.textContent = label;
  control.disabled = disabled;
  control.addEventListener("click", async () => {
    control.disabled = true;
    try {
      const result = await handler();
      $("result").hidden = false;
      $("result").textContent = JSON.stringify(result, null, 2);
    } catch (error) {
      $("result").hidden = false;
      $("result").textContent = error instanceof Error ? error.message : String(error);
    } finally {
      control.disabled = disabled;
    }
  });
  parent.append(control);
}

function categories(profiles, deliveries) {
  const groups = new Map();
  for (const profile of profiles) {
    const family = profile.family;
    const group = groups.get(family) || {
      family,
      profiles: [],
      enabled: 0,
      pending: 0,
      minSpacingMinutes: Infinity,
      intervalMinutes: Infinity,
    };
    group.profiles.push(profile.id);
    if (profile.enabled) group.enabled++;
    group.minSpacingMinutes = Math.min(group.minSpacingMinutes, profile.minSpacingMinutes);
    group.intervalMinutes = Math.min(group.intervalMinutes, profile.intervalMinutes);
    groups.set(family, group);
  }
  for (const delivery of deliveries) {
    const profile = profiles.find((item) => item.id === delivery.policy);
    if (!profile) continue;
    const group = groups.get(profile.family);
    if (group && ["scheduled", "waiting_container"].includes(delivery.status)) group.pending++;
  }
  return [...groups.values()].sort((a, b) => a.family.localeCompare(b.family));
}

async function load() {
  const session = await request("/api/session");
  csrf = session.csrf || "";
  const [status, inspect] = await Promise.all([
    invoke("workspace_status"),
    invoke("automation_inspect"),
  ]);
  const profiles = inspect.profiles || [];
  const deliveries = inspect.deliveries || [];
  $("status").textContent =
    `Workspace ${session.workspace} · ${status.plan === "advanced" ? "Advanced active" : "Advanced execution disabled"} · ${profiles.length} reviewed profiles.`;

  records("categories", categories(profiles, deliveries), (row, category) => {
    text(row, category.family, true);
    text(
      row,
      `${category.profiles.length} profile(s) · ${category.enabled} running · ${category.pending} pending · minimum profile spacing ${category.minSpacingMinutes}m`,
    );
    text(row, category.profiles.join(", "));
  });

  records("inventory", profiles, (row, profile) => {
    text(row, `${profile.id} · ${profile.family}`, true);
    text(
      row,
      `${profile.repository}@${profile.branch}:${profile.path} · ${profile.sha ? `source ${profile.sha.slice(0, 12)}` : "baseline not yet observed"}`,
    );
    text(
      row,
      `${profile.enabled ? "Running" : "Paused"}${profile.error ? ` · ${profile.error}` : ""}${profile.lastCheck ? ` · checked ${new Date(profile.lastCheck).toLocaleString()}` : ""}`,
    );
    button(
      row,
      "Preview source allocation",
      () => invoke("automation_preview", { id: profile.id }),
      status.plan !== "advanced",
    );
  });

  records(
    "deliveries",
    deliveries.slice().sort((a, b) => b.createdAt - a.createdAt),
    (row, delivery) => {
      const profile = profiles.find((item) => item.id === delivery.policy);
      text(row, `${profile?.family || "uncategorised"} · ${delivery.status}`, true);
      text(row, `${delivery.account} · ${new Date(delivery.dueAt).toLocaleString()}`);
      text(
        row,
        `${delivery.postId ? "provider creation ID recorded" : "no provider creation ID"} · ${delivery.metrics ? "metrics captured" : "metrics not captured"}`,
      );
    },
  );
}

load().catch((error) => {
  $("status").replaceChildren();
  const link = document.createElement("a");
  link.href = "/auth/login?return=/app";
  link.textContent = "Sign in to open Advanced inventory";
  $("status").append(link);
  $("result").hidden = false;
  $("result").textContent = error instanceof Error ? error.message : String(error);
});
