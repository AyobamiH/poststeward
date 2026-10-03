/** Owner editorial UI. All generated/source strings use textContent, never HTML. */
export function mountPreparation({ invoke, action, show, onHandoff }) {
  const $ = (id) => document.getElementById(id);
  let polling = false,
    timer,
    hasPending = false;
  const key = () => crypto.randomUUID();
  const element = (tag, text) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const control = (parent, label, fn) => {
    const button = element("button", label);
    button.type = "button";
    button.onclick = () => action(fn);
    parent.append(button);
    return button;
  };
  const field = (parent, label, value, rows = 3) => {
    const wrapper = element("label", label);
    const input = element("textarea");
    input.value = value;
    input.rows = rows;
    wrapper.append(input);
    parent.append(wrapper);
    return input;
  };
  const activity = (job) => job.updatedAt || job.createdAt || 0;
  const identity = (job, latest) => {
    const node = element("div");
    node.className = "preparation-identity";
    if (latest && activity(job) > 0)
      node.append(element("strong", "Latest activity"));
    node.append(element("p", `Preparation ID: ${job.id}`));
    for (const [label, value] of [
      ["Created", job.createdAt],
      ["Last activity", job.updatedAt],
    ]) {
      const line = element("p", `${label}: `);
      const date = new Date(value);
      if (typeof value === "number" && Number.isFinite(date.getTime())) {
        const time = element(
          "time",
          date.toLocaleString("en-GB", {
            day: "2-digit",
            month: "short",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            timeZoneName: "short",
          }),
        );
        time.dateTime = date.toISOString();
        line.append(time);
      } else line.append("Unavailable");
      node.append(line);
    }
    return node;
  };
  const context = () => {
    const fd = new FormData($("preparation-create"));
    return Object.fromEntries(
      [
        "audience",
        "objective",
        "brandVoice",
        "productContext",
        "exclusions",
        "callToAction",
      ].map((name) => [name, String(fd.get(name) || "")]),
    );
  };
  const modelForm = $("preparation-model");
  const supported = {
    openai: ["gpt-4.1-mini-2025-04-14"],
    cloudflare_workers: [
      "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
      "@cf/meta/llama-3.1-8b-instruct",
    ],
    cloudflare_gateway: ["gpt-4.1-mini", "gpt-4.1"],
  };
  let settingsLoaded = false,
    settingsDirty = false;
  const choices = (select, values) => {
    const previous = select.value;
    select.replaceChildren(
      ...values.map((value) => {
        const option = element("option", value);
        option.value = value;
        return option;
      }),
    );
    if (values.includes(previous)) select.value = previous;
  };
  function providerFields() {
    const f = modelForm.elements,
      provider = f.modelProvider.value,
      cf = provider !== "openai";
    choices(f.modelName, supported[provider]);
    choices(f.fallbackModel, supported[f.fallbackProvider.value] || []);
    if (provider === "cloudflare_gateway") f.funding.value = "gateway_credits";
    if (f.fallbackProvider.value === "cloudflare_gateway")
      f.fallbackFunding.value = "gateway_credits";
    f.funding.disabled = !cf || provider === "cloudflare_gateway";
    for (const name of [
      "accountId",
      "gatewayId",
      "inspectionToken",
      "maxInputBytes",
      "maxOutputTokens",
      "temperature",
      "maxJobUsd",
      "logging",
      "fallbackProvider",
      "fallbackModel",
      "fallbackFunding",
    ])
      f[name].disabled = !cf;
    f.accountId.required = cf;
    f.gatewayId.required =
      cf &&
      (f.funding.value === "gateway_credits" ||
        (f.fallbackProvider.value &&
          f.fallbackFunding.value === "gateway_credits"));
    f.maxDailyUsd.required = cf;
    for (const name of ["inputUsdPerMillion", "outputUsdPerMillion"])
      f[name].disabled = cf;
    $("preparation-cloudflare-settings").hidden = !cf;
  }
  for (const name of [
    "modelProvider",
    "fallbackProvider",
    "funding",
    "fallbackFunding",
  ])
    modelForm.elements[name].addEventListener("change", providerFields);
  modelForm.addEventListener("input", () => {
    settingsDirty = true;
  });
  providerFields();
  const nullable = (value) =>
    value === "" || value === null ? null : Number(value);
  async function refresh() {
    const [status, jobs, policies] = await Promise.all([
      invoke("model_status"),
      invoke("preparations_list"),
      invoke("autonomy_list"),
    ]);
    const policyRoot = $("autonomy-projects");
    policyRoot.replaceChildren();
    for (const policy of policies) {
      const row = element("article");
      row.append(
        element(
          "p",
          `${policy.project}: ${policy.enabled ? (policy.error ? "held: " + policy.error : policy.deferred ? "waiting for daily budget reset" : "running") : "paused"} · stock target ${policy.stockFloor} · spacing ${policy.intervalMinutes} minutes · daily maximum ${policy.maxDailyDeliveries} per destination`,
        ),
      );
      if (policy.enabled)
        control(row, "Pause " + policy.project, () =>
          mutate("autonomy_pause", { project: policy.project }),
        );
      policyRoot.append(row);
    }
    jobs.sort(
      (a, b) =>
        activity(b) - activity(a) ||
        (b.createdAt || 0) - (a.createdAt || 0) ||
        a.id.localeCompare(b.id),
    );
    $("preparation-model-status").textContent = status.configured
      ? `${status.provider} · ${status.model} · funding ${status.funding} · ${status.authentication === "verified_by_successful_call" ? "successful inference observed" : "inference unverified"} · ${status.usage.jobs}/${status.limits.maxJobsPerDay} requests reserved today · ${status.usage.inputTokens} input / ${status.usage.outputTokens} output tokens reported. ${status.costNotice}`
      : "Connect your workspace's own OpenAI or Cloudflare account. PostSteward supplies no hidden company-funded model fallback.";
    if (status.routing) {
      $("preparation-model-status").textContent +=
        ` Reserved USD ${((status.usage.reservedMicros || 0) / 1e6).toFixed(6)}; reported-usage estimated USD ${((status.usage.settledMicros || 0) / 1e6).toFixed(6)}; uncertain/rejected-attempt allowance USD ${((status.usage.uncertainMicros || 0) / 1e6).toFixed(6)}. Pricing reviewed ${status.fundingProofs[0]?.catalogueDate || "unverified"}; account balances are shared and provider-reported, not exclusive workspace credit.`;
    }
    $("preparation-spend-notice").textContent = status.routing
      ? `Paid generation uses ${status.routing.primary.model}, funded by ${status.routing.primary.funding}, with a maximum USD ${status.routing.maxJobUsd} per request and USD ${status.routing.maxDailyUsd} per UTC day. Fallbacks: ${status.routing.fallbacks.length ? status.routing.fallbacks.map((r) => r.model + " / " + r.funding).join(", ") : "off"}. Provider charges and PostSteward subscription are separate.`
      : "Generation uses the connected OpenAI account and saved call/token limits. Connection validation does not generate content.";
    if (status.configured && !settingsLoaded && !settingsDirty) {
      const f = modelForm.elements,
        routing = status.routing;
      f.modelProvider.value = routing?.primary.provider || "openai";
      f.maxJobsPerDay.value = status.limits.maxJobsPerDay;
      f.allowAgents.checked = status.limits.allowAgents;
      for (const name of [
        "inputUsdPerMillion",
        "outputUsdPerMillion",
        "maxDailyUsd",
      ])
        f[name].value = status.limits[name] ?? "";
      if (routing) {
        for (const name of [
          "accountId",
          "maxInputBytes",
          "maxOutputTokens",
          "temperature",
          "maxJobUsd",
          "logging",
        ])
          f[name].value = routing[name];
        f.funding.value = routing.primary.funding;
        f.gatewayId.value =
          routing.primary.gatewayId || routing.fallbacks[0]?.gatewayId || "";
        f.fallbackProvider.value = routing.fallbacks[0]?.provider || "";
        f.fallbackFunding.value = routing.fallbacks[0]?.funding || "workers_ai";
      }
      providerFields();
      if (routing) {
        f.modelName.value = routing.primary.model;
        if (routing.fallbacks[0])
          f.fallbackModel.value = routing.fallbacks[0].model;
      }
      settingsLoaded = true;
    }
    const root = $("preparation-jobs");
    const previous = new Map(
      [...root.querySelectorAll(":scope > article")].map((node) => [
        node.dataset.jobId,
        node,
      ]),
    );
    for (const node of root.querySelectorAll(":scope > p")) node.remove();
    if (!jobs.length)
      root.append(
        element("p", "No preparations yet. Use Start a new preparation below."),
      );
    const rendered = new Map();
    for (const [index, job] of jobs.entries()) {
      const existing = previous.get(job.id);
      previous.delete(job.id);
      if (existing?.dataset.dirty === "true") {
        existing
          .querySelector(".preparation-identity")
          .replaceWith(identity(job, index === 0));
        existing
          .querySelector(".preparation-entry > summary")
          .replaceWith(
            preparationSummary(job, Number(existing.dataset.revision)),
          );
        rendered.set(job.id, existing);
        const note = existing.querySelector(".edit-notice");
        note.textContent =
          Number(existing.dataset.revision) !== job.revision
            ? "This preparation changed remotely. Your unsaved edits are retained; discard them to load the current revision."
            : "Unsaved edits retained during status refresh. Save and check them before approval.";
        continue;
      }
      const fresh = render(job, index === 0);
      rendered.set(job.id, fresh);
      if (existing) {
        const expanded = [...existing.querySelectorAll("details")].map(
          (node) => node.open,
        );
        [...fresh.querySelectorAll("details")].forEach((node, index) => {
          node.open = expanded[index] || false;
        });
        existing.replaceWith(fresh);
      } else root.append(fresh);
    }
    for (const node of previous.values()) node.remove();
    // Refresh must reorder existing nodes as well as newly appended cards.
    // Keep the owner's unsaved editor, focus and selection when moving it.
    const focused = document.activeElement;
    const selection = Number.isInteger(focused?.selectionStart)
      ? [
          focused.selectionStart,
          focused.selectionEnd,
          focused.selectionDirection,
        ]
      : null;
    for (const [index, job] of jobs.entries()) {
      const node = rendered.get(job.id);
      const current = root.querySelectorAll(":scope > article")[index];
      if (node !== current) root.insertBefore(node, current || null);
    }
    if (focused?.isConnected && document.activeElement !== focused) {
      focused.focus({ preventScroll: true });
      if (selection) focused.setSelectionRange(...selection);
    }
    clearTimeout(timer);
    hasPending = jobs.some((job) => ["queued", "running"].includes(job.status));
    if (hasPending)
      timer = setTimeout(async () => {
        if (polling || document.hidden) return;
        polling = true;
        try {
          await refresh();
        } catch {
          $("preparation-model-status").textContent =
            "Preparation status could not be refreshed. Use Load settings to inspect existing jobs before retrying.";
        } finally {
          polling = false;
        }
      }, 6000);
  }
  async function mutate(name, input) {
    const result = await invoke(name, { ...input, idempotencyKey: key() });
    for (const node of $("preparation-jobs").querySelectorAll(
      ":scope > article",
    ))
      if (node.dataset.jobId === input.id) delete node.dataset.dirty;
    await refresh();
    return result;
  }
  function preparationSummary(job, unsavedRevision) {
    const summary = element(
      "summary",
      `${job.selection.repository} · ${job.selection.releaseTag}`,
    );
    const status = element(
      "span",
      `${job.error?.code === "PREPARATION_CONTEXT_REQUIRED" ? "Needs more context" : job.status.replaceAll("_", " ")} · revision ${job.revision} · Open preparation`,
    );
    status.className = "muted";
    summary.append(status);
    const lastActivity = activity(job);
    if (lastActivity) {
      const updated = element(
        "span",
        `Last activity: ${new Date(lastActivity).toLocaleString("en-GB", { timeZoneName: "short" })}`,
      );
      updated.className = "muted";
      summary.append(updated);
    }
    if (unsavedRevision !== undefined) {
      const unsaved = element(
        "span",
        `Unsaved edits from revision ${unsavedRevision}`,
      );
      unsaved.className = "muted preparation-unsaved";
      summary.append(unsaved);
    }
    return summary;
  }
  function compactPreparation(article, job) {
    const entry = element("details");
    entry.className = "preparation-entry";
    entry.append(preparationSummary(job), ...article.childNodes);
    article.append(entry);
    return article;
  }
  function render(job, latest) {
    const article = element("article");
    article.className = "record preparation-card";
    article.dataset.jobId = job.id;
    article.dataset.revision = job.revision;
    const notice = element("p");
    notice.className = "edit-notice";
    notice.setAttribute("role", "status");
    article.append(notice);
    const markDirty = (input) => {
      input.addEventListener("input", () => {
        article.dataset.dirty = "true";
        const summary = article.querySelector(".preparation-entry > summary");
        if (summary && !summary.querySelector(".preparation-unsaved")) {
          const unsaved = element("span", "Unsaved edits");
          unsaved.className = "muted preparation-unsaved";
          summary.append(unsaved);
        }
        notice.textContent =
          "Unsaved edits. Save and check them before approval.";
      });
    };
    article.append(
      element(
        "h3",
        `${job.selection.repository} · ${job.selection.releaseTag}`,
      ),
      identity(job, latest),
      element(
        "p",
        `State: ${job.error?.code === "PREPARATION_CONTEXT_REQUIRED" ? "Needs more context" : job.status.replaceAll("_", " ")} · revision ${job.revision} · ${job.stage} · ${job.usage.length} model calls completed`,
      ),
    );
    const funding = element("details");
    funding.className = "preparation-funding";
    funding.append(
      element(
        "summary",
        "Inspect model, funding and budget evidence (no prompts or credentials)",
      ),
    );
    if (job.routing)
      funding.append(
        element(
          "p",
          `Model selection: ${job.routing.primary.model} · funding ${job.routing.primary.funding} · configuration ${job.modelRevision}. ${job.attempts?.length || 0} attempted model calls, including permitted fallbacks.`,
        ),
      );
    if (job.attempts?.length) {
      const attempts = funding;
      for (const attempt of job.attempts)
        attempts.append(
          element(
            "p",
            `${attempt.stage}: ${attempt.provider} / ${attempt.model} / ${attempt.funding}; ${attempt.fallback ? "explicit fallback" : "primary"}; ${attempt.outcome}; maximum reserved USD ${(attempt.reservedMicros / 1e6).toFixed(6)}${attempt.estimatedMicros === undefined ? " · charge uncertain/conservatively retained" : " · reported-usage estimate USD " + (attempt.estimatedMicros / 1e6).toFixed(6)}. Editorial policy ${attempt.editorialPolicyVersion || "unrecorded"}; execution release ${attempt.executionRelease || "unrecorded"}. Funding configuration readback ${attempt.fundingProof?.checkedAt ? new Date(attempt.fundingProof.checkedAt).toISOString() : "unverified"}; invoice/readback unverified.`,
          ),
        );
    }
    if (job.error) article.append(element("p", job.error.message));
    const details = element("details");
    details.className = "preparation-review";
    details.open = latest;
    details.append(
      element(
        "summary",
        "Inspect strategy, source evidence and channel drafts",
      ),
    );
    article.append(details);
    const layout = element("div");
    layout.className = "preparation-review-grid";
    const copyPanel = element("section");
    copyPanel.className = "preparation-copy-panel";
    copyPanel.append(
      element("h4", "Review your channel copy"),
      element(
        "p",
        "Edit the exact text below. Save changes, then check the saved copy before owner approval. Nothing here publishes a post.",
      ),
    );
    const support = element("div");
    support.className = "preparation-support-panel";
    layout.append(copyPanel, support);
    details.append(layout);
    const checkPanel = element("section");
    checkPanel.className = "preparation-check";
    checkPanel.append(element("h4", "Model-assisted editorial check"));
    const ready =
      job.critique?.acceptableForOwnerReview && !job.critique.issues.length;
    checkPanel.dataset.state = job.critique
      ? ready
        ? "ready"
        : "blocked"
      : "pending";
    checkPanel.append(
      element(
        "strong",
        job.critique
          ? ready
            ? "Ready for owner review"
            : "Changes need review"
          : "No editorial check result yet",
      ),
    );
    checkPanel.append(
      element(
        "p",
        job.critique?.summary ||
          "A completed check is required before approval. Saving edits does not run a model check.",
      ),
    );
    for (const issue of job.critique?.issues || [])
      checkPanel.append(
        element(
          "p",
          `${issue.alias || "Strategy"} · ${issue.category}: ${issue.detail}`,
        ),
      );
    checkPanel.append(
      element(
        "p",
        "Model checks assist your review; inspect the pinned sources yourself.",
      ),
    );
    support.append(checkPanel);
    const actions = element("div");
    actions.className = "preparation-primary-actions";
    const more = element("details");
    more.className = "preparation-more";
    more.append(
      element("summary", "More preparation actions"),
      element(
        "p",
        "Regeneration uses your connected model account and daily allowance. Strategy regeneration reads the Start a new preparation context form below.",
      ),
    );
    const moreActions = element("div");
    moreActions.className = "preparation-action-row";
    more.append(moreActions);
    const base = { id: job.id, revision: job.revision };
    if (["queued", "running"].includes(job.status)) {
      control(article, "Reject preparation", () =>
        mutate("preparation_reject", base),
      );
      return compactPreparation(article, job);
    }
    const mutable = !["approved", "handed_off"].includes(job.status);
    const strategy = job.strategy ? structuredClone(job.strategy) : undefined;
    if (strategy) {
      const strategyPanel = element("details");
      strategyPanel.className = "preparation-strategy";
      strategyPanel.append(element("summary", "Audience and strategy"));
      support.append(strategyPanel);
      strategyPanel.append(
        element(
          "h4",
          "Suggested audience and strategy — interpretations require review",
        ),
      );
      const audience = field(strategyPanel, "Audience", strategy.audience),
        objective = field(strategyPanel, "Objective", strategy.objective),
        positioning = field(strategyPanel, "Positioning", strategy.positioning);
      strategyPanel.append(element("p", strategy.channelApproach));
      for (const change of strategy.changes)
        strategyPanel.append(
          element(
            "p",
            `Source-backed change: ${change.fact}\nAudience problem: ${change.audienceProblem}\nProposed implication: ${change.implication}`,
          ),
        );
      for (const missing of strategy.missingContext)
        strategyPanel.append(element("p", `Missing context: ${missing}`));
      for (const risk of strategy.risks)
        strategyPanel.append(element("p", `Review risk: ${risk}`));
      strategy.controls = { audience, objective, positioning };
      if (mutable)
        for (const input of [audience, objective, positioning])
          markDirty(input);
      if (!mutable)
        for (const input of [audience, objective, positioning])
          input.readOnly = true;
    }
    if (job.evidence?.length || job.coverage || job.gaps?.length) {
      const evidence = element("details");
      evidence.append(
        element("summary", "Pinned sources and approved context"),
      );
      support.append(evidence);
      if (job.coverage) evidence.append(element("p", job.coverage));
      for (const gap of job.gaps || [])
        evidence.append(element("p", `Source coverage: ${gap}`));
      for (const item of job.evidence || []) {
        evidence.append(
          element("h4", `${item.id} · ${item.kind.replaceAll("_", " ")}`),
        );
        const url = new URL(item.url, location.origin);
        if (
          url.protocol === "https:" &&
          (url.hostname === "github.com" || url.origin === location.origin)
        ) {
          const link = element("a", "Open source");
          link.href = url.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          evidence.append(link);
        }
        const quote = element("pre", item.text);
        quote.className = "source-evidence";
        quote.tabIndex = 0;
        quote.setAttribute("aria-label", "Source evidence " + item.id);
        evidence.append(quote);
      }
    }
    const edits = [];
    for (const draft of job.drafts || []) {
      const variant = element("section");
      variant.className = "preparation-variant";
      variant.append(element("h4", draft.alias));
      copyPanel.append(variant);
      const keep = element("input");
      keep.type = "checkbox";
      keep.checked = true;
      keep.disabled = !mutable;
      const keepLabel = element("label", "Include this variant");
      keepLabel.prepend(keep);
      variant.append(keepLabel);
      const input = field(variant, "Exact channel text", draft.text, 6);
      input.readOnly = !mutable;
      edits.push({ alias: draft.alias, input, keep });
      if (mutable) {
        markDirty(input);
        markDirty(keep);
      }
      const annotations = element("details");
      annotations.append(
        element("summary", "Original generation notes and source annotations"),
        element(
          "p",
          "These notes were generated with the original draft and may refer to earlier copy after edits. The editorial check examines your current saved channel text against the pinned sources and approved context.",
        ),
        element("p", draft.rationale),
      );
      variant.append(annotations);
      for (const claim of draft.claims)
        annotations.append(
          element(
            "p",
            `Generated claim annotation: ${claim.claim}\n${claim.sources.map((source) => `${source.evidence}: “${source.quote}”`).join("\n")}`,
          ),
        );
      if (mutable)
        control(moreActions, `Regenerate ${draft.alias} draft`, () =>
          mutate("preparation_regenerate", {
            ...base,
            stage: "draft",
            alias: draft.alias,
          }),
        );
    }
    if (!edits.length)
      copyPanel.append(
        element(
          "p",
          "No channel drafts are available for this preparation. Inspect its state and source context before requesting more model work.",
        ),
      );
    const reported = job.usage.reduce(
      (total, call) => ({
        input: total.input + call.inputTokens,
        output: total.output + call.outputTokens,
        ms: total.ms + call.latencyMs,
      }),
      { input: 0, output: 0, ms: 0 },
    );
    funding.append(
      element(
        "p",
        `Reported usage: ${reported.input} input / ${reported.output} output tokens; ${(reported.ms / 1000).toFixed(1)} seconds of model latency. Uncertain calls may still be billed.`,
      ),
    );
    if (mutable) {
      copyPanel.append(actions);
      if (edits.length)
        copyPanel.append(
          element(
            "p",
            "Saving is free. Check current saved drafts uses one preparation request and may incur a model charge.",
          ),
        );
    }
    if (mutable) {
      control(moreActions, "Discard unsaved edits and refresh", async () => {
        delete article.dataset.dirty;
        await refresh();
      });
      if (edits.length)
        control(actions, "Save edits and selected variants", async () => {
          const { controls, ...changed } = strategy || {};
          if (controls)
            for (const name of ["audience", "objective", "positioning"])
              changed[name] = controls[name].value;
          await mutate("preparation_edit", {
            ...base,
            text: Object.fromEntries(
              edits
                .filter((edit) => edit.keep.checked)
                .map((edit) => [edit.alias, edit.input.value]),
            ),
            ...(controls ? { strategy: changed } : {}),
          });
        });
      control(
        moreActions,
        "Regenerate strategy from current context form",
        () =>
          mutate("preparation_regenerate", {
            ...base,
            stage: "interpret",
            context: context(),
          }),
      );
      if (edits.length)
        control(actions, "Check current saved drafts", () => {
          if (article.dataset.dirty === "true")
            throw new Error(
              "Save or discard your unsaved edits before checking the saved drafts.",
            );
          return mutate("preparation_regenerate", { ...base, stage: "check" });
        });
      control(moreActions, "Reject this preparation", () =>
        mutate("preparation_reject", base),
      );
      if (
        job.status === "review" &&
        job.digest &&
        job.critique?.acceptableForOwnerReview &&
        !job.critique.issues.length &&
        edits.length
      ) {
        const accepted = element("input");
        accepted.type = "checkbox";
        const label = element(
          "label",
          "I reviewed each included variant, its source support, public availability claims and destination. Freeze this exact saved revision into a campaign.",
        );
        label.prepend(accepted);
        copyPanel.append(label);
        const approve = control(
          copyPanel,
          "Approve exact saved copy and open delivery review",
          async () => {
            if (!accepted.checked)
              throw new Error(
                "Review and confirm the exact saved content before approval.",
              );
            if (
              edits.some(
                (edit) =>
                  !edit.keep.checked ||
                  edit.input.value !==
                    job.drafts.find((draft) => draft.alias === edit.alias).text,
              ) ||
              (strategy?.controls &&
                ["audience", "objective", "positioning"].some(
                  (name) =>
                    strategy.controls[name].value !== job.strategy[name],
                ))
            )
              throw new Error(
                "Save your edits and check the saved drafts before approving. Approval freezes the exact saved revision.",
              );
            const result = await mutate("preparation_approve", {
              ...base,
              digest: job.digest,
            });
            await onHandoff(result);
          },
        );
        approve.disabled = true;
        accepted.onchange = () => {
          approve.disabled = !accepted.checked;
        };
      }
    }
    if (mutable) {
      copyPanel.append(more);
    }
    article.append(funding);
    if (!["queued", "running", "approved"].includes(job.status)) {
      let exported;
      const confirmed = element("input");
      confirmed.type = "checkbox";
      confirmed.disabled = true;
      const label = element(
        "label",
        "I saved the private export. Remove this preparation from my active library; immutable campaigns, receipts and operation history remain.",
      );
      label.prepend(confirmed);
      const archive = element("details");
      archive.className = "preparation-archive";
      archive.append(
        element("summary", "Export or remove this preparation"),
        label,
      );
      article.append(archive);
      const remove = control(archive, "Remove exported preparation", () =>
        mutate("preparation_archive", {
          ...base,
          reviewDigest: exported.reviewDigest,
        }),
      );
      remove.disabled = true;
      confirmed.onchange = () => {
        remove.disabled = !confirmed.checked;
      };
      control(archive, "Download private preparation export", async () => {
        if (article.dataset.dirty === "true")
          throw new Error(
            "Save or discard unsaved edits before exporting the saved preparation.",
          );
        exported = await invoke("preparation_export", { id: job.id });
        const url = URL.createObjectURL(
          new Blob([JSON.stringify(exported, null, 2)], {
            type: "application/json",
          }),
        );
        const link = element("a");
        link.href = url;
        link.download = "poststeward-preparation-" + job.id + ".json";
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        confirmed.disabled = false;
      });
    }
    if (job.campaign) {
      article.append(
        element(
          "p",
          "Owner approved → immutable campaign. Scheduling, publication and verified readback appear in the existing delivery receipts.",
        ),
      );
      control(article, "Open approved campaign delivery review", () =>
        onHandoff(job),
      );
    }
    return compactPreparation(article, job);
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && hasPending && !polling) action(refresh);
  });
  $("preparation-refresh").onclick = () => action(refresh);
  $("preparation-model").onsubmit = (event) => {
    event.preventDefault();
    action(async () => {
      const form = event.currentTarget,
        fd = new FormData(form);
      const apiKey = String(fd.get("apiKey") || "");
      const inspectionToken = String(fd.get("inspectionToken") || "");
      form.elements.apiKey.value = "";
      form.elements.inspectionToken.value = "";
      const provider = form.elements.modelProvider.value;
      const route = (provider, model, funding) => ({
        provider,
        model,
        funding:
          provider === "cloudflare_gateway" ? "gateway_credits" : funding,
        ...(provider === "cloudflare_gateway" || funding === "gateway_credits"
          ? { gatewayId: String(form.elements.gatewayId.value) }
          : {}),
      });
      const cf = provider !== "openai";
      try {
        await mutate("model_connect", {
          ...(apiKey ? { apiKey } : {}),
          ...(cf && inspectionToken ? { inspectionToken } : {}),
          ...(cf
            ? {
                routing: {
                  version: 1,
                  accountId: String(form.elements.accountId.value),
                  primary: route(
                    provider,
                    form.elements.modelName.value,
                    form.elements.funding.value,
                  ),
                  fallbacks: form.elements.fallbackProvider.value
                    ? [
                        route(
                          form.elements.fallbackProvider.value,
                          form.elements.fallbackModel.value,
                          form.elements.fallbackFunding.value,
                        ),
                      ]
                    : [],
                  maxInputBytes: Number(fd.get("maxInputBytes")),
                  maxOutputTokens: Number(fd.get("maxOutputTokens")),
                  temperature: Number(fd.get("temperature")),
                  maxJobUsd: Number(fd.get("maxJobUsd")),
                  maxDailyUsd: Number(fd.get("maxDailyUsd")),
                  logging: String(fd.get("logging")),
                },
              }
            : {}),
          maxJobsPerDay: Number(fd.get("maxJobsPerDay")),
          allowAgents: fd.has("allowAgents"),
          inputUsdPerMillion: cf
            ? null
            : nullable(fd.get("inputUsdPerMillion")),
          outputUsdPerMillion: cf
            ? null
            : nullable(fd.get("outputUsdPerMillion")),
          maxDailyUsd: nullable(fd.get("maxDailyUsd")),
        });
        settingsDirty = false;
        show(
          cf
            ? "Cloudflare metadata validated; encrypted settings saved. No inference or gateway/billing change occurred. Actual inference and funding invoice remain unverified until an authorized generation."
            : "Encrypted OpenAI settings saved; no inference occurred.",
        );
      } finally {
        form.elements.apiKey.value = "";
        form.elements.inspectionToken.value = "";
      }
    });
  };
  $("preparation-model-disconnect").onclick = () =>
    action(() => mutate("model_disconnect", {}));
  const sourceForm = $("preparation-create");
  const sourceKind = sourceForm.elements.sourceKind;
  const sourceFields = () => {
    const repository = sourceKind.value === "repository";
    const docs = sourceForm.elements.documentationPaths;
    docs.required = repository;
    docs.closest("label").firstChild.textContent = repository
      ? "Documentation paths, comma separated (one to three)"
      : "Documentation paths, comma separated (optional; up to three)";
    sourceForm.elements.previousTag.closest("label").hidden = repository;
    if (repository) sourceForm.elements.previousTag.value = "";
    sourceForm.elements.releaseTag.placeholder = repository ? "main" : "v1.0.0";
    const options = sourceForm.querySelector(".preparation-source-options");
    if (options) {
      options.querySelector("summary").textContent = repository
        ? "Project documentation (required)"
        : "Additional release sources (optional)";
      if (repository) options.open = true;
    }
  };
  sourceKind.onchange = sourceFields;
  sourceFields();
  $("preparation-create").onsubmit = (event) => {
    event.preventDefault();
    const fd = new FormData(event.currentTarget);
    const autonomous = event.submitter?.value === "autonomous";
    action(async () => {
      if (autonomous && !fd.has("standingConsent"))
        throw new Error(
          "Confirm standing model and publishing authority before starting.",
        );
      await mutate(autonomous ? "autonomy_configure" : "preparation_create", {
        project: fd.get("project"),
        selection: {
          sourceKind: fd.get("sourceKind") || "release",
          repository: fd.get("repository"),
          releaseTag: fd.get("releaseTag"),
          ...(fd.get("previousTag")
            ? { previousTag: fd.get("previousTag") }
            : {}),
          documentationPaths: String(fd.get("documentationPaths") || "")
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean),
          allowPrivate: fd.has("allowPrivate"),
          allowUnreleased: fd.has("allowUnreleased"),
        },
        context: context(),
        ...(autonomous
          ? {
              enabled: true,
              intervalMinutes: Number(fd.get("intervalMinutes")),
              stockFloor: Number(fd.get("stockFloor")),
              maxDailyDeliveries: Number(fd.get("maxDailyDeliveries")),
            }
          : {}),
      });
      show(
        autonomous
          ? "Autonomous publishing started. Checked, distinct supply will be scheduled within your account cadence and model budget. Inspect stock or pause under Autonomous projects."
          : "Preparation queued. Inspect strategy, evidence and drafts here; no publication or schedule was created.",
      );
    });
  };
  return {
    setExecutor(mode) {
      $("autonomy-setup").hidden = mode === "local";
    },
    setProjects(projects) {
      const select = $("preparation-project"),
        previous = select.value;
      select.replaceChildren();
      for (const project of projects) {
        const option = element("option", project.name);
        option.value = project.id;
        select.append(option);
      }
      if (projects.some((p) => p.id === previous)) select.value = previous;
    },
  };
}
