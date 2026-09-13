const $ = (id) => document.getElementById(id);
let session;

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
    ...(input === undefined ? {} : { body: JSON.stringify(input) }),
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(value.error?.message || "Request failed.");
    error.status = response.status;
    error.code = value.error?.code;
    throw error;
  }
  return value;
}

function renderControls() {
  const phrase = session ? `DELETE ${session.workspace}` : "";
  $("instruction").textContent = session
    ? `Type exactly: ${phrase}`
    : "Sign in before managing workspace data.";
  $("delete").disabled =
    !session ||
    !$("understand").checked ||
    $("confirmation").value !== phrase;
}

for (const id of ["confirmation", "understand"])
  $(id).addEventListener("input", renderControls);

$("delete-form").onsubmit = async (event) => {
  event.preventDefault();
  renderControls();
  if ($("delete").disabled) return;
  $("delete").disabled = true;
  try {
    const value = await api("/api/lifecycle/delete", {
      delete: true,
      confirmation: $("confirmation").value,
    });
    session = undefined;
    $("result").hidden = false;
    $("result").textContent = JSON.stringify(value, null, 2);
    $("status").textContent =
      "Workspace deletion completed. This browser session is no longer valid.";
    $("delete-form").hidden = true;
  } catch (error) {
    $("result").hidden = false;
    $("result").textContent = error.message || "Deletion did not complete.";
    let pending = false;
    try {
      const lifecycle = await api("/api/lifecycle/status");
      pending = lifecycle.deletion?.state === "pending";
    } catch {
      // Keep the original error visible if status cannot be refreshed.
    }
    $("status").textContent = pending
      ? "Deletion has started and this workspace is durably fenced. A provider write may still be settling or cleanup needs retry. Retry this same deletion; do not resume ordinary workspace activity."
      : "Deletion was not accepted and no pending deletion was observed. Existing workspace state has not been reported as deleted.";
    renderControls();
  }
};

try {
  session = await api("/api/session");
  const lifecycle = await api("/api/lifecycle/status");
  $("status").textContent = lifecycle.deletion
    ? lifecycle.deletion.state === "pending"
      ? "Deletion is pending and this workspace is durably fenced. Retry the same deletion to complete cleanup."
      : `Deletion state: ${lifecycle.deletion.state}.`
    : `Workspace ${session.workspace} is active.`;
  renderControls();
} catch (error) {
  session = undefined;
  $("status").replaceChildren();
  const link = document.createElement("a");
  link.href = "/auth/login?return=%2Fapp";
  link.textContent = "Sign in to manage your workspace";
  $("status").append(link);
  $("delete-form").hidden = true;
}


let retentionArchive;
$("archive-export").onclick = async () => {
  $("archive-prune").disabled = true;
  retentionArchive = undefined;
  try {
    const value = await api("/api/lifecycle/retention/export", {});
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "poststeward-retention-" + value.digest.slice(0, 16) + ".json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    retentionArchive = value;
    $("archive-result").hidden = false;
    $("archive-result").textContent = value.records.length + " eligible records. Save the download before confirming cleanup.";
  } catch (error) {
    $("archive-result").hidden = false;
    $("archive-result").textContent = error.message;
  }
  renderRetention();
};
function renderRetention() {
  $("archive-prune").disabled = !session || !retentionArchive?.records.length ||
    $("archive-confirmation").value !== "PRUNE " + session.workspace;
}
$("archive-confirmation").addEventListener("input", renderRetention);
$("archive-prune").onclick = async () => {
  if ($("archive-prune").disabled) return;
  $("archive-prune").disabled = true;
  try {
    const value = await api("/api/lifecycle/retention/prune", {
      cutoff: retentionArchive.cutoff, digest: retentionArchive.digest,
      confirmation: $("archive-confirmation").value,
    });
    $("archive-result").textContent = JSON.stringify(value, null, 2);
  } catch (error) {
    $("archive-result").textContent = error.message;
  }
  retentionArchive = undefined;
  renderRetention();
};
