/*
 * sea_of_colours — eval command center.
 *
 * Pure vanilla JS controller for /evals. Drives three regions:
 *   - sidebar: every scenario in the harness (GET /api/evals/scenarios)
 *   - headline: the active scenario's pass conditions
 *   - past-runs strip + iframe: every past eval session for the
 *     active scenario (GET /api/evals/sessions?scenario=X), clicking
 *     a row swaps the iframe to /?session=<id>
 *
 * Deliberately framework-free — the existing watcher uses vanilla JS
 * + DOM and we don't want a build step on this codebase.
 */
"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const els = {
    counter: $("#ev-counter"),
    scenarioList: $("#ev-scenario-list"),
    headlineName: $("#ev-scenario-name"),
    headlineTags: $("#ev-scenario-tags"),
    headlineSummary: $("#ev-scenario-summary"),
    assertionList: $("#ev-assertion-list"),
    iframe: $("#ev-replay-iframe"),
    viewerCaption: $("#ev-viewer-caption"),
    runsList: $("#ev-runs-list"),
    runsCount: $("#ev-runs-count"),
    tabs: $$(".ev-tab"),
    tabPanels: $$(".ev-tab-panel"),
    transcript: $("#ev-transcript"),
  };

  /** @type {Array<any>} */
  let scenarios = [];
  /** @type {string|null} */
  let activeScenario = null;
  /** @type {any|null} */
  let activeRun = null;
  /** @type {string|null} */
  let activeSessionId = null;
  /** @type {"replay"|"transcript"} */
  let activeTab = "replay";
  /** Cache of transcript fetches keyed by session_id so flipping
   *  between replay and transcript doesn't hit the API twice. */
  const transcriptCache = new Map();

  // ───────────────────────────────────────────────────────────────
  // Fetch helpers
  // ───────────────────────────────────────────────────────────────
  async function fetchJSON(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`${url} → ${res.status}`);
    }
    return res.json();
  }

  // ───────────────────────────────────────────────────────────────
  // Sidebar
  // ───────────────────────────────────────────────────────────────
  function renderScenarioList() {
    els.scenarioList.innerHTML = "";
    if (!scenarios.length) {
      const li = document.createElement("li");
      li.className = "ev-scenario-loading dim";
      li.textContent = "(no scenarios registered)";
      els.scenarioList.appendChild(li);
      return;
    }
    for (const s of scenarios) {
      const li = document.createElement("li");
      li.dataset.scenario = s.name;
      if (s.name === activeScenario) li.classList.add("is-active");

      const name = document.createElement("span");
      name.className = "ev-scen-name";
      name.textContent = s.name;
      li.appendChild(name);

      const meta = document.createElement("span");
      meta.className = "ev-scen-meta";
      const aCount = document.createElement("span");
      aCount.textContent = `${s.assertions.length} assertion${s.assertions.length === 1 ? "" : "s"}`;
      meta.appendChild(aCount);
      for (const t of s.tags) {
        const tag = document.createElement("span");
        tag.className = "ev-scen-tag";
        tag.textContent = t;
        meta.appendChild(tag);
      }
      li.appendChild(meta);

      li.addEventListener("click", () => selectScenario(s.name));
      els.scenarioList.appendChild(li);
    }
    els.counter.textContent = `${scenarios.length} scenarios`;
  }

  // ───────────────────────────────────────────────────────────────
  // Headline
  // ───────────────────────────────────────────────────────────────
  function renderHeadline(s) {
    if (!s) {
      els.headlineName.textContent = "— select a scenario —";
      els.headlineTags.innerHTML = "";
      els.headlineSummary.textContent = "";
      els.assertionList.innerHTML = "";
      return;
    }
    els.headlineName.textContent = s.name;
    els.headlineTags.innerHTML = "";
    for (const t of s.tags) {
      const tag = document.createElement("span");
      tag.className = "ev-headline-tag";
      tag.textContent = t;
      els.headlineTags.appendChild(tag);
    }
    els.headlineSummary.textContent = s.summary;

    els.assertionList.innerHTML = "";
    for (const a of s.assertions) {
      const li = document.createElement("li");
      const marker = document.createElement("span");
      marker.className = "ev-assertion-marker";
      marker.textContent = "▸";
      const name = document.createElement("span");
      name.className = "ev-assertion-name";
      name.textContent = a.name;
      const desc = document.createElement("span");
      desc.className = "ev-assertion-desc";
      desc.textContent = a.describe;
      li.appendChild(marker);
      li.appendChild(name);
      li.appendChild(desc);
      els.assertionList.appendChild(li);
    }
  }

  // ───────────────────────────────────────────────────────────────
  // Past-runs strip
  // ───────────────────────────────────────────────────────────────
  function renderRunsLoading() {
    els.runsList.innerHTML = "";
    const span = document.createElement("span");
    span.className = "dim ev-runs-empty";
    span.textContent = "loading runs…";
    els.runsList.appendChild(span);
    els.runsCount.textContent = "";
  }

  function renderRuns(rows) {
    els.runsList.innerHTML = "";
    if (!rows.length) {
      const span = document.createElement("span");
      span.className = "dim ev-runs-empty";
      span.textContent = "— no recorded runs for this scenario —";
      els.runsList.appendChild(span);
      els.runsCount.textContent = "(0)";
      return;
    }
    els.runsCount.textContent = `(${rows.length})`;
    for (const r of rows) {
      const row = document.createElement("div");
      row.className = "ev-run-row";
      row.dataset.sessionId = r.session_id;
      if (r.session_id === activeSessionId) row.classList.add("is-active");

      const verdict = document.createElement("span");
      verdict.className = "ev-run-verdict";
      // Count distinct outcomes for a more honest verdict label —
      // any failing structural check is FAIL, otherwise PASS as
      // long as at least one structural check ran (post-night
      // sessions mark cell-state assertions as `passed: null`).
      const ass = r.per_assertion || [];
      const failing = ass.filter((a) => a.passed === false);
      const ran = ass.filter((a) => a.passed !== null);
      if (!ass.length) {
        verdict.classList.add("is-na");
        verdict.textContent = "—";
      } else if (failing.length) {
        verdict.classList.add("is-fail");
        verdict.textContent = "FAIL";
      } else if (!ran.length) {
        verdict.classList.add("is-na");
        verdict.textContent = "n/a";
      } else {
        verdict.classList.add("is-pass");
        verdict.textContent = "PASS";
      }

      const config = document.createElement("span");
      config.className = "ev-run-config";
      if (r.config === "unknown") config.classList.add("is-unknown");
      config.textContent = r.config || "—";

      // Agent identity: SOC_RED_REAPER_LIST / _GRID / etc. Falls
      // back to "(agent ?)" for sessions whose invocation row got
      // pruned or pre-dates the audit column being populated.
      const agent = document.createElement("span");
      agent.className = "ev-run-agent";
      if (r.agent_id) {
        agent.textContent = r.agent_id;
      } else {
        agent.classList.add("is-unknown");
        agent.textContent = "(agent ?)";
      }

      const drop = document.createElement("span");
      drop.className = "ev-run-drop";
      drop.textContent = r.drop ? `drop=(${r.drop[0]},${r.drop[1]})` : "(pre-deployed)";

      const summary = document.createElement("span");
      summary.className = "ev-run-summary";
      const failingNames = (r.per_assertion || []).filter((a) => a.passed === false).map((a) => a.name);
      const naNames = (r.per_assertion || []).filter((a) => a.passed === null).map((a) => a.name);
      if (failingNames.length) {
        summary.textContent = `failing: ${failingNames.join(", ")}`;
      } else if (naNames.length === (r.per_assertion || []).length && naNames.length) {
        summary.textContent = `${r.n_moves} moves · all assertions n/a`;
      } else {
        summary.textContent = `${r.n_moves} moves · ${r.n_probes} probe(s) · ${r.n_steps} step(s)`;
      }

      const sid = document.createElement("span");
      sid.className = "ev-run-id";
      sid.textContent = r.session_id.slice(0, 8);

      row.appendChild(verdict);
      row.appendChild(config);
      row.appendChild(agent);
      row.appendChild(drop);
      row.appendChild(summary);
      row.appendChild(sid);

      row.addEventListener("click", () => selectRun(r));
      els.runsList.appendChild(row);
    }

    // Auto-load the first run on scenario change so the iframe is
    // never blank when there's data available.
    if (!activeSessionId && rows.length) {
      selectRun(rows[0]);
    }
  }

  // ───────────────────────────────────────────────────────────────
  // Actions
  // ───────────────────────────────────────────────────────────────
  async function selectScenario(name) {
    activeScenario = name;
    activeSessionId = null;
    activeRun = null;
    $$(".ev-scenario-list li").forEach((li) => {
      li.classList.toggle("is-active", li.dataset.scenario === name);
    });
    const s = scenarios.find((x) => x.name === name);
    renderHeadline(s);
    els.iframe.src = "about:blank";
    els.viewerCaption.textContent = "— pick a past run below —";
    els.transcript.innerHTML = "";
    const empty = document.createElement("p");
    empty.className = "dim ev-transcript-empty";
    empty.textContent = "— pick a past run to see the agent transcript —";
    els.transcript.appendChild(empty);
    renderRunsLoading();
    try {
      const data = await fetchJSON(`/api/evals/sessions?scenario=${encodeURIComponent(name)}`);
      renderRuns(data.sessions || []);
    } catch (err) {
      els.runsList.innerHTML = "";
      const span = document.createElement("span");
      span.className = "dim ev-runs-empty";
      span.textContent = `error loading runs: ${String(err.message || err)}`;
      els.runsList.appendChild(span);
    }
    const url = new URL(window.location.href);
    url.searchParams.set("scenario", name);
    url.searchParams.delete("session");
    window.history.replaceState({}, "", url.toString());
  }

  function selectRun(r) {
    activeSessionId = r.session_id;
    activeRun = r;
    $$(".ev-run-row").forEach((row) => {
      row.classList.toggle("is-active", row.dataset.sessionId === r.session_id);
    });
    els.iframe.src = `/?session=${encodeURIComponent(r.session_id)}&watch=1`;
    const verdict = r.passed ? "PASS" : (r.per_assertion && r.per_assertion.length ? "FAIL" : "—");
    const dropStr = r.drop ? `drop=(${r.drop[0]},${r.drop[1]})` : "(pre-deployed)";
    const agentStr = r.agent_id ? ` · ${r.agent_id}` : "";
    els.viewerCaption.textContent = `${verdict} · ${r.config}${agentStr} · ${dropStr} · ${r.session_id.slice(0, 12)}…`;
    if (activeTab === "transcript") {
      renderTranscript(r);
    } else {
      // Pre-warm so flipping to the transcript tab is instant.
      void fetchTranscript(r).catch(() => {});
    }
    const url = new URL(window.location.href);
    url.searchParams.set("session", r.session_id);
    window.history.replaceState({}, "", url.toString());
  }

  // ───────────────────────────────────────────────────────────────
  // Tabs (REPLAY ⇄ TRANSCRIPT)
  // ───────────────────────────────────────────────────────────────
  function activateTab(name) {
    activeTab = name;
    els.tabs.forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.tab === name);
    });
    els.tabPanels.forEach((panel) => {
      const visible = panel.dataset.panel === name;
      panel.toggleAttribute("hidden", !visible);
      panel.classList.toggle("is-active", visible);
    });
    if (name === "transcript" && activeRun) {
      renderTranscript(activeRun);
    }
  }

  // ───────────────────────────────────────────────────────────────
  // Transcript fetch + render
  // ───────────────────────────────────────────────────────────────
  async function fetchTranscript(run) {
    const sid = run.session_id;
    if (transcriptCache.has(sid)) return transcriptCache.get(sid);
    // Pull the seat-under-test's invocations only — context-irrelevant
    // p2 turns would just be noise in the transcript view.
    const seat = run.player || "p1";
    const url = `/api/game/${encodeURIComponent(sid)}/agent-log?player=${encodeURIComponent(seat)}`;
    const data = await fetchJSON(url);
    transcriptCache.set(sid, data);
    return data;
  }

  function renderTranscript(run) {
    els.transcript.innerHTML = "";
    if (!run) return;
    const loading = document.createElement("p");
    loading.className = "dim ev-transcript-empty";
    loading.textContent = "loading transcript…";
    els.transcript.appendChild(loading);

    fetchTranscript(run).then((data) => {
      const invocations = (data && data.invocations) || [];
      // We're interested in the invocation that authored the policy
      // for this run — the LAST one on the policy day for the seat
      // under test. Earlier rows on the same day are
      // soc_save_rationale follow-ups or aborted attempts.
      const policyDay = run.policy_day != null ? run.policy_day : null;
      const candidates = policyDay != null
        ? invocations.filter((x) => Number(x.day) === Number(policyDay))
        : invocations;
      const inv = candidates.length ? candidates[candidates.length - 1] : invocations[invocations.length - 1];
      els.transcript.innerHTML = "";
      if (!inv) {
        const p = document.createElement("p");
        p.className = "dim ev-transcript-empty";
        p.textContent = "— no agent invocations recorded for this session —";
        els.transcript.appendChild(p);
        return;
      }
      els.transcript.appendChild(renderTranscriptBody(run, inv));
    }).catch((err) => {
      els.transcript.innerHTML = "";
      const p = document.createElement("p");
      p.className = "dim ev-transcript-empty";
      p.textContent = `error loading transcript: ${String(err.message || err)}`;
      els.transcript.appendChild(p);
    });
  }

  function renderTranscriptBody(run, inv) {
    const root = document.createElement("div");

    // Meta strip
    const meta = document.createElement("div");
    meta.className = "ev-tx-meta";
    meta.innerHTML = "";
    const metaParts = [
      ["agent",    inv.agent_id || "—"],
      ["runtime",  inv.runtime || "—"],
      ["seat",     inv.player || run.player || "—"],
      ["day",      inv.day != null ? String(inv.day) : "—"],
      ["status",   inv.status || "—"],
      ["elapsed",  inv.ms_elapsed != null ? `${(inv.ms_elapsed / 1000).toFixed(1)}s` : "—"],
    ];
    for (const [k, v] of metaParts) {
      const span = document.createElement("span");
      const label = document.createElement("span");
      label.className = "dim";
      label.textContent = `${k}=`;
      const val = document.createElement("strong");
      val.textContent = v;
      span.appendChild(label);
      span.appendChild(val);
      meta.appendChild(span);
    }
    root.appendChild(meta);

    // Section: PROMPT
    root.appendChild(renderSection("orchestrator prompt", inv.prompt, {
      missing: "(prompt not recorded — pre-Phase-2 session, or this turn was carried by the heuristic)",
      bylineFn: (text) => text ? `${text.length.toLocaleString()} chars` : "",
    }));

    // Section: THINKING
    // Prefer response_text (full untruncated Cortex output). Fall
    // back to the capped rationale only if response_text is missing
    // — e.g. heuristic turns store the algorithm's plain-English
    // summary as rationale and leave response_text empty.
    const thinking = inv.response_text || inv.rationale || "";
    const thinkingSource = inv.response_text ? "response_text (full)" : "rationale (2KB cap)";
    root.appendChild(renderSection("agent thinking", thinking, {
      missing: "(no rationale recorded)",
      bylineFn: (text) => text ? `${text.length.toLocaleString()} chars · ${thinkingSource}` : "",
    }));

    // Section: TOOL CALLS
    const tcArr = Array.isArray(inv.tool_calls) ? inv.tool_calls : [];
    const tcRendered = tcArr.length
      ? JSON.stringify(tcArr, null, 2)
      : "";
    root.appendChild(renderSection("tool calls", tcRendered, {
      missing: "(no tool calls recorded — agent likely fell back to heuristic)",
      bylineFn: () => tcArr.length ? `${tcArr.length} call(s)` : "",
    }));

    return root;
  }

  /**
   * One collapsed-able section in the transcript. ``body`` is the
   * raw text to render in the <pre>; when empty/null we surface
   * ``opts.missing`` in a muted state so the user knows the field
   * was checked, not just absent from the render. ``bylineFn`` is
   * called on the body to produce a right-aligned secondary label
   * (typically a size or count).
   */
  function renderSection(title, body, opts) {
    const sec = document.createElement("section");
    sec.className = "ev-tx-section";
    const head = document.createElement("header");
    const titleEl = document.createElement("strong");
    titleEl.textContent = `# ${title}`;
    head.appendChild(titleEl);
    if (opts && typeof opts.bylineFn === "function") {
      const byline = opts.bylineFn(body);
      if (byline) {
        const b = document.createElement("span");
        b.className = "ev-tx-byline";
        b.textContent = byline;
        head.appendChild(b);
      }
    }
    sec.appendChild(head);
    const pre = document.createElement("pre");
    pre.className = "ev-tx-pre";
    if (body) {
      pre.textContent = body;
    } else {
      pre.classList.add("is-missing");
      pre.textContent = (opts && opts.missing) || "(not recorded)";
    }
    sec.appendChild(pre);
    return sec;
  }

  // Wire tab buttons once at startup.
  els.tabs.forEach((tab) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  });

  // ───────────────────────────────────────────────────────────────
  // Boot
  // ───────────────────────────────────────────────────────────────
  async function boot() {
    try {
      const data = await fetchJSON("/api/evals/scenarios");
      scenarios = data.scenarios || [];
    } catch (err) {
      els.scenarioList.innerHTML = "";
      const li = document.createElement("li");
      li.className = "ev-scenario-loading dim";
      li.textContent = `error: ${String(err.message || err)}`;
      els.scenarioList.appendChild(li);
      return;
    }
    renderScenarioList();
    renderHeadline(null);

    // Honour ?scenario= / ?session= deep-links so users can share
    // direct pointers (e.g. "look at this failure for enemy_intercept").
    const params = new URLSearchParams(window.location.search);
    const wantedScenario = params.get("scenario");
    const wantedSession = params.get("session");
    let toSelect = wantedScenario;
    if (!toSelect && scenarios.length) toSelect = scenarios[0].name;
    if (toSelect) {
      await selectScenario(toSelect);
      if (wantedSession) {
        // We've already populated the runs list — find and click the
        // requested session row.
        const row = $$(".ev-run-row").find((r) => r.dataset.sessionId === wantedSession);
        if (row) row.click();
      }
    }
  }

  boot();
})();
