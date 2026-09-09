import { registerWebMCP } from "./webmcp.js";
const $ = (id) => document.getElementById(id);
let session,
  selectedCampaign,
  paused = false;
const key = () => crypto.randomUUID();
async function api(path, input, method = input === undefined ? "GET" : "POST") {
  const response = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(session?.csrf ? { "X-CSRF-Token": session.csrf } : {}),
    },
    ...(input !== undefined ? { body: JSON.stringify(input) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || "Request failed.");
  return data;
}
const invoke = (name, input = {}) => api("/api/operations/" + name, input);
function show(data, error = false) {
  $("result").hidden = false;
  $("result").textContent =
    typeof data === "string" ? data : JSON.stringify(data, null, 2);
  $("result").classList.toggle("error", error);
}
async function action(fn) {
  try {
    await fn();
  } catch (e) {
    show(e.message, true);
  }
}
function records(id, items, render) {
  const root = $(id);
  root.replaceChildren();
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Nothing here yet.";
    root.append(p);
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "record";
    render(row, item);
    root.append(row);
  }
}
function line(row, value, strong = false) {
  const e = document.createElement(strong ? "strong" : "span");
  e.textContent = value;
  row.append(e);
}
function button(row, label, fn) {
  const b = document.createElement("button");
  b.textContent = label;
  b.onclick = () => action(fn);
  row.append(b);
}
async function refresh() {
  const [status, accounts, projects, receipts, billing, profiles, grants] =
    await Promise.all([
      invoke("workspace_status"),
      invoke("accounts_list"),
      invoke("projects_list"),
      invoke("receipts_list", { limit: 50 }),
      invoke("billing_status"),
      invoke("automation_inspect"),
      api("/api/grants"),
    ]);
  paused = status.publishingPaused;
  $("plan").textContent =
    status.plan === "advanced" ? "Advanced workspace" : "Free publishing";
  $("pause").textContent = paused ? "Resume publishing" : "Pause publishing";
  records("accounts", accounts, (r, a) => {
    line(r, `${a.alias} · ${a.provider}`, true);
    line(
      r,
      `${a.identity.username} · ${a.active ? "Connected" : "Disconnected"}`,
    );
    if (a.active)
      button(r, "Disconnect", async () => {
        show(
          await invoke("account_disconnect", {
            alias: a.alias,
            idempotencyKey: key(),
          }),
        );
        await refresh();
      });
  });
  records("projects", projects, (r, p) => {
    line(r, p.name, true);
    line(r, p.accounts.join(", "));
  });
  $("project-select").replaceChildren(
    ...projects.map((p) => new Option(p.name, p.id)),
  );
  $("account-select").replaceChildren(
    ...accounts
      .filter((a) => a.active)
      .map((a) => new Option(a.alias, a.alias)),
  );
  records("receipts", receipts, (r, d) => {
    line(r, `${d.provider} · ${d.account} · ${d.status}`, true);
    line(
      r,
      new Date(d.dueAt).toLocaleString() + " · " + (d.reason || "") + " ",
    );
    const copy = document.createElement("p");
    copy.textContent = d.text;
    r.append(copy);
    if (d.url) {
      const link = document.createElement("a");
      link.href = d.url;
      link.textContent = "Provider post";
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      r.append(link);
    }
    if (["scheduled", "waiting_container"].includes(d.status))
      button(r, "Cancel", async () => {
        show(
          await invoke("schedule_cancel", {
            delivery: d.id,
            idempotencyKey: key(),
          }),
        );
        await refresh();
      });
    if (d.postId)
      button(r, "Capture metrics", async () =>
        show(
          await invoke("metrics_capture", {
            delivery: d.id,
            idempotencyKey: key(),
          }),
        ),
      );
  });
  $("billing-status").textContent = billing.entitlement
    ? "Confirmed access until " +
      new Date(billing.entitlement.until).toLocaleString()
    : billing.methods.checkout.available
      ? "Subscription checkout is available."
      : "Purchases are not enabled until deployment validation is complete.";
  $("subscribe").disabled = !billing.methods.checkout.available;
  records("profiles", profiles.profiles, (r, p) => {
    line(r, p.id + " · " + (p.enabled ? "Running" : "Paused"), true);
    line(r, p.repository + " · " + (p.error || p.family));
    button(r, p.enabled ? "Pause" : "Enable", async () => {
      show(
        await invoke(p.enabled ? "automation_pause" : "automation_enable", {
          id: p.id,
          idempotencyKey: key(),
        }),
      );
      await refresh();
    });
  });
  records("grants", grants, (r, g) => {
    line(r, g.actor, true);
    line(r, (g.revoked_at ? "Revoked · " : "") + g.scopes);
    if (!g.revoked_at)
      button(r, "Revoke", async () => {
        await api("/api/grants", { id: g.id }, "DELETE");
        await refresh();
      });
  });
}
for (const id of ["connection", "project", "campaign", "delivery", "grant"])
  $(id).onsubmit = (e) => {
    e.preventDefault();
    const form = e.currentTarget,
      fd = new FormData(form);
    action(async () => {
      if (id === "connection") {
        const input = {
          alias: fd.get("alias"),
          provider: fd.get("provider"),
          accessToken: fd.get("accessToken"),
          ...(fd.get("expiry")
            ? { expiresAt: new Date(fd.get("expiry")).getTime() }
            : {}),
          ...(fd.has("funding") ? { funding: "customer_app" } : {}),
        };
        show(await api("/api/connections/import", input));
        form.reset();
        await refresh();
      }
      if (id === "project") {
        show(
          await invoke("project_put", {
            id: fd.get("id"),
            name: fd.get("name"),
            accounts: fd
              .get("accounts")
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean),
            idempotencyKey: key(),
          }),
        );
        await refresh();
      }
      if (id === "campaign") {
        selectedCampaign = await invoke("campaign_create", {
          project: fd.get("project"),
          text: { [fd.get("alias")]: fd.get("text") },
          idempotencyKey: key(),
        });
        await invoke("campaign_validate", { campaign: selectedCampaign.id });
        $("campaign-preview").hidden = false;
        $("campaign-preview").textContent = Object.entries(
          selectedCampaign.text,
        )
          .map(([a, t]) => a + "\n" + t)
          .join("\n\n");
        $("delivery").hidden = false;
        show(
          "Campaign stored and validated. Review the exact copy before submitting.",
        );
      }
      if (id === "delivery") {
        const at = fd.get("at");
        show(
          await invoke(at ? "schedule_create" : "publish_now", {
            campaign: selectedCampaign.id,
            idempotencyKey: key(),
            ...(at
              ? {
                  at,
                  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                }
              : {}),
          }),
        );
        await refresh();
      }
      if (id === "grant") {
        const grant = await api("/api/grants", {
          scopes: fd.getAll("scope"),
          hours: Number(fd.get("hours")),
        });
        $("token").hidden = false;
        $("token").textContent = grant.token;
        await refresh();
      }
    });
  };
