/** Presentation only. Move existing controls without replacing their operational handlers. */
export function workspaceRoute(hash) {
  const id = hash.replace(/^#/, "");
  if (
    ["publishing", "project", "project-select", "account-select"].includes(id)
  )
    return { view: "publishing", step: "destination" };
  if (
    ["release-preparation", "preparation-create", "preparation-jobs"].includes(
      id,
    )
  )
    return { view: "publishing", step: "prepare" };
  if (["campaign", "exact-copy-panel"].includes(id))
    return { view: "publishing", step: "review" };
  if (["delivery", "campaign-preview"].includes(id))
    return { view: "publishing", step: "schedule" };
  const views = {
    destinations: "accounts",
    "evidence-panel": "results",
    "agent-access": "agents",
    sources: "sources",
    "preparation-model": "sources",
    advanced: "billing",
    profile: "automation",
    "runtime-authority-panel": "runtime",
    "runtime-settings": "runtime",
    "runtime-pairing-panel": "runtime",
    "recovery-safety": "recovery",
  };
  return { view: Object.hasOwn(views, id) ? views[id] : "overview" };
}

export function mountWorkspaceLayout() {
  const $ = (id) => document.getElementById(id);
  const root = $("workspace-content");
  if (!root) return;
  document.body.classList.add("focused-workspace");
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const move = (id, parent) => {
    const el = $(id);
    if (el) parent.append(el);
    return el;
  };
  const views = new Map();
  const labels = {
    overview: [
      "Your publishing workspace",
      "Choose your next task or inspect recent publishing activity.",
    ],
    accounts: [
      "Social accounts",
      "Connect an account and inspect its publishing capabilities.",
    ],
    publishing: [
      "Create & schedule",
      "Choose a destination, prepare your content, review exact copy and schedule it.",
    ],
    results: [
      "Schedules & results",
      "Inspect reservations, provider outcomes and the exact approved copy.",
    ],
    agents: [
      "Agent permissions",
      "Issue scoped, expiring access and revoke grants when they are no longer needed.",
    ],
    sources: [
      "Sources & models",
      "Manage source access, your model account, funding route and spending limits.",
    ],
    billing: [
      "Advanced & billing",
      "Inspect subscription availability, quotes and billing management.",
    ],
    automation: [
      "Automation profiles",
      "Configure reviewed source monitoring and spaced future delivery.",
    ],
    runtime: [
      "Local runtime",
      "Inspect paired machines and choose the workspace's publishing executor.",
    ],
    recovery: [
      "Workspace recovery",
      "Inspect the consequences before preparing a recovery plan.",
    ],
  };
  const host = node("div", undefined, "workspace-views");
  root.append(host);
  for (const [key, [title]] of Object.entries(labels)) {
    const el = node("div", undefined, "workspace-view");
    el.dataset.workspaceView = key;
    el.setAttribute("role", "region");
    el.setAttribute("aria-label", title);
    el.hidden = true;
    views.set(key, el);
    host.append(el);
  }
  move("workspace-next-step", views.get("overview"));
  const links = document.querySelector(".task-links");
  if (links) {
    links.setAttribute("aria-label", "Quick tasks");
    views.get("overview").append(links);
  }
  const overview = node("section", undefined, "workspace-overview-panel");
  overview.append(node("h2", "Publishing at a glance"));
  move("readiness", overview);
  views.get("overview").append(overview);
  const capabilities = $("publishing-capabilities")?.closest("details");
  move("destinations", views.get("accounts"));
  if (capabilities) views.get("accounts").append(capabilities);
  move("evidence-panel", views.get("results"));
  move("agent-access", views.get("agents"));
  move("sources", views.get("sources"));
  const model = $("preparation-model")?.closest("details");
  if (model) views.get("sources").append(model);
  move("advanced", views.get("billing"));
  const profile = $("profile");
  if (profile) {
    const heading = profile.previousElementSibling;
    if (heading?.tagName === "H3") views.get("automation").append(heading);
    views.get("automation").append(profile);
    move("profiles", views.get("automation"));
    const profileLink = node("a", "Manage automation profiles", "button");
    profileLink.href = "#profile";
    views.get("billing").append(profileLink);
  }
  move("recovery-safety", views.get("recovery"));
  move("runtime-pairing-panel", views.get("runtime"));
  const settings = move("runtime-settings", views.get("runtime"));
  if (settings) settings.open = true;
  const transport = $("webmcp-observation")?.closest("details");
  if (transport) views.get("runtime").append(transport);
  move("webmcp-status", views.get("runtime"));

  const publishing = views.get("publishing");
  const steps = new Map();
  const stepNav = node("nav", undefined, "publishing-steps");
  stepNav.setAttribute("aria-label", "Publishing steps");
  publishing.append(stepNav);
  for (const [key, title, hash] of [
    ["destination", "1. Destination", "publishing"],
    ["prepare", "2. Prepare", "release-preparation"],
    ["review", "3. Review", "campaign"],
    ["schedule", "4. Schedule", "delivery"],
  ]) {
    const link = node("a", title);
    link.href = "#" + hash;
    link.dataset.publishingStep = key;
    stepNav.append(link);
    const panel = node("section", undefined, "publishing-step");
    panel.dataset.publishingPanel = key;
    panel.hidden = true;
    steps.set(key, panel);
    publishing.append(panel);
  }
  const destination = steps.get("destination");
  const destinationHeading = node("h2", "Choose where this post will go");
  destinationHeading.id = "destination-heading";
  destination.append(destinationHeading);
  const selectors = node("div", undefined, "destination-fields");
  for (const id of ["project-select", "account-select"]) {
    const input = $(id);
    input.setAttribute("form", "campaign");
    selectors.append(input.closest("label"));
  }
  destination.append(selectors);
  const connectionLink = node("a", "Connect or manage social accounts");
  connectionLink.href = "#destinations";
  destination.append(connectionLink);
  const projects = node("details", undefined, "project-setup");
  projects.append(node("summary", "Create or inspect publishing projects"));
  move("publishing", projects);
  destination.append(projects);
  const continueLink = node(
    "a",
    "Continue to prepare content",
    "button primary",
  );
  continueLink.href = "#release-preparation";
  destination.append(continueLink);
  move("release-preparation", steps.get("prepare"));
  $("accounts-heading").textContent = "Connect a social account";
  $("release-preparation-heading").textContent =
    "Prepare content from a release";
  const modelLink = node("a", "Manage model account and spending limits");
  modelLink.href = "#preparation-model";
  const prepareForm = $("preparation-create");
  const group = (title, names) => {
    const fields = node("fieldset", undefined, "preparation-field-group");
    fields.append(node("legend", title));
    for (const name of names)
      fields.append(prepareForm.elements[name].closest("label"));
    return fields;
  };
  const sourceFields = group("Release source", [
    "project",
    "repository",
    "releaseTag",
  ]);
  const sourceOptions = node(
    "details",
    undefined,
    "preparation-source-options",
  );
  sourceOptions.append(
    node("summary", "Additional release sources (optional)"),
  );
  for (const name of ["previousTag", "documentationPaths"])
    sourceOptions.append(prepareForm.elements[name].closest("label"));
  sourceFields.append(sourceOptions);
  const briefFields = group("Campaign brief", [
    "audience",
    "objective",
    "productContext",
    "brandVoice",
    "callToAction",
    "exclusions",
  ]);
  const consentFields = group("Generation permissions & cost", [
    "allowPrivate",
    "allowUnreleased",
    "spendConsent",
  ]);
  consentFields.insertBefore(
    $("preparation-spend-notice"),
    consentFields.querySelector("label:last-child"),
  );
  consentFields.insertBefore(
    $("preparation-model-status"),
    consentFields.querySelector("label"),
  );
  consentFields.insertBefore(modelLink, consentFields.querySelector("label"));
  prepareForm.prepend(sourceFields, briefFields, consentFields);
  const prepareLink = node("a", "Use my own copy instead", "button");
  prepareLink.href = "#campaign";
  steps.get("prepare").prepend(prepareLink);

  const copy = $("campaign").closest("section");
  copy.id = "exact-copy-panel";
  steps.get("review").append(copy);
  $("exact-copy-heading").textContent = "Review your exact copy";
  const editor = node("div", undefined, "workspace-editor-grid");
  copy.append(editor);
  editor.append($("campaign"));
  const preview = node("aside", undefined, "workspace-post-preview");
  preview.setAttribute("aria-label", "Draft preview");
  preview.append(node("h3", "Draft preview"));
  const previewDestination = node("p", undefined, "muted");
  const previewText = node(
    "p",
    "Your text will appear here.",
    "workspace-preview-text",
  );
  preview.append(
    previewDestination,
    previewText,
    node(
      "p",
      "This is a draft preview. Validate the exact copy before submitting a delivery.",
      "muted",
    ),
  );
  editor.append(preview);
  const schedule = steps.get("schedule");
  schedule.append(
    node("h2", "Review and submit your delivery"),
    node(
      "p",
      "Validate a campaign in Review first. The frozen copy appears below before you explicitly schedule or publish.",
      "schedule-guidance",
    ),
  );
  move("campaign-preview", schedule);
  move("delivery", schedule);
  const returnLink = node("a", "Return to copy review", "button");
  returnLink.href = "#campaign";
  schedule.append(returnLink);

  host.prepend($("local-delivery-note"));

  // Empty original wrappers must not reserve space or expose inactive tasks.
  for (const child of [...root.children]) if (child !== host) child.remove();
  const renderPreview = () => {
    const form = $("campaign");
    previewText.textContent =
      form.elements.text.value || "Your text will appear here.";
    previewDestination.textContent =
      $("account-select").selectedOptions[0]?.textContent ||
      "Choose a destination first";
  };
  $("campaign").addEventListener("input", renderPreview);
  $("account-select").addEventListener("change", renderPreview);

  let previousHash;
  function render(focus = false, firstLoad = false) {
    let hash;
    try {
      hash = "#" + decodeURIComponent(location.hash.slice(1));
    } catch {
      hash = "";
    }
    const pairing = new URLSearchParams(location.search).has("runtime_pairing");
    const route = pairing ? { view: "runtime" } : workspaceRoute(hash);
    for (const [key, el] of views) el.hidden = key !== route.view;
    for (const [key, el] of steps) el.hidden = key !== route.step;
    for (const link of stepNav.children) {
      if (link.dataset.publishingStep === route.step)
        link.setAttribute("aria-current", "step");
      else link.removeAttribute("aria-current");
    }
    const [title, description] = labels[route.view];
    $("plan").textContent = title;
    document.title = `${title} · PostSteward`;
    document.querySelector(
      ".product-page-header .page-description",
    ).textContent = description;
    if ($("workspace-next-heading").textContent === title)
      $("workspace-next-heading").textContent = "Your next publishing task";
    const navHash = {
      accounts: "destinations",
      publishing: "publishing",
      results: "evidence-panel",
      agents: "agent-access",
      sources: "sources",
      billing: "advanced",
      automation: "profile",
      runtime: "runtime-authority-panel",
      recovery: "recovery-safety",
    }[route.view];
    for (const link of document.querySelectorAll(
      ".product-nav a, .product-menu-panel a",
    )) {
      const url = new URL(link.href);
      const active =
        url.pathname === "/app" && url.hash === (navHash ? "#" + navHash : "");
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    if (
      firstLoad ||
      hash !== previousHash ||
      $("project-select").options.length === 0
    )
      projects.open =
        $("project-select").options.length === 0 || hash === "#project";
    previousHash = hash;
    $("publishing-heading").textContent = $(
      "publishing-heading",
    ).textContent.replace(/^2\. /, "");
    if (hash === "#preparation-model" && model) model.open = true;
    renderPreview();
    if (focus && !root.hidden && hash !== "#main-content") {
      const target = $(hash.slice(1));
      const title =
        (target?.checkVisibility() && target.querySelector("h2,h3")) ||
        steps.get(route.step)?.querySelector("h2") ||
        $("plan");
      title.tabIndex = -1;
      title.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "instant" });
    }
  }
  addEventListener("hashchange", () => render(true));
  document.addEventListener("workspace-ready", () =>
    render(Boolean(location.hash), true),
  );
  document.addEventListener("workspace-updated", () => render(false));
  render();
  return {
    openDelivery() {
      location.hash = "delivery";
      render(true);
      if (!$("result").hidden) schedule.append($("result"));
    },
  };
}
