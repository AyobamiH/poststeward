import { renderOwnerSnapshot, showFeedback } from "./owner-ui.js";
import { registerWebMCP, checkNativeWebMCP } from "./webmcp.js";
import { oauthHosts, providerPostHosts, trustedExternal } from "./app-client.js";
const $ = (id) => document.getElementById(id);
let session, selectedCampaign, paused = false, oauthInfo, recovery;
const key = () => crypto.randomUUID();
async function api(path, input, method = input === undefined ? "GET" : "POST") {
  const response = await fetch(path, {
    method, credentials: "same-origin", mode: "same-origin", cache: "no-store", redirect: "manual",
    signal: AbortSignal.timeout(20000),
    headers: { "Content-Type": "application/json", ...(session?.csrf ? { "X-CSRF-Token": session.csrf } : {}) },
    ...(input !== undefined ? { body: JSON.stringify(input) } : {}),
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error("The response was incomplete. Inspect the existing receipt before retrying."); }
  if (!response.ok) {
    const error = new Error(data.error?.message || "Request failed.");
    error.code = data.error?.code; error.status = response.status; throw error;
  }
  return data;
}
const invoke = (name, input = {}) => api("/api/operations/" + name, input);
let feedbackTarget, actionInProgress = false;
function show(data, error = false) { showFeedback(data, error, feedbackTarget); }
async function action(fn) {
  if (actionInProgress) return;
  actionInProgress = true;
  feedbackTarget = document.activeElement?.closest("form, .work-pane, .work-zone") || undefined;
  feedbackTarget?.setAttribute("aria-busy", "true");
  try { await fn(); } catch (e) { show(e.message, true); }
  finally { feedbackTarget?.removeAttribute("aria-busy"); feedbackTarget = undefined; actionInProgress = false; }
}
function records(id, items, render) {
  const root = $(id); root.replaceChildren();
  if (!items.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "Nothing here yet."; root.append(p); }
  for (const item of items) { const row = document.createElement("div"); row.className = "record"; render(row, item); root.append(row); }
}
function line(row, value, strong = false) {
  const e = document.createElement(strong ? "strong" : "span"); e.textContent = value; row.append(e);
}
function button(row, label, fn, disabled = false) {
  const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.disabled = disabled; b.onclick = () => action(fn); row.append(b);
}
function navigateExternal(value, hosts) {
  const destination = trustedExternal(value, hosts);
  if (!destination) throw new Error("The external destination was not on the expected provider host.");
  location.assign(destination);
}
function renderOAuth() {
  for (const b of $("oauth-buttons").querySelectorAll("button[data-provider]")) b.disabled = oauthInfo?.providers?.[b.dataset.provider]?.available !== true;
  $("oauth-status").textContent = oauthInfo ? "Provider status loaded." : "Provider status unavailable. No connection capability is inferred.";
}
function renderRecovery() {
  const plan = recovery?.plan;
  $("recovery-status").textContent = recovery ? "Recovery status loaded." : "Recovery status unavailable.";
  $("recovery-actions").hidden = !plan;
  for (const id of ["recovery-execute", "recovery-reconcile", "recovery-resume", "recovery-undo", "recovery-cancel"]) $(id).disabled = true;
  let instruction = "";
  if (plan?.state === "prepared") {
    $("recovery-execute").disabled = false; $("recovery-cancel").disabled = false;
    instruction = `To restore, type RESTORE ${session.workspace}.`;
  }
  if (plan?.state === "armed") {
    $("recovery-reconcile").disabled = false;
    instruction = "The object restart must reconcile restored authority before any resume.";
  }
  if (plan?.state === "reconciled") {
    $("recovery-resume").disabled = false;
    if (plan.undoAvailable) $("recovery-undo").disabled = false;
    instruction = `To resume type RESUME ${session.workspace}. To undo type UNDO ${session.workspace}.`;
  }
  $("recovery-instruction").textContent = instruction;
}
async function refresh() {
  const [status, accounts, projects, receipts, billing, profiles, grants, readiness] = await Promise.all([
    invoke("workspace_status"), invoke("accounts_list"), invoke("projects_list"), invoke("receipts_list", { limit: 50 }),
    invoke("billing_status"), invoke("automation_inspect"), api("/api/grants"), api("/readiness.json"),
  ]);
  paused = status.publishingPaused;
  $("plan").textContent = status.plan === "advanced" ? "Advanced workspace" : "Free publishing";
  $("pause").textContent = paused ? "Resume publishing" : "Pause publishing";
  $("readiness").textContent = `Release ${readiness.release.slice(0, 12)} · ${readiness.access.signupMode} signup · provider OAuth ${Object.values(readiness.providers).filter((x) => x.oauth).length}/3 · Advanced ${readiness.payments.advancedEnabled ? "enabled" : "disabled"}`;
  try { oauthInfo = await api("/api/connections/oauth/status"); } catch { oauthInfo = undefined; }
  try { recovery = await api("/api/recovery/status"); } catch { recovery = undefined; }
  renderOAuth(); renderRecovery();
  records("accounts", accounts, (r, a) => {
    line(r, `${a.alias} · ${a.provider}`, true);
    line(r, `${a.identity.username} · stable ${a.identity.id} · ${a.active ? "Connected" : "Disconnected"}${a.capabilities?.oauth ? " · OAuth" : " · manual"}${a.capabilities?.refresh ? " · refresh" : ""}${a.capabilities?.readback ? " · readback" : ""}`);
    if (a.active) button(r, "Disconnect", async () => { show(await invoke("account_disconnect", { alias: a.alias, idempotencyKey: key() })); await refresh(); });
  });
  records("projects", projects, (r, p) => { line(r, p.name, true); line(r, p.accounts.join(", ")); });
  for (const id of ["project-select", "profile-project"]) $(id).replaceChildren(...projects.map((p) => new Option(p.name, p.id)));
  for (const id of ["account-select", "profile-account"]) $(id).replaceChildren(...accounts.filter((a) => a.active).map((a) => new Option(`${a.alias} · ${a.provider}`, a.alias)));
  records("receipts", receipts, (r, d) => {
    line(r, `${d.provider} · ${d.account} · ${d.status}`, true);
    line(r, `${new Date(d.dueAt).toLocaleString()} · ${d.reason || ""}`);
    const copy = document.createElement("p"); copy.textContent = d.text; r.append(copy);
    const providerUrl = d.url ? trustedExternal(d.url, providerPostHosts(d.provider)) : undefined;
    if (providerUrl) { const link = document.createElement("a"); link.href = providerUrl; link.textContent = "Provider post"; link.target = "_blank"; link.rel = "noopener noreferrer"; r.append(link); }
    if (["scheduled", "waiting_container"].includes(d.status)) button(r, "Cancel", async () => { show(await invoke("schedule_cancel", { delivery: d.id, idempotencyKey: key() })); await refresh(); });
    if (d.postId && d.status === "published_unverified") button(r, "Read back existing post", async () => { show(await invoke("receipt_recheck", { delivery: d.id, idempotencyKey: key() })); await refresh(); });
    if (d.postId) button(r, "Capture metrics", async () => show(await invoke("metrics_capture", { delivery: d.id, idempotencyKey: key() })));
  });
  const billingRoot = $("billing-status"); billingRoot.replaceChildren();
  if (billing.sandbox) line(billingRoot, "Stripe sandbox only. Test payments do not enable Advanced automation.");
  const entitlement = billing.entitlement;
  const covered = !!entitlement && !entitlement.revoked && entitlement.until > Date.now();
  const paymentState = billing.attempt?.status;
  const summary = document.createElement("p");
  summary.textContent = billing.recoveryPending
    ? "Billing recovery is pending. Refresh to reconcile existing Stripe records before another purchase."
    : entitlement?.revoked ? "Access revoked. Manage the existing subscription before purchasing again."
    : covered ? (billing.sandbox ? "Confirmed test access until " : "Confirmed access until ") + new Date(entitlement.until).toLocaleString()
    : paymentState === "pending" ? "Payment pending verification. Do not start another purchase."
    : paymentState === "failed" ? "Payment was not completed. Inspect the existing payment before retrying."
    : entitlement ? "The confirmed access period has ended."
    : paymentState === "paid" ? "Payment recorded. No current access period is confirmed."
    : billing.methods.checkout.available ? "No payment recorded. Subscription checkout is available."
    : "Purchases remain disabled pending settlement acceptance.";
  billingRoot.append(summary);
  if (billing.attempt || entitlement || billing.recoveryPending) {
    const receipt = {
      observedAt: new Date().toISOString(), sandbox: billing.sandbox, paymentStatus: paymentState || null,
      recoveryPending: !!billing.recoveryPending, quoteId: billing.attempt?.quote || null,
      checkoutSessionId: billing.attempt?.session || null, accessUntil: entitlement?.until || null,
      revoked: !!entitlement?.revoked, price: billing.price,
      webhookEvidence: billing.webhookEvidence || { available: false, events: [] },
      evidenceBoundary: "Payment state may be reconciled by refresh. Webhook events are a separate persisted ledger captured before that reconciliation; only non-null completedAt records completed processing. Latest 20 events only; replay is not established by this receipt.",
    };
    const details = document.createElement("details"), title = document.createElement("summary"), pre = document.createElement("pre");
    title.textContent = "Inspect billing receipt"; pre.textContent = JSON.stringify(receipt, null, 2);
    details.append(title, pre); billingRoot.append(details);
  }
  $("subscribe").disabled = !billing.methods.checkout.available || covered || paymentState === "pending";
  $("portal").disabled = !billing.portalAvailable;
  $("profile-configure").disabled = status.plan !== "advanced";
  records("profiles", profiles.profiles, (r, p) => {
    line(r, p.id + " · " + (p.enabled ? "Running" : "Paused"), true); line(r, p.repository + " · " + (p.error || p.family));
    button(r, "Preview", async () => show(await invoke("automation_preview", { id: p.id })), status.plan !== "advanced");
    button(r, p.enabled ? "Pause" : "Enable", async () => {
      show(await invoke(p.enabled ? "automation_pause" : "automation_enable", { id: p.id, idempotencyKey: key() })); await refresh();
    }, !p.enabled && status.plan !== "advanced");
  });
  records("grants", grants, (r, g) => {
    line(r, g.actor, true); line(r, (g.revoked_at ? "Revoked · " : "") + g.scopes);
    if (!g.revoked_at) button(r, "Revoke", async () => { await api("/api/grants", { id: g.id }, "DELETE"); await refresh(); });
  });
  renderOwnerSnapshot({ accounts, receipts, profiles, grants, oauthInfo, recovery });
}
for (const b of $("oauth-buttons").querySelectorAll("button[data-provider]")) b.onclick = () => action(async () => {
  const form = new FormData($("oauth"));
  const alias = form.get("alias");
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(alias)) throw new Error("Choose an account alias first.");
  const provider = b.dataset.provider;
  const actorUrn = String(form.get("actorUrn") || "").trim();
  const started = await api(`/api/connections/oauth/${provider}/start`, {
    alias,
    returnPath: "/app",
    ...(provider === "linkedin" && actorUrn ? { actorUrn } : {}),
  });
  navigateExternal(started.authorizationUrl, oauthHosts(provider));
});
for (const id of ["connection", "project", "campaign", "delivery", "grant", "profile", "recovery-prepare"]) $(id).onsubmit = (e) => {
  e.preventDefault(); const form = e.currentTarget, fd = new FormData(form);
  action(async () => {
    if (id === "connection") {
      try {
        const input = {
          alias: fd.get("alias"), provider: fd.get("provider"), accessToken: fd.get("accessToken"),
          ...(fd.get("expiry") ? { expiresAt: new Date(fd.get("expiry")).getTime() } : {}),
          ...(fd.has("funding") ? { funding: "customer_app" } : {}),
        };
        show(await api("/api/connections/import", input)); await refresh();
      } finally { form.elements.accessToken.value = ""; }
    }
    if (id === "project") {
      show(await invoke("project_put", { id: fd.get("id"), name: fd.get("name"), accounts: fd.get("accounts").split(",").map((x) => x.trim()).filter(Boolean), idempotencyKey: key() })); await refresh();
    }
    if (id === "campaign") {
      selectedCampaign = await invoke("campaign_create", { project: fd.get("project"), text: { [fd.get("alias")]: fd.get("text") }, idempotencyKey: key() });
      await invoke("campaign_validate", { campaign: selectedCampaign.id });
      $("campaign-preview").hidden = false;
      $("campaign-preview").textContent = Object.entries(selectedCampaign.text).map(([a, t]) => a + "\n" + t).join("\n\n");
      $("delivery").hidden = false; show("Campaign stored and validated. Review the exact copy before submitting.");
    }
    if (id === "delivery") {
      const at = fd.get("at");
      show(await invoke(at ? "schedule_create" : "publish_now", { campaign: selectedCampaign.id, idempotencyKey: key(), ...(at ? { at, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone } : {}) })); await refresh();
    }
    if (id === "grant") {
      const grant = await api("/api/grants", { scopes: fd.getAll("scope"), hours: Number(fd.get("hours")) });
      $("token").hidden = false; $("token").textContent = grant.token; await refresh();
    }
    if (id === "profile") {
      show(await invoke("automation_configure", { id: fd.get("id"), project: fd.get("project"), repository: fd.get("repository"), branch: fd.get("branch"), path: fd.get("path"), templates: { [fd.get("alias")]: fd.get("template") }, family: fd.get("family"), intervalMinutes: Number(fd.get("interval")), minSpacingMinutes: Number(fd.get("spacing")), idempotencyKey: key() })); await refresh();
    }
    if (id === "recovery-prepare") {
      const at = new Date(fd.get("at")); if (!Number.isFinite(at.getTime())) throw new Error("Choose a valid recovery target.");
      show(await api("/api/recovery/prepare", { at: at.toISOString(), reason: fd.get("reason") }));
      $("recovery-confirmation").value = ""; await refresh();
    }
  });
};
function currentPlan() { if (!recovery?.plan) throw new Error("No recovery plan is available."); return recovery.plan; }
$("recovery-execute").onclick = () => action(async () => {
  const p = currentPlan(); show(await api("/api/recovery/execute", { id: p.id, digest: p.digest, execute: true, confirmation: $("recovery-confirmation").value })); await refresh();
});
$("recovery-reconcile").onclick = () => action(async () => {
  const p = currentPlan(); show(await api("/api/recovery/reconcile", { id: p.id, digest: p.digest, reconcile: true })); await refresh();
});
$("recovery-resume").onclick = () => action(async () => {
  const p = currentPlan(); show(await api("/api/recovery/resume", { id: p.id, digest: p.digest, resume: true, confirmation: $("recovery-confirmation").value })); await refresh();
});
$("recovery-undo").onclick = () => action(async () => {
  const p = currentPlan(); show(await api("/api/recovery/undo", { id: p.id, digest: p.digest, undo: true, confirmation: $("recovery-confirmation").value })); await refresh();
});
$("recovery-cancel").onclick = () => action(async () => {
  const p = currentPlan(); show(await api("/api/recovery/cancel", { id: p.id, digest: p.digest, cancel: true })); await refresh();
});
$("refresh").onclick = () => action(refresh);
$("pause").onclick = () => action(async () => { show(await invoke("publishing_pause", { paused: !paused, idempotencyKey: key() })); await refresh(); });
$("signout").onclick = () => action(async () => { await api("/auth/logout", {}); location.assign("/"); });
$("export").onclick = () => action(async () => {
  const data = await invoke("workspace_export");
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a"); a.href = url; a.download = "publishing-workspace.json"; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("subscribe").onclick = () => action(async () => {
  const q = await invoke("billing_quote", { mode: "subscription", idempotencyKey: key() });
  $("quote").hidden = false; $("quote").replaceChildren(document.createTextNode("$5.00 USD per month. Automatically renews. "));
  button($("quote"), "Continue to Stripe", async () => { const checkout = await invoke("billing_checkout", { quote: q.id, idempotencyKey: key() }); navigateExternal(checkout.url, ["checkout.stripe.com"]); });
});
$("portal").onclick = () => action(async () => { const p = await invoke("billing_portal", { idempotencyKey: key() }); navigateExternal(p.url, ["billing.stripe.com"]); });
$("webmcp-check").onclick = () => action(async () => {
  $("webmcp-observation").hidden = true;
  const observation = await checkNativeWebMCP(session.workspace);
  $("webmcp-observation").textContent = JSON.stringify(observation, null, 2); $("webmcp-observation").hidden = false;
});
$("webmcp-status").textContent = document.modelContext?.registerTool ? "This browser exposes WebMCP. Sign in to register and check workspace tools." : "Native WebMCP is not available in this browser.";
try {
  session = await api("/api/session");
  $("session-notice").textContent = "Workspace " + session.workspace;
  const help = await api("/help.json");
  try {
    const registered = await registerWebMCP(help, invoke, session.scopes);
    $("webmcp-status").textContent = registered.available ? `${registered.count} browser agent tools registered. Live execution has not yet been checked.` : "Remote MCP and HTTP are available. Native WebMCP is not available in this browser.";
    $("webmcp-check").disabled = !registered.available;
  } catch { $("webmcp-status").textContent = "Native WebMCP registration failed. Workspace controls remain available."; }
  await refresh();
  const params = new URL(location.href).searchParams;
  if (params.get("connected")) show(`${params.get("connected")} OAuth completed. Verify the stable account identity before publishing.`);
  else if (params.get("connection") === "denied") show("Provider authorization was declined. No connection was created.", true);
  if (params.has("connected") || params.has("connection")) history.replaceState(null, "", "/app");
} catch (e) {
  $("session-notice").replaceChildren();
  if (!session || e.status === 401) {
    const a = document.createElement("a"); a.href = "/auth/login"; a.textContent = "Sign in to open your workspace"; $("session-notice").append(a);
  } else $("session-notice").textContent = "Signed in, but workspace data could not be loaded. Refresh to inspect current state.";
  show(e.message, true);
}
