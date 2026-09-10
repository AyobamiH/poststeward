import { registerWebMCP } from "./webmcp.js";
const $ = (id) => document.getElementById(id);
let session,
  selectedCampaign,
  paused = false,
  oauthInfo,
  recovery;
const key = () => crypto.randomUUID();
async function api(path, input, method = input === undefined ? "GET" : "POST") {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    redirect: "manual",
    headers: {
      "Content-Type": "application/json",
      ...(session?.csrf ? { "X-CSRF-Token": session.csrf } : {}),
    },
    ...(input !== undefined ? { body: JSON.stringify(input) } : {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error?.message || "Request failed.");
    error.code = data.error?.code;
    error.status = response.status;
    throw error;
  }
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
function button(row, label, fn, disabled = false) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.disabled = disabled;
  b.onclick = () => action(fn);
  row.append(b);
}
function renderOAuth() {
  if (!oauthInfo) return;
  for (const b of $("oauth-buttons").querySelectorAll("button[data-provider]"))
    b.disabled = oauthInfo.providers?.[b.dataset.provider]?.available !== true;
  const providers = Object.entries(oauthInfo.providers || {}).map(
    ([name, value]) =>
      `${name}: ${value.available ? "OAuth ready" : "provider app not configured"}${name === "linkedin" ? (value.readback ? ", readback enabled" : ", readback approval absent") : ""}`,
  );
  const connections = (oauthInfo.connections || []).map(
    (item) =>
      `${item.alias}: ${item.status}${item.needsReauthorization ? ", reauthorise" : ""}`,
  );
  $("oauth-status").textContent =
    [...providers, ...connections].join(" · ") ||
    "No provider applications are configured.";
}
function renderRecovery() {
  if (!recovery) {
    $("recovery-status").textContent = "Recovery status unavailable.";
    return;
  }
  const plan = recovery.plan;
  $("recovery-status").textContent =
    `Quarantine: ${recovery.control?.quarantined ? "ON" : "off"}. External effects: ${JSON.stringify(recovery.effects || {})}. ${plan ? `Plan ${plan.id} · ${plan.state} · target ${new Date(plan.targetTime).toISOString()} · ${plan.reason}` : "No recovery plan."}`;
  $("recovery-actions").hidden = !plan;
  for (const id of [
    "recovery-execute",
    "recovery-reconcile",
    "recovery-resume",
    "recovery-undo",
    "recovery-cancel",
  ])
    $(id).disabled = true;
  let instruction = "";
  if (plan?.state === "prepared") {
    $("recovery-execute").disabled = false;
    $("recovery-cancel").disabled = false;
    instruction = `To restore, type RESTORE ${session.workspace}.`;
  }
  if (plan?.state === "armed") {
    $("recovery-reconcile").disabled = false;
    instruction =
      "The object restart must reconcile restored authority before any resume.";
  }
  if (plan?.state === "reconciled") {
    $("recovery-resume").disabled = false;
    if (plan.undoAvailable) $("recovery-undo").disabled = false;
    instruction = `To resume type RESUME ${session.workspace}. To undo type UNDO ${session.workspace}.`;
  }
  $("recovery-instruction").textContent = instruction;
}
async function refresh() {
  const [
    status,
    accounts,
    projects,
    receipts,
    billing,
    profiles,
    grants,
    readiness,
  ] = await Promise.all([
    invoke("workspace_status"),
    invoke("accounts_list"),
    invoke("projects_list"),
    invoke("receipts_list", { limit: 50 }),
    invoke("billing_status"),
    invoke("automation_inspect"),
    api("/api/grants"),
    api("/readiness.json"),
  ]);
  paused = status.publishingPaused;
  $("plan").textContent =
    status.plan === "advanced" ? "Advanced workspace" : "Free publishing";
  $("pause").textContent = paused ? "Resume publishing" : "Pause publishing";
  $("readiness").textContent =
    `Release ${readiness.release.slice(0, 12)} · ${readiness.access.signupMode} signup · provider OAuth ${Object.values(readiness.providers).filter((x) => x.oauth).length}/3 · Advanced ${readiness.payments.advancedEnabled ? "enabled" : "disabled"}`;
  try {
    oauthInfo = await api("/api/connections/oauth/status");
  } catch {
    oauthInfo = { providers: {}, connections: [] };
  }
  try {
    recovery = await api("/api/recovery/status");
  } catch {
    recovery = undefined;
  }
  renderOAuth();
  renderRecovery();
  records("accounts", accounts, (r, a) => {
    line(r, `${a.alias} · ${a.provider}`, true);
    line(
      r,
      `${a.identity.username} · stable ${a.identity.id} · ${a.active ? "Connected" : "Disconnected"}${a.capabilities?.oauth ? " · OAuth" : " · manual"}${a.capabilities?.refresh ? " · refresh" : ""}${a.capabilities?.readback ? " · readback" : ""}`,
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
  for (const id of ["project-select", "profile-project"])
    $(id).replaceChildren(...projects.map((p) => new Option(p.name, p.id)));
  for (const id of ["account-select", "profile-account"])
    $(id).replaceChildren(
      ...accounts
        .filter((a) => a.active)
        .map((a) => new Option(`${a.alias} · ${a.provider}`, a.alias)),
    );
  records("receipts", receipts, (r, d) => {
    line(r, `${d.provider} · ${d.account} · ${d.status}`, true);
    line(r, `${new Date(d.dueAt).toLocaleString()} · ${d.reason || ""}`);
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
      : "Purchases remain disabled pending settlement acceptance.";
  $("subscribe").disabled = !billing.methods.checkout.available;
  $("portal").disabled = !billing.methods.checkout.available;
  $("profile-configure").disabled = status.plan !== "advanced";
  records("profiles", profiles.profiles, (r, p) => {
    line(r, p.id + " · " + (p.enabled ? "Running" : "Paused"), true);
    line(r, p.repository + " · " + (p.error || p.family));
    button(
      r,
      "Preview",
      async () => show(await invoke("automation_preview", { id: p.id })),
      status.plan !== "advanced",
    );
    button(
      r,
      p.enabled ? "Pause" : "Enable",
      async () => {
        show(
          await invoke(p.enabled ? "automation_pause" : "automation_enable", {
            id: p.id,
            idempotencyKey: key(),
          }),
        );
        await refresh();
      },
      !p.enabled && status.plan !== "advanced",
    );
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
for (const b of $("oauth-buttons").querySelectorAll("button[data-provider]"))
  b.onclick = () =>
    action(async () => {
      const alias = new FormData($("oauth")).get("alias");
      if (!/^[A-Za-z0-9_-]{1,100}$/.test(alias))
        throw new Error("Choose an account alias first.");
      const started = await api(
        `/api/connections/oauth/${b.dataset.provider}/start`,
        { alias, returnPath: "/app" },
      );
      location.assign(started.authorizationUrl);
    });
for (const id of [
  "connection",
  "project",
  "campaign",
  "delivery",
  "grant",
  "profile",
  "recovery-prepare",
])
  $(id).onsubmit = (e) => {
    e.preventDefault();
    const form = e.currentTarget,
      fd = new FormData(form);
    action(async () => {
      if (id === "connection") {
        try {
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
          await refresh();
        } finally {
          form.elements.accessToken.value = "";
        }
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
      if (id === "profile") {
        show(
          await invoke("automation_configure", {
            id: fd.get("id"),
            project: fd.get("project"),
            repository: fd.get("repository"),
            branch: fd.get("branch"),
            path: fd.get("path"),
            templates: { [fd.get("alias")]: fd.get("template") },
            family: fd.get("family"),
            intervalMinutes: Number(fd.get("interval")),
            minSpacingMinutes: Number(fd.get("spacing")),
            idempotencyKey: key(),
          }),
        );
        await refresh();
      }
      if (id === "recovery-prepare") {
        const at = new Date(fd.get("at"));
        if (!Number.isFinite(at.getTime()))
          throw new Error("Choose a valid recovery target.");
        show(
          await api("/api/recovery/prepare", {
            at: at.toISOString(),
            reason: fd.get("reason"),
          }),
        );
        $("recovery-confirmation").value = "";
        await refresh();
      }
    });
  };
function currentPlan() {
  if (!recovery?.plan) throw new Error("No recovery plan is available.");
  return recovery.plan;
}
$("recovery-execute").onclick = () =>
  action(async () => {
    const p = currentPlan();
    show(
      await api("/api/recovery/execute", {
        id: p.id,
        digest: p.digest,
        execute: true,
        confirmation: $("recovery-confirmation").value,
      }),
    );
    await refresh();
  });
$("recovery-reconcile").onclick = () =>
  action(async () => {
    const p = currentPlan();
    show(
      await api("/api/recovery/reconcile", {
        id: p.id,
        digest: p.digest,
        reconcile: true,
      }),
    );
    await refresh();
  });
$("recovery-resume").onclick = () =>
  action(async () => {
    const p = currentPlan();
    show(
      await api("/api/recovery/resume", {
        id: p.id,
        digest: p.digest,
        resume: true,
        confirmation: $("recovery-confirmation").value,
      }),
    );
    await refresh();
  });
$("recovery-undo").onclick = () =>
  action(async () => {
    const p = currentPlan();
    show(
      await api("/api/recovery/undo", {
        id: p.id,
        digest: p.digest,
        undo: true,
        confirmation: $("recovery-confirmation").value,
      }),
    );
    await refresh();
  });
$("recovery-cancel").onclick = () =>
  action(async () => {
    const p = currentPlan();
    show(
      await api("/api/recovery/cancel", {
        id: p.id,
        digest: p.digest,
        cancel: true,
      }),
    );
    await refresh();
  });
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
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
$("subscribe").onclick = () =>
  action(async () => {
    const q = await invoke("billing_quote", {
      mode: "subscription",
      idempotencyKey: key(),
    });
    $("quote").hidden = false;
    $("quote").replaceChildren(
      document.createTextNode("$5.00 USD per month. Automatically renews. "),
    );
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
    : "Remote MCP and HTTP are available. Native WebMCP is not available in this browser.";
  await refresh();
  const params = new URL(location.href).searchParams;
  if (params.get("connected"))
    show(
      `${params.get("connected")} OAuth completed. Verify the stable account identity before publishing.`,
    );
  else if (params.get("connection") === "denied")
    show(
      "Provider authorization was declined. No connection was created.",
      true,
    );
  if (params.has("connected") || params.has("connection"))
    history.replaceState(null, "", "/app");
} catch (e) {
  $("session-notice").replaceChildren();
  const a = document.createElement("a");
  a.href = "/auth/login";
  a.textContent = "Sign in to open your workspace";
  $("session-notice").append(a);
  show(e.message, true);
}
