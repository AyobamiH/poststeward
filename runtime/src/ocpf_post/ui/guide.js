/* Read-only owner guide. No shell execution, provider requests or extra SSE stream. */
(function () {
  'use strict';
  const topicForNode = {
    sources: 'supply', observer: 'supply', buffer: 'admission', work: 'overview',
    editorial: 'supply', vault: 'supply', admission: 'admission', inventory: 'supply',
    service: 'schedule', scheduler: 'schedule', run_due: 'receipts', readback: 'verify',
    x: 'receipts', threads: 'receipts', linkedin: 'verify', learning: 'learning',
    measure_24: 'learning', measure_72: 'learning', measure_168: 'learning',
    engagement: 'replies', reply_worker: 'replies'
  };
  const effectNames = {
    READ_ONLY: 'Reads information', LOCAL_STATE_WRITE: 'Can write local evidence',
    FUTURE_CONSEQUENCE: 'Can change future work', EXTERNAL_PROVIDER_EFFECT: 'Can create an external effect',
    AUTHORITY_CHANGE: 'Can change permissions or runtime setup'
  };
  const stateNames = {inspect: 'Useful next check', waiting: 'Waiting is expected', review: 'Review without resending',
    external: 'Permission boundary', engineering: 'Performance investigation', informational: 'You can explore', unknown: 'Evidence needed'};
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function button(text, onClick, cls) {
    const node = element('button', cls || '', text);
    node.type = 'button';
    node.addEventListener('click', onClick);
    return node;
  }
  function details(label, content, cls) {
    const node = element('details', cls || '');
    node.append(element('summary', '', label), content);
    return node;
  }
  function safeTime(value) {
    const date = new Date(value);
    return Number.isFinite(+date) ? date.toLocaleString('en-GB', {timeZone: 'Europe/London', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short'}) : 'time not recorded';
  }
  function mount(api) {
    const drawer = document.getElementById('systemDrawer');
    const wrap = document.getElementById('systemDrawerWrap');
    const launcher = document.getElementById('systemButton');
    if (!drawer || !wrap || !launcher || drawer.dataset.guideMounted) return;
    drawer.dataset.guideMounted = 'true';
    drawer.classList.add('po-guide');
    launcher.textContent = 'Guide & doctor';
    launcher.classList.add('po-guide-launch');
    launcher.setAttribute('aria-label', 'Open Guide and doctor');
    let level = 'beginner';
    try { if (localStorage.getItem('post-once:guide:level') === 'advanced') level = 'advanced'; } catch (_) { /* optional preference */ }
    let tab = 'next', topic = 'overview', latest = null, displayed = null, checkedAt = null;
    let connected = false, open = false, pending = false, library = null, loading = false, loadFailed = false;
    let lastRevision = null, returnFocus = launcher, query = '', lastSelectionKey = '', previousItems = new Set();
    const dismissed = new Set(), inertBefore = new Map();
    let savedHtmlOverflow = '', savedBodyOverflow = '', lastNoticeState = '';

    const heading = document.getElementById('systemHeading');
    heading.textContent = 'A little guidance, right where you are';
    heading.tabIndex = -1;
    const header = drawer.querySelector('.drawer-head');
    const eyebrow = header && header.querySelector('.eyebrow');
    if (eyebrow) eyebrow.textContent = 'Guide & doctor';
    const subtitle = header && header.querySelector('p');
    if (subtitle) subtitle.textContent = 'Understand what’s happening, find a useful next check, or explore how things work.';
    const close = document.getElementById('closeSystem');
    close.textContent = 'Close'; close.setAttribute('aria-label', 'Close guide');

    const oldScroll = drawer.querySelector('.drawer-scroll');
    const legacy = details('Existing health evidence and navigation', element('div'), 'po-technical');
    legacy.id = 'po-existing-evidence';
    if (oldScroll) {
      legacy.lastChild.append(...Array.from(oldScroll.children));
      oldScroll.remove();
    }
    const tabs = element('div', 'po-tabs');
    tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Guide views');
    const tabButtons = {};
    const toolbar = element('div', 'po-toolbar');
    const freshness = element('div', 'po-freshness', 'Waiting for local evidence');
    const levelLabel = element('label', '', 'Detail ');
    const levelSelect = element('select'); levelSelect.id = 'po-guide-level';
    levelSelect.setAttribute('aria-label', 'Explanation detail');
    for (const [value, label] of [['beginner', 'Beginner'], ['advanced', 'Advanced']]) {
      const option = element('option', '', label); option.value = value; levelSelect.append(option);
    }
    levelSelect.value = level; levelLabel.append(levelSelect); toolbar.append(freshness, levelLabel);
    const scroll = element('div', 'po-scroll');
    const status = element('div', 'po-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const footer = element('div', 'po-footer');
    footer.append(status, element('div', '', 'This guide reads evidence and explains commands. It never runs them. Copying is not completion.'));
    const notice = element('div', 'po-note'); notice.hidden = true;
    const panels = {};
    for (const [id, text] of [['next', 'Next steps'], ['explore', 'Explore'], ['commands', 'All commands']]) {
      const node = button(text, () => switchTab(id));
      node.id = 'po-tab-' + id; node.setAttribute('role', 'tab'); node.setAttribute('aria-controls', 'po-panel-' + id);
      node.setAttribute('aria-selected', String(id === tab)); node.tabIndex = id === tab ? 0 : -1;
      const panel = element('section'); panel.id = 'po-panel-' + id;
      panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', node.id); panel.hidden = id !== tab;
      tabButtons[id] = node; panels[id] = panel; tabs.append(node);
    }
    scroll.append(notice, panels.next, panels.explore, panels.commands);
    drawer.append(tabs, toolbar, scroll, footer);
    tabs.addEventListener('keydown', event => {
      const keys = Object.keys(tabButtons), current = keys.indexOf(tab);
      let next;
      if (event.key === 'ArrowRight') next = keys[(current + 1) % keys.length];
      if (event.key === 'ArrowLeft') next = keys[(current + keys.length - 1) % keys.length];
      if (event.key === 'Home') next = keys[0];
      if (event.key === 'End') next = keys[keys.length - 1];
      if (next) { event.preventDefault(); switchTab(next); tabButtons[next].focus(); }
    });

    async function loadLibrary() {
      if (library || loading) return;
      loading = true; loadFailed = false;
      try {
        const response = await fetch('/api/guide/commands', {cache: 'no-store'});
        if (!response.ok) throw new Error('reference');
        const value = await response.json();
        if (!Array.isArray(value.commands) || !Array.isArray(value.topics)) throw new Error('reference');
        library = value;
      } catch (_) { loadFailed = true; }
      finally { loading = false; if (open) render(); }
    }
    function say(message) { status.textContent = message; }
    async function copy(command, output, node) {
      try {
        if (!navigator.clipboard || !window.isSecureContext) throw new Error('clipboard');
        await navigator.clipboard.writeText(command);
        say('Copied. Run it in your Post-Once checkout in WSL. Nothing has run here.');
        node.textContent = 'Copied';
      } catch (_) {
        const selection = window.getSelection(), range = document.createRange();
        range.selectNodeContents(output); selection.removeAllRanges(); selection.addRange(range);
        say('Clipboard access is unavailable. The command is selected; press Ctrl+C, then paste it in WSL.');
      }
    }
    function commandBlock(action, parent, label) {
      if (!action || typeof action.command !== 'string') return;
      const output = element('code', 'po-command', action.command);
      const copyButton = button(label || 'Copy inspection command', () => copy(action.command, output, copyButton), 'po-primary');
      parent.append(element('span', 'po-effect', action.effect || 'Inspection only'), output);
      const actions = element('div', 'po-actions'); actions.append(copyButton); parent.append(actions);
    }
    function switchTab(next) {
      tab = next;
      for (const [id, node] of Object.entries(tabButtons)) {
        node.setAttribute('aria-selected', String(id === next)); node.tabIndex = id === next ? 0 : -1;
        panels[id].hidden = id !== next;
      }
      render();
    }
    function sourceDetails(card) {
      const content = element('div', 'po-expect');
      content.append(element('p', '', card.expect || 'Inspect the returned evidence before deciding what to do next.'));
      const evidence = element('pre', '', JSON.stringify(card.evidence || {}, null, 2));
      const technical = element('div');
      technical.append(element('p', 'po-technical', 'Evidence: ' + (card.evidence_path || 'current snapshot')),
        element('p', 'po-technical', 'Snapshot: ' + String(displayed && displayed.revision || 'not available')), evidence);
      const fold = details('Why this is suggested · evidence', technical);
      fold.open = level === 'advanced'; content.append(fold);
      return content;
    }
    function renderCard(card, primary) {
      const article = element('article', 'po-card' + (primary ? ' po-card-primary' : ''));
      article.dataset.suggestionId = card.id;
      article.append(element('div', 'po-kicker', primary ? 'Start here · ' + (stateNames[card.state] || 'Inspection') : stateNames[card.state] || 'Another useful check'),
        element('h3', '', card.title), element('p', 'po-meaning', card.meaning));
      if (card.caution) article.append(element('p', 'po-caution', card.caution));
      article.append(element('p', '', card.next_step));
      commandBlock(card.action, article);
      article.append(details('What should I look for?', sourceDetails(card)));
      const actions = element('div', 'po-actions');
      actions.append(button('Understand this part', () => { topic = card.topic || 'overview'; switchTab('explore'); }));
      if (card.priority >= 20 && !['unknown', 'review'].includes(card.state)) {
        actions.append(button('Set aside for this visit', () => { dismissed.add(JSON.stringify([card.id, card.evidence])); renderNext(); say('Set aside in this browser visit only. The underlying condition is unchanged.'); }, 'po-dismiss'));
      }
      article.append(actions); return article;
    }
    function currentSelection() { try { return api.selection() || null; } catch (_) { return null; } }
    function contextCard() {
      const selection = currentSelection();
      if (!selection) return null;
      const context = selection.type === 'event' && displayed && displayed.guide && displayed.guide.contexts && displayed.guide.contexts[selection.value && selection.value.id];
      const nodeId = selection.type === 'node' ? (typeof selection.value === 'string' ? selection.value : selection.value && selection.value.id) : null;
      const contextTopic = nodeId ? topicForNode[nodeId] || 'overview' : 'receipts';
      const section = element('article', 'po-card po-selection');
      section.append(element('div', 'po-kicker', 'The part you selected'), element('h3', '', nodeId ? 'Let’s understand ' + nodeId.replaceAll('_', ' ') : 'Follow this publication’s evidence'));
      if (context) {
        section.append(element('p', 'po-meaning', context.boundary));
        section.append(element('pre', '', JSON.stringify(context.identity, null, 2)));
        commandBlock(context.action, section);
      } else if (selection.type === 'event') {
        section.append(element('p', '', 'This beat does not have enough matched identity information for an exact command. We won’t guess an account or campaign.'));
      }
      section.append(button('Explain this stage', () => { topic = contextTopic; switchTab('explore'); }));
      return section;
    }
    function renderNext() {
      const panel = panels.next;
      // Preserve the original health evidence elements: the cockpit still updates them.
      legacy.remove(); panel.replaceChildren();
      const context = contextCard(); if (context) panel.append(context);
      const guide = displayed && displayed.guide;
      if (!guide || !Array.isArray(guide.items)) {
        panel.append(element('p', 'po-empty', 'Waiting for the first local observation. You can still explore the system or browse commands. No action is needed just to load this guide.'));
      } else {
        panel.append(element('p', 'po-intro', 'One useful check at a time. These are evidence-based suggestions, not automatic fixes.'));
        const cards = guide.items.filter(card => !dismissed.has(JSON.stringify([card.id, card.evidence])));
        if (cards.length) panel.append(renderCard(cards[0], true));
        if (cards.length > 1) {
          const rest = element('div'); cards.slice(1).forEach(card => rest.append(renderCard(card, false)));
          panel.append(details('Other checks to consider (' + (cards.length - 1) + ')', rest));
        }
        if (dismissed.size) panel.append(button('Show suggestions set aside this visit', () => { dismissed.clear(); renderNext(); }));
        if (guide.additional_count) panel.append(element('p', 'po-technical', guide.additional_count + ' further findings are available in work status.'));
      }
      panel.append(legacy);
    }
    function libraryUnavailable(panel) {
      if (library) return false;
      panel.append(element('p', 'po-empty', loadFailed ? 'The command reference isn’t available right now. Your publishing work is not affected.' : 'Loading the local command reference…'));
      if (loadFailed) panel.append(button('Try loading the reference again', () => loadLibrary()));
      return true;
    }
    function renderExplore() {
      const panel = panels.explore; panel.replaceChildren();
      if (libraryUnavailable(panel)) return;
      const selected = library.topics.find(item => item.id === topic) || library.topics[0];
      const card = element('article', 'po-card po-card-primary');
      card.append(element('div', 'po-kicker', 'Follow your curiosity'), element('h3', '', selected.question), element('p', 'po-meaning', selected.answer));
      if (selected.id === 'today') {
        card.append(element('p', '', 'For today’s full publication and vault-origin report, run this from the checkout. It writes a private report, not a social post.'));
        commandBlock({command: 'bash scripts/owner-daily.sh', effect: 'Creates report files only'}, card, 'Copy report command');
      } else commandBlock(selected.action, card);
      card.append(details('How to read the result', element('p', 'po-expect', selected.expect)));
      panel.append(card, element('p', 'po-intro', 'What would you like to understand next?'));
      library.topics.filter(item => item.id !== topic).forEach(item => panel.append(button(item.question, () => { topic = item.id; renderExplore(); scroll.scrollTop = 0; panels.explore.querySelector('h3').tabIndex = -1; panels.explore.querySelector('h3').focus(); }, 'po-question')));
    }
    function renderCommands() {
      const panel = panels.commands; panel.replaceChildren();
      if (libraryUnavailable(panel)) return;
      const label = element('label', '', 'Find a command or describe what you’re curious about'); label.htmlFor = 'po-command-search';
      const input = element('input', 'po-search'); input.id = 'po-command-search'; input.type = 'search'; input.value = query;
      input.placeholder = 'Try “vault”, “receipt” or “schedule”';
      const count = element('p', 'po-count'), list = element('div');
      panel.append(label, input, count, element('p', 'po-intro', 'Commands are reference cards. Copying opens their help, not an action. Choose Advanced above to see the full catalogue.'), list);
      function results() {
        query = input.value;
        const words = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
        const matches = library.commands.filter(row => (level === 'advanced' || row.level === 'beginner') && words.every(word => (row.path + ' ' + row.summary + ' ' + (row.notes || '')).toLowerCase().includes(word)));
        count.textContent = matches.length + ' of ' + library.commands.length + ' commands · ' + (level === 'beginner' ? 'beginner inspection view' : 'all commands, including changes');
        list.replaceChildren();
        matches.forEach(row => {
          const body = element('div');
          body.append(element('span', 'po-effect', effectNames[row.consequence] || row.consequence), element('p', '', row.summary));
          if (row.consequence !== 'READ_ONLY') body.append(element('p', 'po-caution', 'Not a routine status check. ' + (row.safe_form ? 'Inspection form: ' + row.safe_form + '.' : 'No preview is declared. Read the help before using it.')));
          if (row.notes) body.append(element('p', '', row.notes));
          commandBlock({command: row.help_command, effect: 'Opens help only'}, body, 'Copy help command');
          const args = element('div', 'po-args');
          (row.arguments || []).forEach(arg => args.append(element('div', 'po-arg', ((arg.flags || []).join(', ') || arg.name) + (arg.required ? ' · required' : '') + (arg.choices ? ' · ' + arg.choices.join(' / ') : '') + (arg.help ? '\n' + arg.help : ''))));
          if (row.arguments && row.arguments.length) body.append(details('Options and required values', args));
          list.append(details('./ocpf-post ' + row.path, body, 'po-library-row'));
        });
        if (!matches.length) list.append(element('p', 'po-empty', 'No matching command in this view. Try a shorter term, or switch to Advanced for the complete catalogue. We won’t invent a command for an unmatched request.'));
      }
      input.addEventListener('input', results); results();
    }
    function render() {
      if (tab === 'next') renderNext();
      if (tab === 'explore') renderExplore();
      if (tab === 'commands') renderCommands();
      updateFreshness();
    }
    function updateFreshness() {
      const shownAt = pending && displayed ? displayed.observed_at : checkedAt;
      const age = shownAt ? (Date.now() - new Date(shownAt).getTime()) / 1000 : Infinity;
      const stale = !Number.isFinite(age) || age > 120 || age < -5 || !connected;
      freshness.textContent = shownAt ? (pending ? 'Reading observation · ' : stale ? 'Last observation · ' : 'Observed · ') + safeTime(shownAt) : 'Waiting for local evidence';
      notice.hidden = !stale && !pending;
      notice.dataset.stale = String(stale);
      const noticeState = String(stale) + ':' + String(pending) + ':' + String(connected);
      if (noticeState === lastNoticeState) return;
      lastNoticeState = noticeState;
      notice.replaceChildren();
      if (pending) {
        notice.append(element('p', '', 'New evidence is available. Your current reading position has been kept.'));
        notice.append(button('Use the latest evidence', () => applyLatest(true)));
      } else if (stale) {
        notice.append(element('p', '', connected ? 'This observation is older than two minutes. Treat it as context, not a fresh diagnosis.' : 'Waiting for the live connection. Previously shown evidence may be out of date; nothing needs to be rerun just because it is still visible.'));
      }
    }
    function applyLatest(announce) {
      if (!latest) return;
      const nextIds = new Set((latest.guide && latest.guide.items || []).map(item => item.id));
      const removed = Array.from(previousItems).filter(id => !nextIds.has(id));
      displayed = latest; pending = false; previousItems = nextIds;
      render();
      if (announce) say(removed.length ? 'The checks changed. ' + removed.length + ' previous suggestion(s) are no longer in this view. That alone is not proof of a repair.' : 'Showing the latest observed evidence.');
    }
    function receive(snapshot) {
      if (!snapshot || typeof snapshot !== 'object') return;
      latest = snapshot; connected = true;
      checkedAt = snapshot.observed_at || checkedAt;
      if (lastRevision === snapshot.revision && displayed) { updateFreshness(); return; }
      lastRevision = snapshot.revision;
      if (open && displayed) { pending = true; updateFreshness(); }
      else applyLatest(false);
    }
    function selectionChanged() {
      const selection = currentSelection();
      const key = JSON.stringify(selection && {type: selection.type, value: selection.type === 'event' ? selection.value && selection.value.id : selection.value});
      if (key === lastSelectionKey) return;
      lastSelectionKey = key;
      if (open && tab === 'next') renderNext();
    }
    function setBackgroundInert() {
      let child = wrap;
      while (child && child.parentElement) {
        for (const sibling of child.parentElement.children) {
          if (sibling !== child && !['SCRIPT', 'STYLE', 'LINK'].includes(sibling.tagName)) {
            inertBefore.set(sibling, sibling.inert); sibling.inert = true;
          }
        }
        if (child.parentElement === document.body) break;
        child = child.parentElement;
      }
    }
    function openingChanged() {
      const showing = wrap.classList.contains('open');
      if (showing === open) return;
      open = showing;
      if (showing) {
        setBackgroundInert(); savedHtmlOverflow = document.documentElement.style.overflow; savedBodyOverflow = document.body.style.overflow;
        document.documentElement.style.overflow = 'hidden'; document.body.style.overflow = 'hidden';
        if (!displayed) applyLatest(false);
        render(); loadLibrary(); heading.focus();
      } else {
        for (const [node, wasInert] of inertBefore) node.inert = wasInert;
        inertBefore.clear(); document.documentElement.style.overflow = savedHtmlOverflow; document.body.style.overflow = savedBodyOverflow;
        if (returnFocus && returnFocus.isConnected) returnFocus.focus();
        applyLatest(false);
      }
    }
    launcher.addEventListener('click', () => { returnFocus = launcher; }, true);
    const selectionLaunch = button('Help with this selection', event => { returnFocus = event.currentTarget; tab = 'next'; switchTab('next'); api.open(); }, 'mini po-guide-launch');
    const inspector = document.querySelector('.inspector-actions'); if (inspector) inspector.append(selectionLaunch);
    const stages = {opStageSupply: 'supply', opStageAdmission: 'admission', opStageAutomaticSchedule: 'schedule', opStageExecute: 'receipts', opStageProvider: 'receipts', opStageVerify: 'verify'};
    for (const [id, target] of Object.entries(stages)) {
      const node = document.getElementById(id); if (!node) continue;
      node.tabIndex = 0; node.setAttribute('role', 'button'); node.setAttribute('aria-label', 'Understand ' + target);
      const launch = () => { returnFocus = node; topic = target; switchTab('explore'); api.open(); };
      node.addEventListener('click', launch);
      node.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); launch(); } });
    }
    levelSelect.addEventListener('change', () => {
      level = levelSelect.value === 'advanced' ? 'advanced' : 'beginner';
      try { localStorage.setItem('post-once:guide:level', level); } catch (_) { /* optional preference */ }
      render();
    });
    drawer.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); api.close(); return; }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(drawer.querySelectorAll('button, input, select, summary, a[href], [tabindex="0"]')).filter(node => !node.disabled && node.getClientRects().length && !node.closest('[hidden]'));
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === heading)) { event.preventDefault(); last && last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first && first.focus(); }
    });
    new MutationObserver(openingChanged).observe(wrap, {attributes: true, attributeFilter: ['class']});
    const updateTarget = document.getElementById('updatedBadge');
    if (updateTarget) new MutationObserver(() => receive(api.snapshot())).observe(updateTarget, {childList: true, subtree: true, characterData: true});
    const selectionTarget = document.getElementById('inspectTitle');
    if (selectionTarget) new MutationObserver(selectionChanged).observe(selectionTarget, {childList: true, subtree: true, characterData: true});
    api.stream.addEventListener('snapshot', () => receive(api.snapshot()));
    api.stream.addEventListener('heartbeat', event => {
      try {
        const heartbeat = JSON.parse(event.data);
        if (latest && heartbeat.revision === latest.revision && heartbeat.observed_at) {
          checkedAt = heartbeat.observed_at; connected = true; updateFreshness();
        }
      } catch (_) { /* An incomplete heartbeat is not new evidence. */ }
    });
    api.stream.addEventListener('error', () => { connected = false; updateFreshness(); });
    setInterval(() => { if (open) updateFreshness(); }, 15000); // Freshness label only; never fetch or rebuild.
    receive(api.snapshot());
    openingChanged();
  }
  window.PostOnceGuide = {mount: mount};
})();
