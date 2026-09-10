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
    mode: "same-origin",
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(20000),
    headers: {
      "Content-Type": "application/json",
      ...(session?.csrf ? { "X-CSRF-Token": session.csrf } : {}),
    },
    ...(input !== undefined ? { body: JSON.stringify(input) } : {}),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "The response was incomplete. Refresh existing state before repeating a consequential action.",
    );
  }
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
  } catch (error) {
    show(error.message || "Request failed.", true);
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
  const element = document.createElement(strong ? "strong" : "span");
  element.textContent = value;
  row.append(element);
}
function button(row, label, fn) {
  const control = document.createElement("button");
  control.textContent = label;
  control.onclick = () => action(fn);
  row.append(control);
}
function trustedExternal(value, hosts) {
  try {
    const url = new URL(value);
    if (
      url.protocol === "https:" &&
      hosts.includes(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.port
    ) {
      return url.href;
    }
  } catch {
    /* Untrusted response data is not navigation authority. */
  }
}
function navigateExternal(value, hosts) {
  const target = trustedExternal(value, hosts);
  if (!target) throw new Error("The external destination was not on the expected provider host.");
  location.assign(target);
}

function renderOAuth() {
  const providers = oauthInfo?.providers || {};
  for (const control of $("oauth-buttons").querySelectorAll("button[data-provider]")) {
    const state = providers[control.dataset.provider];
    control.disabled = state?.available !== true;
    control.title = state?.available
      ? `Authorize ${control.dataset.provider} with the configured provider application.`
      : `${control.dataset.provider} provider application is not configured in this deployment.`;
  }
  const providerText = Object.entries(providers).map(([provider, state]) => {
    const ready = state.available ? "OAuth ready" : "provider app not configured";
    const readback =
      provider === "linkedin"
        ? state.readback
          ? ", member readback enabled"
          : ", member readback unavailable"
        : "";
    return `${provider}: ${ready}${readback}`;
  });
  const connectionText = (oauthInfo?.connections || []).map((item) => {
    const renewal = item.needsReauthorization
      ? "reauthorization required"
      : item.strategy?.replaceAll("_", " ") || "credential lifecycle unknown";
    return `${item.alias}: ${item.provider}, ${item.status}, ${renewal}`;
  });
  $("oauth-status").textContent =
    [...providerText, ...connectionText].join(" · ") ||
    "No provider applications are configured.";
}
async function refreshOAuth() {
  oauthInfo = await api("/api/connections/oauth/status");
  renderOAuth();
}

function renderRecovery() {
  if (!recovery) return;
  const { control = {}, effects = {}, plan = null } = recovery;
  $("recovery-status").textContent = control.quarantined
    ? `QUARANTINED: ${control.reason || "recovery is in progress"}`
    : "Normal operation. Recovery quarantine is not active.";
  $("recovery-plan").textContent = plan
    ? JSON.stringify(
        {
          id: plan.id,
          state: plan.state,
          targetTime: new Date(plan.targetTime).toISOString(),
          reason: plan.reason,
          digest: plan.digest,
          expiresAt: new Date(plan.expiresAt).toISOString(),
          undoAvailable: plan.undoAvailable,
          externalEffects: effects,
        },
        null,
        2,
      )
    : JSON.stringify({ plan: null, externalEffects: effects }, null, 2);
  const prepared = plan?.state === "prepared";
  const armed = plan?.state === "armed";
  const reconciled = plan?.state === "reconciled";
  $("recovery-cancel").disabled = !prepared;
  $("recovery-execute").disabled = !prepared || plan.expiresAt <= Date.now();
  $("recovery-reconcile").disabled = !armed;
  $("recovery-resume").disabled = !reconciled || !control.quarantined;
  $("recovery-undo").disabled = !reconciled || !plan.undoAvailable;
}
async function refreshRecovery() {
  recovery = await api("/api/recovery/status");
  renderRecovery();
  return recovery;
}
function recoveryAction(name, flag) {
  return action(async () => {
    const current = await refreshRecovery();
    const plan = current.plan;
    if (!plan) throw new Error("There is no recovery plan to act on.");
    if (name === "execute") {
      const target = new Date(plan.targetTime).toISOString();
      if (
        !window.confirm(
          `Restore this workspace to ${target}? Publication is quarantined and restored authority will be invalidated.`,
        )
      )
        return;
    }
    if (name === "undo") {
      if (
        !window.confirm(
          "Undo the reconciled restore using its exact recorded bookmark? This re-enters recovery quarantine.",
        )
      )
        return;
    }
    const result = await api(`/api/recovery/${name}`, {
      id: plan.id,
      digest: plan.digest,
      [flag]: true,
    });
    recovery = result.status || (await refreshRecovery());
    renderRecovery();
    show(result);
  });
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
  records("accounts", accounts, (row, account) => {
    line(row, `${account.alias} · ${account.provider}`, true);
    const capabilities = account.capabilities
      ? ` · ${account.capabilities.oauth ? "OAuth" : "manual"} · ${account.capabilities.refresh ? "refreshable" : "no refresh"} · ${account.capabilities.readback ? "readback" : "no independent readback"}`
      : " · legacy/manual connection";
    line(
      row,
      `${account.identity.username} · ${account.active ? "Connected" : "Disconnected"}${capabilities}`,
    );
    if (account.active)
      button(row, "Disconnect", async () => {
        show(
          await invoke("account_disconnect", {
            alias: account.alias,
            idempotencyKey: key(),
          }),
        );
        await refresh();
      });
  });
  records("projects", projects, (row, project) => {
    line(row, project.name, true);
    line(row, project.accounts.join(", "));
  });
  $("project-select").replaceChildren(
    ...projects.map((project) => new Option(project.name, project.id)),
  );
  $("account-select").replaceChildren(
    ...accounts
      .filter((account) => account.active)
      .map((account) => new Option(account.alias, account.alias)),
  );
  records("receipts", receipts, (row, delivery) => {
    line(row, `${delivery.provider} · ${delivery.account} · ${delivery.status}`, true);
    line(
      row,
      new Date(delivery.dueAt).toLocaleString() +
        " · " +
        (delivery.reason || "") +
        " ",
    );
    const copy = document.createElement("p");
    copy.textContent = delivery.text;
    row.append(copy);
    if (delivery.url) {
      const link = document.createElement("a");
      const safe = trustedExternal(delivery.url, [
        "x.com",
        "www.linkedin.com",
        "threads.net",
        "www.threads.net",
        "threads.com",
        "www.threads.com",
      ]);
      if (safe) {
        link.href = safe;
        link.textContent = "Provider post";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.append(link);
      }
    }
    if (["scheduled", "waiting_container"].includes(delivery.status))
      button(row, "Cancel", async () => {
        show(
          await invoke("schedule_cancel", {
            delivery: delivery.id,
            idempotencyKey: key(),
          }),
        );
        await refresh();
      });
    if (delivery.postId)
      button(row, "Capture metrics", async () =>
        show(
          await invoke("metrics_capture", {
            delivery: delivery.id,
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
      : "Purchases are disabled until external payment acceptance is complete.";
  $("subscribe").disabled = !billing.methods.checkout.available;
  $("portal").disabled = !billing.entitlement && !billing.attempt;
  records("profiles", profiles.profiles, (row, profile) => {
    line(row, profile.id + " · " + (profile.enabled ? "Running" : "Paused"), true);
    line(row, profile.repository + " · " + (profile.error || profile.family));
    button(row, profile.enabled ? "Pause" : "Enable", async () => {
      show(
        await invoke(profile.enabled ? "automation_pause" : "automation_enable", {
          id: profile.id,
          idempotencyKey: key(),
        }),
      );
      await refresh();
    });
  });
  records("grants", grants, (row, grant) => {
    line(row, grant.actor, true);
    line(row, (grant.revoked_at ? "Revoked · " : "") + grant.scopes);
    if (!grant.revoked_at)
      button(row, "Revoke", async () => {
        await api("/api/grants", { id: grant.id }, "DELETE");
        await refresh();
      });
  });
  const auxiliary = await Promise.allSettled([refreshOAuth(), refreshRecovery()]);
  for (const state of auxiliary)
    if (state.status === "rejected") show(state.reason?.message || "Owner status refresh failed.", true);
}

for (const control of $("oauth-buttons").querySelectorAll("button[data-provider]")) {
  control.onclick = () =>
    action(async () => {
      const form = $("oauth-connection");
      const alias = new FormData(form).get("alias");
      if (!/^[A-Za-z0-9_-]{1,100}$/.test(String(alias || "")))
        throw new Error("Choose an account alias before starting provider authorization.");
      const provider = control.dataset.provider;
      const started = await api(`/api/connections/oauth/${provider}/start`, { alias });
      const hosts = {
        x: ["x.com"],
        threads: ["threads.net", "www.threads.net"],
        linkedin: ["www.linkedin.com"],
      }[provider];
      navigateExternal(started.authorizationUrl, hosts || []);
    });
}

for (const id of ["connection", "project", "campaign", "delivery", "grant"])
  $(id).onsubmit = (event) => {
    event.preventDefault();
    const form = event.currentTarget,
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
          form.reset();
          await refresh();
        } finally {
          const field = form.elements.namedItem("accessToken");
          if (field) field.value = "";
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
              .map((value) => value.trim())
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
          .map(([alias, text]) => alias + "\n" + text)
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

$("recovery-prepare").onsubmit = (event) => {
  event.preventDefault();
  const fd = new FormData(event.currentTarget);
  action(async () => {
    const result = await api("/api/recovery/prepare", {
      at: fd.get("at"),
      reason: fd.get("reason"),
    });
    recovery = result.status;
    renderRecovery();
    show(result);
  });
};
$("recovery-refresh").onclick = () => action(refreshRecovery);
$("recovery-cancel").onclick = () => recoveryAction("cancel", "cancel");
$("recovery-execute").onclick = () => recoveryAction("execute", "execute");
$("recovery-reconcile").onclick = () => recoveryAction("reconcile", "reconcile");
$("recovery-resume").onclick = () => recoveryAction("resume", "resume");
$("recovery-undo").onclick = () => recoveryAction("undo", "undo");

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
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "publishing-workspace.json";
    anchor.click();
    URL.revokeObjectURL(url);
  });
$("subscribe").onclick = () =>
  action(async () => {
    const quote = await invoke("billing_quote", {
      mode: "subscription",
      idempotencyKey: key(),
    });
    $("quote").hidden = false;
    $("quote").textContent =
      "$5.00 USD per month. Automatically renews. Manage cancellation in Stripe. ";
    button($("quote"), "Continue to Stripe", async () => {
      const checkout = await invoke("billing_checkout", {
        quote: quote.id,
        idempotencyKey: key(),
      });
      navigateExternal(checkout.url, ["checkout.stripe.com"]);
    });
  });
$("portal").onclick = () =>
  action(async () => {
    const portal = await invoke("billing_portal", { idempotencyKey: key() });
    navigateExternal(portal.url, ["billing.stripe.com"]);
  });

try {
  session = await api("/api/session");
  $("session-notice").textContent = "Workspace " + session.workspace;
  const help = await api("/help.json");
  const registered = await registerWebMCP(help, invoke, session.scopes);
  $("webmcp-status").textContent = registered.available
    ? `${registered.count} native browser agent tools registered. Public release still requires an authenticated read-only execution in a supported browser.`
    : "Remote MCP and HTTP are available. This browser exposes no native WebMCP registration surface.";
  await refresh();
} catch (error) {
  $("session-notice").replaceChildren();
  const anchor = document.createElement("a");
  anchor.href = "/auth/login";
  anchor.textContent = "Sign in to open your workspace";
  $("session-notice").append(anchor);
  show(error.message, true);
}