$("refresh").onclick = () => action(refresh);
$("pause").onclick = () =>
  action(async () => {
    show(
      await invoke("publishing_pause", {
        paused: !paused,
        idempotencyKey: key(),
      }),
    );
    await refresh();
  });
$("signout").onclick = () =>
  action(async () => {
    await api("/auth/logout", {});
    location.assign("/");
  });
$("export").onclick = () =>
  action(async () => {
    const data = await invoke("workspace_export");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "publishing-workspace.json";
    a.click();
    URL.revokeObjectURL(url);
  });
$("subscribe").onclick = () =>
  action(async () => {
    const q = await invoke("billing_quote", {
      mode: "subscription",
      idempotencyKey: key(),
    });
    $("quote").hidden = false;
    $("quote").textContent =
      "$5.00 USD per month. Automatically renews. Manage cancellation in Stripe. ";
    button($("quote"), "Continue to Stripe", async () => {
      const checkout = await invoke("billing_checkout", {
        quote: q.id,
        idempotencyKey: key(),
      });
      location.assign(checkout.url);
    });
  });
$("portal").onclick = () =>
  action(async () => {
    const p = await invoke("billing_portal", { idempotencyKey: key() });
    location.assign(p.url);
  });
try {
  session = await api("/api/session");
  $("session-notice").textContent = "Workspace " + session.workspace;
  const help = await api("/help.json");
  const registered = await registerWebMCP(help, invoke, session.scopes);
  $("webmcp-status").textContent = registered.available
    ? `${registered.count} browser agent tools available.`
    : "Remote MCP and HTTP are available. This browser has no native WebMCP support.";
  await refresh();
} catch (e) {
  $("session-notice").replaceChildren();
  const a = document.createElement("a");
  a.href = "/auth/login";
  a.textContent = "Sign in to open your workspace";
  $("session-notice").append(a);
  show(e.message, true);
}
