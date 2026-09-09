import { confirmation, mayApprove, mayReadback, receiptLabel, safePostLink, createClient } from "./pilot-client.js";
const $ = (id) => document.getElementById(id);
let session, snapshot, busy = false, timer, polls = 0;
const api = createClient((...args) => fetch(...args), () => session?.csrf);
const operation = (name, input = {}) => api("/api/operations/" + name, input);
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
}
function controls() {
  for (const form of [$("connection-form"), $("prepare-form")])
    for (const element of form.elements) element.disabled = busy || !session || Boolean(snapshot?.record?.deliveryId);
  $("confirm").disabled = busy || !session || !mayApprove(snapshot, $("approve").checked);
  $("approve").disabled = busy || Boolean(snapshot?.record?.deliveryId);
  $("cancel").disabled = busy || !["scheduled", "waiting_container"].includes(snapshot?.delivery?.status);
  $("recheck").disabled = busy || !mayReadback(snapshot);
  $("refresh").disabled = busy;
  $("export-receipt").disabled = busy || !snapshot?.record;
  $("signout").disabled = busy;
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
    $("destination").textContent = `${r.account.provider} · ${r.account.alias} · @${r.account.identity.username} · stable ID ${r.account.identity.id} · binding ${r.account.version}`;
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
  controls();
}
async function loadReceipt() {
  const previous = snapshot?.record?.id;
  snapshot = await api("/api/pilot/status");
  if (previous !== snapshot.record?.id) $("approve").checked = false;
  render();
}
async function loadAccounts() {
  const accounts = await operation("accounts_list");
  const chosen = $("account").value;
  $("account").replaceChildren(new Option("Choose a connected account", ""), ...accounts.filter((a) => a.active && ["x", "threads"].includes(a.provider)).map((a) => new Option(`${a.alias} · ${a.provider} · @${a.identity.username} · ${a.identity.id}`, a.alias)));
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
      // Read-only recovery; never automatically retry a confirmation or import.
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
$("connection-form").onsubmit = (event) => {
  event.preventDefault();
  act(async () => {
    const provider = $("connection-provider").value;
    if (provider === "x" && !$("connection-funding").checked) throw new Error("Confirm ownership and API usage of your funded X application first.");
    try {
      await api("/api/connections/import", { alias: $("connection-alias").value, provider,
        accessToken: $("connection-token").value, ...(provider === "x" ? { funding: "customer_app" } : {}) });
      await loadAccounts(); await loadReceipt(); notice("Account identity verified. Choose it explicitly and prepare the exact review; nothing has posted.");
    } finally { $("connection-token").value = ""; }
  });
};
$("prepare-form").onsubmit = (event) => {
  event.preventDefault();
  act(async () => {
    snapshot = await api("/api/pilot/prepare", { alias: $("account").value, text: $("copy").value });
    $("approve").checked = false; render();
    notice("Review prepared without a provider write. Check the stable account ID and every word, then explicitly approve.");
    $("review-heading").scrollIntoView({ block: "center", behavior: "smooth" });
  });
};
$("confirm-form").onsubmit = (event) => {
  event.preventDefault();
  if (!mayApprove(snapshot, $("approve").checked)) return;
  const input = confirmation(snapshot.record);
  act(async () => {
    snapshot = await api("/api/pilot/confirm", input); $("approve").checked = false; render();
    notice("One durable delivery reserved. The thirty-second cancellation window has started. Do not create another publication as a retry.");
    polls = 0; poll();
  });
};
$("approve").onchange = controls;
for (const id of ["account", "copy"]) $(id).oninput = () => {
  $("approve").checked = false; controls();
  if (snapshot?.record && !snapshot.record.deliveryId) notice("Inputs changed. Prepare a new review; the displayed exact review has not changed.");
};
$("refresh").onclick = () => act(async () => { await loadAccounts(); await loadReceipt(); polls = 0; poll(); });
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
  await loadReceipt(); await loadAccounts();
  notice(snapshot.completed ? "The recorded controlled publication has independent provider readback evidence." : "Owner sign-in verified. Choose the destination and review the exact publication before approval.");
  poll();
});
