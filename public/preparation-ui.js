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
  const nullable = (value) => (value === "" ? null : Number(value));
  async function refresh() {
    const [status, jobs] = await Promise.all([
      invoke("model_status"),
      invoke("preparations_list"),
    ]);
    $("preparation-model-status").textContent = status.configured
      ? `OpenAI account connected · ${status.authentication === "verified_by_successful_call" ? "API access verified by a successful call" : "API access not yet verified"} · ${status.usage.jobs}/${status.limits.maxJobsPerDay} preparation requests reserved today · ${status.usage.inputTokens} input / ${status.usage.outputTokens} output tokens reported${status.usage.estimatedUsd === null ? " · USD cost unavailable; add provider rates" : ` · estimated USD ${status.usage.estimatedUsd.toFixed(4)}`}. Your model account is billed directly.`
      : "Connect your workspace’s own OpenAI API account to request original generation. PostSteward does not supply a shared company key.";
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
        element(
          "p",
          "No preparations yet. Select a release and approved context above.",
        ),
      );
    for (const job of jobs) {
      const existing = previous.get(job.id);
      previous.delete(job.id);
      if (existing?.dataset.dirty === "true") {
        const note = existing.querySelector(".edit-notice");
        note.textContent =
          Number(existing.dataset.revision) !== job.revision
            ? "This preparation changed remotely. Your unsaved edits are retained; discard them to load the current revision."
            : "Unsaved edits retained during status refresh. Save and check them before approval.";
        continue;
      }
      const fresh = render(job);
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
  function render(job) {
    const article = element("article");
    article.className = "record";
    article.dataset.jobId = job.id;
    article.dataset.revision = job.revision;
    const notice = element("p");
    notice.className = "edit-notice";
    notice.setAttribute("role", "status");
    article.append(notice);
    const markDirty = (input) => {
      input.addEventListener("input", () => {
        article.dataset.dirty = "true";
        notice.textContent =
          "Unsaved edits. Save and check them before approval.";
      });
    };
    article.append(
      element(
        "h3",
        `${job.selection.repository} · ${job.selection.releaseTag}`,
      ),
      element(
        "p",
        `State: ${job.status.replaceAll("_", " ")} · revision ${job.revision} · ${job.stage} · ${job.usage.length} model calls completed`,
      ),
    );
    if (job.error) article.append(element("p", job.error.message));
    if (job.coverage) article.append(element("p", job.coverage));
    for (const gap of job.gaps || [])
      article.append(element("p", `Source coverage: ${gap}`));
    const details = element("details");
    details.append(
      element(
        "summary",
        "Inspect strategy, source evidence and channel drafts",
      ),
    );
    article.append(details);
    const base = { id: job.id, revision: job.revision };
    if (["queued", "running"].includes(job.status)) {
      control(article, "Reject preparation", () =>
        mutate("preparation_reject", base),
      );
      return article;
    }
    const mutable = !["approved", "handed_off"].includes(job.status);
    const strategy = job.strategy ? structuredClone(job.strategy) : undefined;
    if (strategy) {
      details.append(
        element(
          "h4",
          "Suggested audience and strategy — interpretations require review",
        ),
      );
      const audience = field(details, "Audience", strategy.audience),
        objective = field(details, "Objective", strategy.objective),
        positioning = field(details, "Positioning", strategy.positioning);
      details.append(element("p", strategy.channelApproach));
      for (const change of strategy.changes)
        details.append(
          element(
            "p",
            `Source-backed change: ${change.fact}\nAudience problem: ${change.audienceProblem}\nProposed implication: ${change.implication}`,
          ),
        );
      for (const missing of strategy.missingContext)
        details.append(element("p", `Missing context: ${missing}`));
      for (const risk of strategy.risks)
        details.append(element("p", `Review risk: ${risk}`));
      strategy.controls = { audience, objective, positioning };
      if (mutable)
        for (const input of [audience, objective, positioning])
          markDirty(input);
      if (!mutable)
        for (const input of [audience, objective, positioning])
          input.readOnly = true;
    }
    if (job.evidence?.length) {
      const evidence = element("details");
      evidence.append(
        element("summary", "Pinned sources and approved context"),
      );
      details.append(evidence);
      for (const item of job.evidence) {
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
      details.append(element("h4", draft.alias));
      const keep = element("input");
      keep.type = "checkbox";
      keep.checked = true;
      keep.disabled = !mutable;
      const keepLabel = element("label", "Include this variant");
      keepLabel.prepend(keep);
      details.append(keepLabel);
      const input = field(details, "Exact channel text", draft.text, 6);
      input.readOnly = !mutable;
      edits.push({ alias: draft.alias, input, keep });
      if (mutable) {
        markDirty(input);
        markDirty(keep);
      }
      details.append(element("p", draft.rationale));
      for (const claim of draft.claims)
        details.append(
          element(
            "p",
            `Claim to verify: ${claim.claim}\n${claim.sources.map((source) => `${source.evidence}: “${source.quote}”`).join("\n")}`,
          ),
        );
      if (mutable)
        control(details, `Regenerate ${draft.alias} draft`, () =>
          mutate("preparation_regenerate", {
            ...base,
            stage: "draft",
            alias: draft.alias,
          }),
        );
    }
    if (job.critique) {
      details.append(
        element("h4", "Model-assisted editorial check"),
        element("p", job.critique.summary),
      );
      for (const issue of job.critique.issues)
        details.append(
          element(
            "p",
            `${issue.alias || "Strategy"} · ${issue.category}: ${issue.detail}`,
          ),
        );
    }
    const reported = job.usage.reduce(
      (total, call) => ({
        input: total.input + call.inputTokens,
        output: total.output + call.outputTokens,
        ms: total.ms + call.latencyMs,
      }),
      { input: 0, output: 0, ms: 0 },
    );
    details.append(
      element(
        "p",
        `Reported usage: ${reported.input} input / ${reported.output} output tokens; ${(reported.ms / 1000).toFixed(1)} seconds of model latency. Uncertain calls may still be billed.`,
      ),
    );
    if (mutable) {
      control(details, "Discard unsaved edits and refresh", async () => {
        delete article.dataset.dirty;
        await refresh();
      });
      if (edits.length)
        control(details, "Save edits and selected variants", async () => {
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
      control(details, "Regenerate strategy from current context form", () =>
        mutate("preparation_regenerate", {
          ...base,
          stage: "interpret",
          context: context(),
        }),
      );
      if (edits.length)
        control(details, "Check current saved drafts", () =>
          mutate("preparation_regenerate", { ...base, stage: "check" }),
        );
      control(details, "Reject this preparation", () =>
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
        details.append(label);
        const approve = control(
          details,
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
      details.append(label);
      const remove = control(details, "Remove exported preparation", () =>
        mutate("preparation_archive", {
          ...base,
          reviewDigest: exported.reviewDigest,
        }),
      );
      remove.disabled = true;
      confirmed.onchange = () => {
        remove.disabled = !confirmed.checked;
      };
      control(details, "Download private preparation export", async () => {
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
    return article;
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
      const apiKey = String(fd.get("apiKey"));
      form.elements.apiKey.value = "";
      try {
        await mutate("model_connect", {
          apiKey,
          maxJobsPerDay: Number(fd.get("maxJobsPerDay")),
          allowAgents: fd.has("allowAgents"),
          inputUsdPerMillion: nullable(fd.get("inputUsdPerMillion")),
          outputUsdPerMillion: nullable(fd.get("outputUsdPerMillion")),
          maxDailyUsd: nullable(fd.get("maxDailyUsd")),
        });
        show(
          "Workspace model key encrypted. API authentication is checked only when you request generation.",
        );
      } finally {
        form.elements.apiKey.value = "";
      }
    });
  };
  $("preparation-model-disconnect").onclick = () =>
    action(() => mutate("model_disconnect", {}));
  $("preparation-create").onsubmit = (event) => {
    event.preventDefault();
    const fd = new FormData(event.currentTarget);
    action(async () => {
      await mutate("preparation_create", {
        project: fd.get("project"),
        selection: {
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
      });
      show(
        "Preparation queued. Inspect strategy, evidence and drafts here; no publication or schedule was created.",
      );
    });
  };
  return {
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
