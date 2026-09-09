import { confirmation, mayApprove, mayReadback, receiptLabel, safePostLink, createClient } from "./pilot-client.js";
const $ = (id) => document.getElementById(id);
let session, snapshot, oauthInfo, busy = false, dirty = false, timer, polls = 0;
const api = createClient((...args) => fetch(...args), () => session?.csrf);
const operation = (name, input = {}) => api("/api/operations/" + name, input);
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
}
function controls() {
  for (const form of [$("connection-form"), $("oauth-form"), $("prepare-form")])
    for (const element of form.elements) {
      if (element.dataset?.provider) {
        const available = oauthInfo?.providers?.[element.dataset.provider]?.available === true;
        element.disabled = busy || !session || Boolean(snapshot?.record?.deliveryId) || !available;
      } else {
        element.disabled = busy || !session || Boolean(snapshot?.record?.deliveryId);
      }
    }
  $("confirm").disabled = busy || !session || dirty || !mayApprove(snapshot, $("approve").checked);
  $("approve").disabled = busy || dirty || Boolean(snapshot?.record?.deliveryId);
  $("cancel").disabled = busy || !["scheduled", "waiting_container"].includes(snapshot?.delivery?.status);
  $("recheck").disabled = busy || !mayReadback(snapshot);
  $("refresh").disabled = busy;
  $("export-receipt").disabled = busy || !snapshot?.record;
  $("signout").disabled = busy;
}
function renderOAuth() {
  if (!oauthInfo) return;
  const labels = Object.entries(oauthInfo.providers || {}).map(([provider, value]) => {
    const capability = value.available ? "ready" : "provider app not configured";
    const readback = provider === "linkedin" ? (value.readback ? ", member readback enabled" : ", member readback not approved") : "";
    return `${provider}: ${capability}${readback}`;
  });
  const connections = (oauthInfo.connections || []).map((item) => {
    const refresh = item.needsReauthorization ? "reauthorization required" : item.strategy.replaceAll("_", " ");
    return `${item.alias}: ${item.provider}, ${item.status}, ${refresh}`;
  });
  $("oauth-status").textContent = [...labels, ...connections].join(" · ") || "No provider applications are configured.";
}
function render() {
  const r = snapshot?.record, d = snapshot?.delivery;
  $("workbench").hidden = !session || !snapshot?.owner;
  $("signout").hidden = !session;
  if (snapshot?.owner) {
    const o = snapshot.owner;
    $("owner-status").textContent = `Google sign-in completed at ${new Date(o.authenticatedAt).toISOString()}. Proof ${o.id}. Workspace ${o.workspace}.`;
    $("signin").textContent = "Sign in again for a fresh approval";
  }
  $("review-panel").hidden = !r;
  if (r) {
    const readback = r.account.capabilities?.readback === true ? " · independent readback" : "";
    $("destination").textContent = `${r.account.provider} · ${r.account.alias} · @${r.account.identity.username} · stable ID ${r.account.identity.id} · binding ${r.account.version}${readback}`;
    $("exact-copy").textContent = r.text;
    $("review-meta").textContent = `Review ${r.id}. Expires ${new Date(r.expiresAt).toISOString()}. Runtime ${r.release}. Exact-copy SHA-256 ${r.textDigest}.`;
    $("confirm-form").hidden = Boolean(r.deliveryId);
  }
  $("receipt-status").textContent = receiptLabel(snapshot) + (d ? ` Delivery ${d.id}. Due ${new Date(d.dueAt).toISOString()}.${d.reason ? " Reason: " + d.reason : ""}` : "");
  $("receipt").textContent = snapshot ? JSON.stringify(snapshot, null, 2) : "No receipt yet.";
  $("post-link").replaceChildren();
  const href = safePostLink(r?.firstVerified?.url || d?.url);
  if (href) {
    const link = document.createElement("a"); link.href = href; link.textContent = "Open the recorded provider post";
    link.target = "_blank"; link.rel = "noopener noreferrer"; $("post-link").append(link);
  }
  renderOAuth();
  controls();
}
async function loadReceipt() {
  const previous = snapshot?.record?.id;
  snapshot = await api("/api/pilot/status");
  if (previous !== snapshot.record?.id) { $("approve").checked = false; dirty = false; }
  render();
}
async function loadOAuth() {
  oauthInfo = await api("/api/connections/oauth/status");
  renderOAuth();
  controls();
}
async function loadAccounts() {
  const accounts = await operation("accounts_list");
  const chosen = $("account").value;
  const eligible = accounts.filter((a) =>
    a.active &&
    (["x", "threads"].includes(a.provider) ||
      (a.provider === "linkedin" && a.capabilities?.readback === true)),
  );
  $("account").replaceChildren(
    new Option("Choose a connected account", ""),
    ...eligible.map((a) =>
      new Option(
        `${a.alias} · ${a.provider} · @${a.identity.username} · ${a.identity.id}${a.capabilities?.oauth ? " · OAuth" : ""}`,
        a.alias,
      ),
    ),
  );
  if ([...$("account").options].some((o) => o.value === chosen)) $("account").value = chosen;
}
async function act(fn) {
  if (busy) return;
  busy = true; controls();
  try { await fn(); }
  catch (error) {
    $("approve").checked = false;
    notice(error.message || "Request interrupted. Refresh the receipt; do not create a second publication.", true);
    if (error.status === 401 || error.code === "OWNER_SIGNIN_REQUIRED") {
      session = undefined; snapshot = undefined;
      $("owner-status").textContent = "A current Google owner sign-in is required. No new approval has been recorded here.";
    } else if (session) {
      // Read-only recovery; never automatically retry confirmation, OAuth start or token import.
      try { await loadReceipt(); } catch { /* Preserve the original error. */ }
    }
  } finally { busy = false; render(); }
}
function pending() {
  if (!snapshot?.delivery || snapshot.completed) return false;
  return ["scheduled", "executing", "waiting_container"].includes(snapshot.delivery.status) ||
    (snapshot.delivery.postId && snapshot.record.readbackAttempts < 8);
}
function poll() {
  clearTimeout(timer);
  if (!session || !pending() || polls >= 72) return;
  timer = setTimeout(async () => {
    if (document.hidden || busy) { poll(); return; }
    polls++;
    await act(async () => {
      await loadReceipt();
      if (!snapshot.completed && mayReadback(snapshot)) {
        snapshot = await api("/api/pilot/recheck", {}); render();
      }
    });
    poll();
  }, 5000);
}
for (const button of $("oauth-buttons").querySelectorAll("button[data-provider]")) {
  button.onclick = () => act(async () => {
    const alias = $("oauth-alias").value;
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(alias)) throw new Error("Choose an account alias before starting provider authorization.");
    const provider = button.dataset.provider;
    const started = await api(`/api/connections/oauth/${provider}/start`, { alias });
    notice(`Opening ${provider} authorization. No publication has been approved.`);
    location.assign(started.authorizationUrl);
  });
}
$("connection-form").onsubmit = (event) => {
  event.preventDefault();
  act(async () => {
    try {
      const provider = $("connection-provider").value;
      if (provider === "x" && !$("connection-funding").checked) throw new Error("Confirm ownership and API usage of your funded X application first.");
      await api("/api/connections/import", { alias: $("connection-alias").value, provider,
        accessToken: $("connection-token").value, ...(provider === "x" ? { funding: "customer_app" } : {}) });
      dirty = true; $("approve").checked = false;
      await loadAccounts(); await loadOAuth(); await loadReceipt();
      notice("Account identity verified. Manual import does not claim automatic refresh. Choose an eligible destination and prepare the exact review; nothing has posted.");
    } finally { $("connection-token").value = ""; }
  });
};
$("prepare-form").onsubmit = (event) => {
  event.preventDefault();
  act(async () => {
    snapshot = await api("/api/pilot/prepare", { alias: $("account").value, text: $("copy").value });
    dirty = false; $("approve").checked = false; render();
    notice("Review prepared without a provider write. Check the stable account ID and every word, then explicitly approve.");
    $("review-heading").scrollIntoView({ block: "center", behavior: "smooth" });
  });
};
$("confirm-form").onsubmit = (event) => {
  event.preventDefault();
  if (!(!dirty && mayApprove(snapshot, $("approve").checked))) return;
  const input = confirmation(snapshot.record);
  act(async () => {
    snapshot = await api("/api/pilot/confirm", input); $("approve").checked = false; render();
    notice("One durable delivery reserved. The thirty-second cancellation window has started. Do not create another publication as a retry.");
    polls = 0; poll();
  });
};
$("approve").onchange = controls;
for (const id of ["account", "copy"]) $(id).oninput = () => {
  dirty = true; $("approve").checked = false; controls();
  if (snapshot?.record && !snapshot.record.deliveryId) notice("Inputs changed. Prepare a new review; the displayed exact review has not changed.");
};
$("refresh").onclick = () => act(async () => { await loadOAuth(); await loadAccounts(); await loadReceipt(); polls = 0; poll(); });
$("cancel").onclick = () => act(async () => {
  snapshot = await api("/api/pilot/cancel", {}); render();
  notice(snapshot.cancellation?.cancelled ? "Cancellation won before dispatch. No replacement will be created." : "Cancellation did not win. Inspect the existing delivery; do not assume publication was stopped.");
});
$("recheck").onclick = () => act(async () => { snapshot = await api("/api/pilot/recheck", {}); render(); polls = 0; poll(); });
$("signout").onclick = () => act(async () => { await api("/auth/logout", {}); session = undefined; snapshot = undefined; location.assign("/"); });
$("export-receipt").onclick = () => {
  if (!snapshot?.record) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = "poststeward-acceptance-" + snapshot.record.id + ".json";
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
setInterval(controls, 1000);
await act(async () => {
  session = await api("/api/session");
  await loadReceipt(); await loadOAuth(); await loadAccounts();
  const params = new URL(location.href).searchParams;
  const connected = params.get("connected");
  const denied = params.get("connection");
  if (connected) notice(`${connected} authorization completed and the provider identity was verified. Review the destination before preparing content.`);
  else if (denied === "denied") notice("Provider authorization was declined. No connection or publication was created.", true);
  else notice(snapshot.completed ? "The recorded controlled publication has independent provider readback evidence." : "Owner sign-in verified. Choose the destination and review the exact publication before approval.");
  if (params.has("connected") || params.has("connection")) history.replaceState(null, "", "/pilot");
  poll();
});
