import { trustedExternal } from "./app-client.js";

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

function sourceMessage(message, error = false) {
  const node = $("github-source-status");
  node.textContent = message;
  node.classList.toggle("error", error);
}

function line(row, value, strong = false) {
  const node = document.createElement(strong ? "strong" : "span");
  node.textContent = value;
  row.append(node);
}

function installButton(row, label, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", () => action(handler));
  row.append(button);
}

async function action(handler) {
  try {
    await handler();
  } catch (error) {
    sourceMessage(error instanceof Error ? error.message : "Request failed.", true);
  }
}

function render(status) {
  const config = status.configuration || {};
  const installations = Array.isArray(status.installations)
    ? status.installations
    : [];
  const repositories = Array.isArray(status.repositories)
    ? status.repositories
    : [];
  const connect = $("github-source-connect");
  connect.disabled = config.available !== true;
  sourceMessage(
    config.available
      ? `${repositories.length} selected repositor${repositories.length === 1 ? "y" : "ies"} linked. Private reads are owner-authorised and revalidated before every source check.`
      : "Private GitHub sources are not configured for this deployment. Public repositories continue to use anonymous read-only checks.",
  );

  const list = $("github-sources");
  list.replaceChildren();
  if (!installations.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No private GitHub installation is linked to this workspace.";
    list.append(empty);
  }
  for (const installation of installations) {
    const row = document.createElement("div");
    row.className = "record";
    line(
      row,
      `${installation.account_login} · installation ${installation.installation_id}`,
      true,
    );
    line(
      row,
      `${installation.status}${installation.last_error ? ` · ${installation.last_error}` : ""} · selected repositories only`,
    );
    installButton(row, "Unlink", async () => {
      await api("/api/sources/github/unlink", {
        installationId: installation.installation_id,
      });
      await refreshGitHubSources();
    });
    list.append(row);
  }

  const privateRepositories = repositories.filter((repository) => repository.private);
  $("github-probe-repository").replaceChildren(
    ...privateRepositories.map((repository) => {
      const option = document.createElement("option");
      option.value = repository.full_name;
      option.textContent = repository.full_name;
      return option;
    }),
  );
  $("github-probe-submit").disabled = !config.available || !privateRepositories.length;
  const datalist = $("github-repositories");
  datalist.replaceChildren(
    ...repositories.map((repository) => {
      const option = document.createElement("option");
      option.value = repository.full_name;
      option.label = repository.private ? "Private · linked" : "Linked";
      return option;
    }),
  );
}

async function refreshGitHubSources() {
  render(await api("/api/sources/github/status"));
}

$("github-source-probe").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const result = $("github-probe-result");
    result.hidden = true;
    result.textContent = "";
    $("github-probe-submit").disabled = true;
    try {
      const input = Object.fromEntries(new FormData(event.currentTarget));
      const observation = await api("/api/sources/github/probe", input);
      result.textContent = JSON.stringify(observation, null, 2);
      result.hidden = false;
      sourceMessage("Private source read verified at the time shown below.");
    } finally {
      $("github-probe-submit").disabled = false;
    }
  });
});

$("github-source-connect").addEventListener("click", () =>
  action(async () => {
    const started = await api("/api/sources/github/start", {});
    const destination = trustedExternal(started.installationUrl, ["github.com"]);
    if (!destination)
      throw new Error("GitHub returned an unexpected installation destination.");
    location.assign(destination);
  }),
);

try {
  session = await api("/api/session");
  await refreshGitHubSources();
  const url = new URL(location.href);
  const result = url.searchParams.get("github");
  if (result === "linked")
    sourceMessage(
      "GitHub repositories linked. Source access will be revalidated before every private check.",
    );
  else if (result === "cancelled")
    sourceMessage("GitHub repository connection was cancelled; no authority was retained.", true);
  if (result) {
    url.searchParams.delete("github");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
} catch (error) {
  sourceMessage(error instanceof Error ? error.message : "GitHub source status unavailable.", true);
}
