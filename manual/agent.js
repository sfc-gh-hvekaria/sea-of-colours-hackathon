/* sea_of_colours · agent & harness guide — interactive.
 *
 * Every number, coordinate and quoted line on this page comes from
 * `agent-data.js`, which is generated off ONE real turn (snapshot
 * SNAP_408ddd46_d4_p1, day 4 of 7) by scripts/export_agent_guide_data.py.
 * Nothing here is illustrative or hand-typed; if the turn changes, the page
 * changes with it.
 */
(() => {
  "use strict";

  const D = window.AG_DATA;
  if (!D) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      '<pre style="color:#ff8585;padding:20px">agent-data.js did not load — ' +
        "run: SOC_BACKEND=snowflake python scripts/export_agent_guide_data.py</pre>",
    );
    return;
  }
  const BOARD = D.board;
  const CARD = D.card;
  const HARNESS = D.harness || { modules: {}, total_lines: 0 };
  const W = BOARD.width;
  const H = BOARD.height;

  /* ══════════════════════════════════════════════════════════════════
   * BOARD PRIMITIVES
   *
   * Ported verbatim from manual.js lines 11-19 and 46-217 (paintTile,
   * renderBoard and the mutation helpers). manual.js is a single closed IIFE
   * with no exports, so this is a copy rather than an import — and a
   * deliberate one: the manual has thirteen tabs of animation depending on
   * that code, and refactoring it into a shared module to save ~170 lines
   * would put all of them at risk. If the engine's paint rules ever change,
   * both copies need the edit; paintTile is the one to watch, since it
   * mirrors app.js computeParcelPaintFallback and render.py cell_visual.
   * ══════════════════════════════════════════════════════════════════ */

  const TILE = { EMPTY: 0, GREEN: 1, RED: 2, BLUE: 3 };
  const VOID = "rgb(14,11,22)";
  const RED_FG = "rgb(255,0,0)";
  const BLUE_FG = "rgb(59,143,224)";
  const GREEN_FG = "rgb(63,185,80)";
  const CH_LIGHT = "\u2591\u2591"; // ░░
  const CH_MED = "\u2592\u2592"; // ▒▒
  const CH_HEAVY = "\u2593\u2593"; // ▓▓
  const CH_FULL = "\u2588\u2588"; // ██
  const GLYPH_HARVESTER = "X";
  const GLYPH_PROBE = "\u00B7"; // ·

  function tierOf(purity) {
    if (purity >= 255) return "pure";
    if (purity >= 151) return "mass";
    if (purity >= 51) return "vein";
    return "trace";
  }

  function paintTile(tile, purity) {
    const p = Math.max(0, Math.min(255, purity | 0));
    if (tile === TILE.EMPTY) return { fg: VOID, bg: VOID, ch: CH_FULL };
    if (tile === TILE.GREEN) return { fg: GREEN_FG, bg: VOID, ch: CH_FULL };
    const fg = tile === TILE.RED ? RED_FG : BLUE_FG;
    let ch;
    if (p >= 255) ch = CH_FULL;
    else if (p >= 151) ch = CH_HEAVY;
    else if (p >= 51) ch = CH_MED;
    else ch = CH_LIGHT;
    return { fg, bg: VOID, ch };
  }

  function trailGlyph(n) {
    if (n >= 4) return CH_FULL;
    if (n === 3) return CH_HEAVY;
    if (n === 2) return CH_MED;
    return CH_LIGHT;
  }

  function renderBoard(host, cells, opts = {}) {
    host.innerHTML = "";
    const cols = opts.cols || cells.length;
    const rows = opts.rows || Math.ceil(cells.length / cols);
    const cellW = opts.cellW != null ? opts.cellW : 24;
    const cellH = opts.cellH != null ? opts.cellH : 24;
    const board = document.createElement("div");
    board.className = "mn-board";
    board.style.setProperty("--cell-w", `${cellW}px`);
    board.style.setProperty("--cell-h", `${cellH}px`);
    const glyphFontPx = Math.round(cellW * 0.88);
    board.style.setProperty("--cell-font", `${glyphFontPx}px`);
    board.style.setProperty("--cell-line-height", (cellH / glyphFontPx).toFixed(3));
    board.style.width = `${cols * cellW}px`;
    board.style.height = `${rows * cellH}px`;

    for (let i = 0; i < cells.length; i++) {
      const c = cells[i] || { tile: TILE.EMPTY, purity: 0 };
      const paint = paintTile(c.tile, c.purity);
      const cellEl = document.createElement("div");
      cellEl.className = "mn-cell";
      cellEl.dataset.idx = String(i);
      cellEl.style.left = `${(i % cols) * cellW}px`;
      cellEl.style.top = `${Math.floor(i / cols) * cellH}px`;
      cellEl.style.width = `${cellW}px`;
      cellEl.style.height = `${cellH}px`;

      const terr = document.createElement("span");
      terr.className = "mn-cell-terrain";
      terr.style.background = paint.bg;
      terr.style.color = paint.fg;
      terr.textContent = paint.ch;
      cellEl.appendChild(terr);

      const trail = document.createElement("span");
      trail.className = "mn-cell-trail";
      if (c.trail) {
        trail.textContent = trailGlyph(c.trail);
        trail.classList.add("is-on");
      }
      cellEl.appendChild(trail);

      const ent = document.createElement("span");
      ent.className = "mn-cell-entity";
      if (c.entity) ent.textContent = c.entity;
      cellEl.appendChild(ent);

      const fog = document.createElement("span");
      fog.className = "mn-cell-fog";
      fog.textContent = CH_LIGHT;
      if (c.fog) cellEl.classList.add("is-fogged");
      else fog.classList.add("is-clear");
      cellEl.appendChild(fog);

      board.appendChild(cellEl);
    }
    host.appendChild(board);
    return board;
  }

  function setTerrain(cellEl, tile, purity) {
    const paint = paintTile(tile, purity);
    const t = cellEl.querySelector(".mn-cell-terrain");
    t.style.background = paint.bg;
    t.style.color = paint.fg;
    t.textContent = paint.ch;
  }

  function setEntity(cellEl, glyph, seatVar = "--seat-p1") {
    const e = cellEl.querySelector(".mn-cell-entity");
    e.textContent = glyph || "";
    e.style.color = glyph ? `var(${seatVar})` : "";
    e.classList.remove(
      "mn-cell-entity--probe",
      "mn-cell-entity--probe-life-1",
      "mn-cell-entity--probe-life-2",
    );
  }

  function setProbe(cellEl, nightsRemaining, seatVar = "--seat-p1") {
    setEntity(cellEl, GLYPH_PROBE, seatVar);
    const e = cellEl.querySelector(".mn-cell-entity");
    e.classList.add("mn-cell-entity--probe");
    const rings = Math.max(0, Math.min(2, (nightsRemaining | 0) - 1));
    if (rings > 0) e.classList.add(`mn-cell-entity--probe-life-${rings}`);
  }

  function bumpTrail(cellEl, currentByIdx, idx) {
    const n = (currentByIdx[idx] || 0) + 1;
    currentByIdx[idx] = n;
    const t = cellEl.querySelector(".mn-cell-trail");
    t.textContent = trailGlyph(n);
    t.classList.add("is-on");
  }

  function setFog(cellEl, on) {
    const f = cellEl.querySelector(".mn-cell-fog");
    if (on) {
      f.classList.remove("is-clear");
      cellEl.classList.add("is-fogged");
    } else {
      f.classList.add("is-clear");
      cellEl.classList.remove("is-fogged");
    }
  }

  /* ══════════════════════════════════════════════════════════════════
   * GUIDE HELPERS
   * ══════════════════════════════════════════════════════════════════ */

  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) =>
    String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const nfmt = (n) => Number(n).toLocaleString("en-US");

  /** Engine truth for one cell, as [tile, purity]. */
  function truthAt(x, y) {
    return BOARD.truth[y * W + x] || [0, 0];
  }

  /**
   * A viewport onto the 40x28 board.
   *
   * Full-board renders at small cells are good for "where am I", but the
   * glyph texture that carries tier only reads at ~20px+, so the walk scenes
   * crop to a window and draw that window at a legible size. `Crop` maps
   * board coords to the local index either way, so scene code never has to
   * know which mode it is in.
   */
  class Crop {
    constructor({ x0 = 0, y0 = 0, x1 = W - 1, y1 = H - 1 } = {}) {
      this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1;
      this.cols = x1 - x0 + 1;
      this.rows = y1 - y0 + 1;
    }
    has(x, y) {
      return x >= this.x0 && x <= this.x1 && y >= this.y0 && y <= this.y1;
    }
    idx(x, y) {
      return this.has(x, y) ? (y - this.y0) * this.cols + (x - this.x0) : -1;
    }
  }

  /** Build the cell array for a crop, straight from engine truth. */
  function truthCells(crop, { fog = false } = {}) {
    const out = [];
    for (let y = crop.y0; y <= crop.y1; y++) {
      for (let x = crop.x0; x <= crop.x1; x++) {
        const [tile, purity] = truthAt(x, y);
        out.push({ tile, purity, fog });
      }
    }
    return out;
  }

  /** All cells inside a probe's vision disk. Euclidean r=4, per game/tuning. */
  const PROBE_R = 4;
  function disk(cx, cy, r = PROBE_R) {
    const out = [];
    for (let y = cy - r; y <= cy + r; y++) {
      for (let x = cx - r; x <= cx + r; x++) {
        if (x < 0 || y < 0 || x >= W || y >= H) continue;
        if ((x - cx) ** 2 + (y - cy) ** 2 <= r * r) out.push([x, y]);
      }
    }
    return out;
  }

  function cellOf(board, crop, x, y) {
    const i = crop.idx(x, y);
    return i < 0 ? null : board.children[i];
  }

  function mark(board, crop, x, y, cls) {
    const el = cellOf(board, crop, x, y);
    if (el) el.classList.add(cls);
  }

  function clearMarks(board, ...classes) {
    for (const el of board.children) el.classList.remove(...classes);
  }

  /** Parse "(31,18)" as used throughout the card. */
  function parseAt(s) {
    const m = /\((-?\d+)\s*,\s*(-?\d+)\)/.exec(String(s));
    return m ? [parseInt(m[1], 10), parseInt(m[2], 10)] : null;
  }

  /* ── text tinting ──────────────────────────────────────────────── */

  /** Minimal JSON tinting — enough to read structure, no tokeniser. */
  function tintJSON(text) {
    return esc(text)
      .replace(/&quot;/g, '"')
      .replace(/"([^"\\]*(?:\\.[^"\\]*)*)"(\s*:)/g,
        '<span class="tok-key">"$1"</span><span class="tok-punc">$2</span>')
      .replace(/:\s*"([^"\\]*(?:\\.[^"\\]*)*)"/g,
        ': <span class="tok-str">"$1"</span>')
      .replace(/\b(-?\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>')
      .replace(/\b(true|false|null)\b/g, '<span class="tok-num">$1</span>');
  }

  /** Prompt tinting: section rules and bracketed option IDs stand out. */
  function tintPrompt(text) {
    return esc(text)
      .replace(/^(===.*===)$/gm, '<span class="tok-hd">$1</span>')
      .replace(/^([A-Z][A-Z0-9 &/·—-]{6,})$/gm, '<span class="tok-hd">$1</span>')
      .replace(/\[([A-Z][A-Z0-9_#]+)\]/g, '<span class="tok-str">[$1]</span>')
      .replace(/\((\d+),(\d+)\)/g, '<span class="tok-num">($1,$2)</span>');
  }

  function pane(title, sub, body, { cls = "is-mid", tint = null, right = "" } = {}) {
    const html = tint === "json" ? tintJSON(body)
      : tint === "prompt" ? tintPrompt(body)
      : esc(body);
    return (
      '<div class="ag-pane">' +
      '<div class="ag-pane-head"><span class="t">' + esc(title) + "</span>" +
      (sub ? '<span class="s">' + esc(sub) + "</span>" : "") +
      (right ? '<span class="r">' + esc(right) + "</span>" : "") +
      "</div>" +
      '<pre class="' + cls + '">' + html + "</pre></div>"
    );
  }

  function takeaway(text) {
    return '<div class="ag-takeaway"><span class="lbl">WHAT TO TAKE AWAY</span><p>' +
      text + "</p></div>";
  }

  /**
   * A stepper: PREV / NEXT / PLAY / RESTART over an array of steps, using the
   * manual's own control vocabulary so the two pages feel like one product.
   */
  function stepper(host, steps, apply, { autoMs = 900, statusId = null } = {}) {
    let i = 0;
    let timer = null;
    const bar = document.createElement("div");
    bar.className = "mn-controls";
    bar.innerHTML =
      '<button class="mn-btn" data-a="prev">[ ◄ PREV ]</button>' +
      '<button class="mn-btn" data-a="next">[ NEXT ► ]</button>' +
      '<button class="mn-btn" data-a="play">[ ⏵ PLAY ]</button>' +
      '<button class="mn-btn" data-a="restart">[ ⏮ RESTART ]</button>' +
      '<span class="dim mn-status"></span>';
    host.appendChild(bar);
    const status = bar.querySelector(".mn-status");
    const playBtn = bar.querySelector('[data-a="play"]');

    function show(n) {
      i = Math.max(0, Math.min(steps.length - 1, n));
      apply(i, steps[i]);
      status.textContent = `# ${i + 1}/${steps.length} · ${steps[i].label || ""}`;
      if (statusId) {
        const ext = document.getElementById(statusId);
        if (ext) ext.textContent = steps[i].sub || "";
      }
    }
    function stop() {
      if (timer) clearInterval(timer);
      timer = null;
      playBtn.classList.remove("is-on");
      playBtn.textContent = "[ ⏵ PLAY ]";
    }
    function play() {
      if (timer) return stop();
      playBtn.classList.add("is-on");
      playBtn.textContent = "[ ⏸ PAUSE ]";
      timer = setInterval(() => {
        if (i >= steps.length - 1) return stop();
        show(i + 1);
      }, autoMs);
    }
    bar.addEventListener("click", (e) => {
      const a = e.target.closest("[data-a]");
      if (!a) return;
      const act = a.dataset.a;
      if (act === "play") return play();
      stop();
      if (act === "prev") show(i - 1);
      if (act === "next") show(i + 1);
      if (act === "restart") show(0);
    });
    show(0);
    return { show, stop };
  }

  /* ══════════════════════════════════════════════════════════════════
   * NAV — scroll spy
   * ══════════════════════════════════════════════════════════════════ */

  function initNav() {
    const links = [...document.querySelectorAll(".ag-nav a")];
    const byId = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          links.forEach((a) => a.classList.remove("is-current"));
          const a = byId.get(e.target.id);
          if (a) a.classList.add("is-current");
        }
      },
      { rootMargin: "-15% 0px -70% 0px" },
    );
    document.querySelectorAll(".ag-scene").forEach((s) => obs.observe(s));
  }

  /* ══════════════════════════════════════════════════════════════════
   * SCENES — each fills one #sNN-mount left by agent.html
   * ══════════════════════════════════════════════════════════════════ */

  const scenes = [];
  /** Register a section builder. Each fills the #sNN-body left by agent.html. */
  function scene(fn) {
    scenes.push(fn);
  }

  // ═══ SCENE BUILDERS ══════════════════════════════════════════════
  // Add new sections ABOVE the runner at the foot of this file.

  /* ── 00 · the novice walkthrough ───────────────────────────────── */

  // Blue = the game, green = our Python, amber = the model. Established here
  // and honoured by every diagram further down the page.
  const PHASE = { engine: "is-engine", harness: "is-harness", model: "is-model" };

  const TURN = [
    {
      ph: "engine", who: "GAME ENGINE", short: "Night falls",
      title: "The game is running",
      body:
        "Sea of Colours is turn-based. A season is seven nights, and on each " +
        "one every house submits its orders at the same time, then the engine " +
        "resolves them all together. Nobody moves in real time and nobody " +
        "watches anybody else move.<p>The board is 40 by 28 cells of red, " +
        "blue, green and empty ground, and it starts completely hidden. Here " +
        "it is on night four as our seat, p1, has it — one small lit circle " +
        "and a great deal of dark.</p>",
      mount: "board-a",
    },
    {
      ph: "engine", who: "GAME ENGINE", short: "Issues game state",
      title: "The engine issues a game state",
      body:
        "The engine holds the true board, but it never hands that over. It " +
        "builds a private copy for each seat with everything that seat has not " +
        "earned stripped out — a fogged view. One function call, once a night:" +
        "<p>This is the <em>only</em> way information reaches the agent. There " +
        "is no second channel, and no way for the agent to ask a follow-up " +
        "question. Whatever is in this object is what it gets.</p>",
      mount: "view",
    },
    {
      ph: "harness", who: "HARNESS · PYTHON", short: "Digests it",
      title: "The harness digests it",
      body:
        "That object is structured data — nested lists and dictionaries, " +
        "useful to a program and useless to a reader. So the harness works " +
        "out, in order: what can I <b>see</b> right now, what do I " +
        "<b>remember</b> from previous nights, what is <b>worth doing</b>, and " +
        "which of those things are <b>legal</b> tonight.<p>Those four " +
        "questions are four crews of Python, and this is most of what the " +
        "harness is. Each crew reads the engine's view and produces one block " +
        "of the document that gets written in the next step.</p>",
      mount: "lanes",
      after:
        "<p>Legality is the one worth dwelling on. Rather than let the model " +
        "propose a move and then reject it, the harness works out every legal " +
        "play <em>in advance</em> and offers only those. That is why the " +
        "fourth crew is bigger than the other three together: drawing a route " +
        "that obeys the carrying limit, avoids ground already stripped, gets " +
        "home before dawn and does not collide with a rival doing the same " +
        "thing is the actual difficulty of this game, and none of it is safe " +
        "to leave to a model that cannot see the board.</p>",
    },
    {
      ph: "harness", who: "HARNESS · PYTHON", short: "Builds the prompt",
      title: "The harness assembles the prompt",
      body:
        "Now it writes the document. Not a question — a briefing. Five blocks, " +
        "stacked in a fixed order every single night so the model always finds " +
        "things in the same place:",
      mount: "stack",
      after:
        "<p>Two of those blocks never really change: the rules and the " +
        "doctrine are the same text most nights, and together they are three " +
        "fifths of the page. The board, the memory and the menu are rebuilt " +
        "from scratch every turn.</p>",
    },
    {
      ph: "model", who: "FRONTIER MODEL", short: "Thinks",
      title: "The model thinks",
      body:
        "The document is sent to the model and it is asked to reason — in " +
        "prose, out loud, committing to nothing. No format to satisfy, no " +
        "decision required yet.<p>Splitting this from the decision is " +
        "deliberate. Asked to reason and produce valid structured output in " +
        "one breath, a model tends to do both badly: the thinking gets clipped " +
        "to fit the format and the format gets broken by the thinking. Here it " +
        "is allowed to be wrong cheaply first.</p>",
      mount: "think",
    },
    {
      ph: "model", who: "FRONTIER MODEL", short: "Commits a plan",
      title: "The model commits a plan",
      body:
        "The whole document goes again, now with the model's own reasoning " +
        "attached, and this time the answer must be strict JSON. The important " +
        "field is tiny:",
      mount: "plan",
      after:
        "<p>Three identifiers, in the order they should run. That is the " +
        "entire instruction to the game. The model never writes a coordinate, " +
        "never names an hour, never assigns a unit — it picks from the menu it " +
        "was handed. Take coordinates away from a language model and a whole " +
        "class of confident, plausible, illegal plans becomes impossible.</p>",
    },
    {
      ph: "harness", who: "HARNESS · PYTHON", short: "Compiles & checks",
      title: "The harness compiles it and makes it safe",
      body:
        "Those three identifiers are expanded back into the concrete moves " +
        "they stand for, and every one is checked against the rules: can a " +
        "harvester legally land there, is that ground already stripped, do we " +
        "own enough probes, is the walk within the carrying limit. Three " +
        "identifiers became %MOVES% moves.",
      mount: "moves",
      after:
        '<div class="ag-aside"><span class="lbl">THE RULE THAT MATTERS</span>' +
        "<p>This compiler may refuse anything <b>illegal</b>. It may not " +
        "overrule anything merely <b>unwise</b> — it cannot shorten a route it " +
        "thinks is risky or swap a target it disagrees with. When it objects, " +
        "it says so in a log and runs the plan anyway. A compiler that quietly " +
        "edits plans makes the agent's reasoning impossible to judge: you can " +
        "no longer tell a bad decision from a good one that got overwritten." +
        "</p></div>",
    },
    {
      ph: "engine", who: "GAME ENGINE", short: "Receives the moves",
      title: "The moves go back to the engine",
      body:
        "One call, one array, and the agent's involvement in the night is " +
        "over. Note how small the vocabulary is: <code>drop</code> a harvester " +
        "onto a cell, <code>step</code> it one square, <code>pickup</code> to " +
        "lift it home, <code>probe</code> to place an eye. Four verbs.",
      mount: "wire",
    },
    {
      ph: "engine", who: "GAME ENGINE", short: "Plays the turn",
      title: "The engine plays the turn",
      body:
        "Every seat's orders resolve together, hour by hour, and only now does " +
        "anyone find out what was under the fog. Here is what those " +
        "%MOVES% moves actually did — one harvester walking a remembered route " +
        "onto its own pure cell in the south-east, the other landing blind " +
        "beside a rival's seam in the north-west and groping for a prize it " +
        "had never seen.<p>Then the harness reads the engine's hour-by-hour " +
        "record of the night, writes it down beside what the agent " +
        "<em>said</em> it would do, and that pairing becomes the memory block " +
        "in tomorrow's document. The loop closes.</p>",
      mount: "board-b",
    },
  ];

  /* The digestion floor: every module that runs between "engine hands over a
   * view" and "harness starts writing". Grouped by the question it answers
   * rather than by call order, because the point is the division of labour —
   * and the fact that three quarters of it is working out what is legal. */
  const LANES = [
    {
      q: "What can I see?",
      feeds: "THE BOARD NOW",
      foot: "On this night: %LIVE% cells lit by one probe, of which %WV% had " +
        "something on them worth writing down.",
      ops: [
        ["world_view.py",
          "Walks every cell currently in sight and records what is on it — " +
          "which ore, how rich, whose probe. Empty ground is deliberately " +
          "left out, or the list would be 80 lines of nothing."],
        ["out_of_grid.py",
          "Signs and echoes — everything known without being seen: public " +
          "beacons that glow through fog but are smeared off-centre by the " +
          "engine, and cells this seat saw on an earlier night."],
      ],
    },
    {
      q: "What do I remember?",
      feeds: "LAST NIGHT & JOURNAL",
      foot: "On this night: 3 previous nights on the thread, and one EMP hit " +
        "on the seat's probes still being reasoned about.",
      ops: [
        ["last_night.py",
          "Fuses four engine records into one page: what you ordered, what " +
          "happened hour by hour, what you banked against what you promised, " +
          "and what you witnessed other houses do."],
        ["digest.py",
          "Turns raw event channels into causal sentences — who hit you, " +
          "which of their probes you killed, where the scars are."],
        ["hazard_memory.py",
          "Every cell this seat has ever watched turn green, kept forever. " +
          "Green never recovers, so that growing set is a permanent " +
          "do-not-step list even under fog."],
        ["journal.py",
          "One entry per night since day one, oldest first, so the model " +
          "reads its own story so far and then extends it."],
      ],
    },
    {
      q: "What is it worth?",
      feeds: "prices on every option",
      foot: "On this night: the single pure cell at (31,18) came out top of " +
        "the pyramid at 765 points.",
      ops: [
        ["option_economics.py",
          "Prices each candidate: expected haul, the odds of actually " +
          "getting it, and what a rival could do to you mid-walk."],
        ["value_pyramid.py",
          "Ranks the whole board by worth and by how sure we are it is " +
          "there — seen beats remembered beats rumoured."],
        ["comb_shapes.py",
          "Draws the walk itself: serpentines and combs across a probe " +
          "disk, turning toward value at every step."],
      ],
    },
    {
      q: "What am I allowed to do?",
      feeds: "THE OPTION MENU",
      foot: "On this night: %OPTIONS% legal plays survived to the menu, each " +
        "with its route drawn and its payoff already worked out.",
      ops: [
        ["seam_control.py",
          "Multi-night attack patterns over a rich seam — your own, or a " +
          "rival's."],
        ["agency.py",
          "Gathers every candidate the others produce into one registry, " +
          "gives each a name, and renders the menu the model picks from."],
        ["hint_dispersion.py",
          "Nudges this seat off the obvious square. Every house runs the " +
          "same maths on the same public beacon, so without this they all " +
          "land on the same cell and all bank nothing."],
        ["frontier.py",
          "Where to place a new eye: away from rivals, toward the edges, " +
          "jittered so two seats do not converge."],
        ["supersede.py",
          "Which rival probe is worth landing on top of to blind them."],
        ["orbit.py",
          "What to spend the credits on — another harvester, or more eyes."],
        ["speculative.py",
          "Hot drops into fog for nights when nothing good is lit, which " +
          "this one was."],
        ["chain_filter.py",
          "Throws away near-duplicate walks so the menu is not four " +
          "spellings of the same plan."],
      ],
    },
  ];

  scene(function s00() {
    /* three parties, and the wires between them */
    $("#s00-bridge").innerHTML =
      '<div class="ag-actor is-engine"><span class="who ph-engine">THE GAME</span>' +
      '<div class="what">Engine + simulator</div>' +
      '<div class="note">Holds the true board and resolves every night. ' +
      "Python, in this same process.</div></div>" +
      '<div class="ag-wire"><span>a fogged view</span>' +
      '<span class="arrow">──────▶</span>' +
      '<span class="arrow">◀──────</span><span>legal moves</span></div>' +
      '<div class="ag-actor is-harness"><span class="who ph-harness">THE HARNESS</span>' +
      '<div class="what">tabula_v12</div>' +
      '<div class="note">The only party that talks to both sides. Writes the ' +
      "prompt, reads the reply, compiles it, remembers the night.</div></div>" +
      '<div class="ag-wire"><span>a long document</span>' +
      '<span class="arrow">──────▶</span>' +
      '<span class="arrow">◀──────</span><span>prose, then JSON</span></div>' +
      '<div class="ag-actor is-model"><span class="who ph-model">THE MODEL</span>' +
      '<div class="what">Frontier LLM</div>' +
      '<div class="note">A text endpoint over the network. Stateless, no ' +
      "tools, no sight of the game. Text in, text out.</div></div>";

    /* the one-line overview */
    $("#s00-mini").innerHTML = TURN.map(
      (s, i) =>
        '<button class="ag-mini-step ' + PHASE[s.ph] + '" data-i="' + i + '">' +
        '<span class="i">' + String(i + 1).padStart(2, "0") + "</span>" +
        '<span class="t">' + esc(s.short) + "</span></button>",
    ).join("");
    $("#s00-mini").addEventListener("click", (e) => {
      const b = e.target.closest("[data-i]");
      if (!b) return;
      const el = document.getElementById("st" + b.dataset.i);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    /* the vertical flow */
    const fill = (s) =>
      s.replace(/%OPTIONS%/g, CARD.options.length)
        .replace(/%MOVES%/g, CARD.moves.length)
        .replace(/%LIVE%/g, BOARD.counts.live)
        .replace(/%WV%/g, CARD.world_view.length);

    $("#s00-steps").innerHTML = TURN.map(
      (s, i) =>
        '<div class="ag-step ' + PHASE[s.ph] + '" id="st' + i + '">' +
        '<div class="ag-step-rail"><span class="ag-step-num">' + (i + 1) +
        "</span></div>" +
        '<div class="ag-step-main">' +
        '<span class="ag-step-who ph-' + s.ph + '">' + esc(s.who) + "</span>" +
        "<h4>" + esc(s.title) + "</h4>" +
        // `body` supplies its own <p> breaks; wrapping it in another <p> would
        // nest them, which the browser silently unnests and leaves a stray tag.
        "<p>" + fill(s.body).replace(/<p>/g, "</p><p>") + "</p>" +
        (s.mount ? '<div id="s00-' + s.mount + '"></div>' : "") +
        (s.after ? fill(s.after) : "") +
        "</div></div>",
    ).join("");

    /* step 1 — the board as the seat receives it */
    const crop = new Crop({});
    const boardA = renderBoard($("#s00-board-a"),
      truthCells(crop, { fog: true }), {
        cols: crop.cols, rows: crop.rows, cellW: 13, cellH: 13,
      });
    for (const c of BOARD.live) {
      const el = cellOf(boardA, crop, c.x, c.y);
      if (el) setFog(el, false);
    }
    for (const p of MY_PROBES) {
      for (const [x, y] of disk(p.x, p.y)) mark(boardA, crop, x, y, "ag-mark-disk");
      const el = cellOf(boardA, crop, p.x, p.y);
      if (el) setProbe(el, p.nights_remaining || 1, "--seat-p1");
    }
    $("#s00-board-a").insertAdjacentHTML(
      "beforeend",
      '<div class="ag-legend"><span class="ag-key-disk"><i></i>the one probe p1 ' +
      "still has — " + BOARD.counts.live + " cells lit of " + nfmt(W * H) +
      "</span></div>",
    );

    /* step 2 — the shape of what the engine hands over */
    // Wrapped rather than one key per line: 24 lines would scroll out of a
    // short pane and the point is the SHAPE, not any individual key.
    const keys = BOARD.view_keys || [];
    $("#s00-view").innerHTML = pane(
      'build_agent_view(session, "p1")', "the keys that come back",
      keys.join(" · "),
      { cls: "is-short", right: keys.length + " keys of structured data" },
    );

    /* step 3 — the four crews, sized by how much Python each one is */
    const loc = (f) => (HARNESS.modules[f] || {}).lines || 0;
    const widest = Math.max(
      ...LANES.flatMap((l) => l.ops.map(([f]) => loc(f))), 1);
    let shown = 0;
    const laneHTML = LANES.map((l) => {
      const ops = l.ops.slice().sort((a, b) => loc(b[0]) - loc(a[0]));
      const sum = ops.reduce((n, [f]) => n + loc(f), 0);
      shown += sum;
      return (
        '<div class="ag-lane">' +
        '<div class="ag-lane-head"><span class="q">' + esc(l.q) + "</span>" +
        '<span class="fd">writes <b>' + esc(l.feeds) + "</b></span>" +
        '<span class="sz">' + ops.length + " files · " + nfmt(sum) +
        " lines</span></div><div class=\"ag-ops\">" +
        ops.map(([f, d]) =>
          '<div class="ag-op"><span class="nm">' + esc(f) + "</span>" +
          '<span class="ds">' + esc(d) + "</span>" +
          '<span class="mt"><i class="bar" style="width:' +
          Math.max(2, Math.round((loc(f) / widest) * 100)) + '%"></i>' +
          '<span class="lc">' + nfmt(loc(f)) + "</span></span></div>").join("") +
        "</div>" +
        (l.foot ? '<div class="ag-lane-foot">' + fill(l.foot) + "</div>" : "") +
        "</div>"
      );
    }).join("");
    $("#s00-lanes").innerHTML =
      '<div class="ag-lanes">' + laneHTML +
      '<div class="ag-lanes-total"><span>' +
      "the four crews, before a single word of the prompt is written</span>" +
      "<span><b>" + nfmt(shown) + "</b> of the harness's " +
      nfmt(HARNESS.total_lines) + " lines of Python</span></div>" +
      '<p class="ag-lanes-rest">The remaining ' +
      nfmt(HARNESS.total_lines - shown) + " lines are the steps either side of " +
      "these: the rules and doctrine text, the module that assembles the " +
      "document, the response schema, the compiler in step 7, and the audit " +
      "card this guide is generated from.</p></div>";

    /* step 4 — the five blocks of the prompt */
    const PLAIN = {
      "RULES & DOCTRINE": "How the game works, and how we have taught it to be " +
        "played. Scoring, the clock, weapons — then strategy.",
      "THE BOARD NOW": "What the seat can see this instant, and what it knows " +
        "without being able to see it.",
      "LAST NIGHT & JOURNAL": "What it said it would do last night, what the " +
        "engine records that it actually did, and a line for every night before.",
      "THE OPTION MENU": "Every legal play tonight, with its route and its " +
        "price. The list it must choose from.",
      "YOUR TASK": "The question, and the exact shape the answer has to take.",
    };
    $("#s00-stack").innerHTML =
      '<div class="ag-stack">' +
      CARD.sections.map((s) =>
        '<div class="ag-slab"><span class="nm">' + esc(s.label) + "</span>" +
        '<span class="ds">' + esc(PLAIN[s.label] || "") + "</span>" +
        '<span class="sz">' + nfmt(s.chars) + "</span></div>").join("") +
      "</div>";

    /* step 5 — the opening of the real reasoning */
    const firstPara = CARD.think
      .split(/\n\s*\n/)
      .filter((p) => p.trim() && !p.trim().startsWith("#"))
      .slice(0, 2)
      .map((p) => p.replace(/\n/g, " ").trim())
      .join("\n\n");
    $("#s00-think").innerHTML = pane(
      "THINK", "the first two paragraphs it wrote back", firstPara,
      { cls: "is-short" },
    );

    /* step 6 — the decision */
    $("#s00-plan").innerHTML = pane(
      "PLAN", "the field that decides the night",
      '"plan": ' + JSON.stringify(CARD.plan_ids, null, 1),
      { cls: "is-short", tint: "json" },
    );

    /* step 7 — what that expanded into */
    $("#s00-moves").innerHTML = pane(
      "COMPILED", CARD.plan_ids.length + " ids → " + CARD.moves.length + " moves",
      CARD.moves
        .map((m, i) => {
          const at = m.at || m.to;
          return "H" + String(i + 1).padStart(2, "0") + "  " + m.a.padEnd(7) +
            (at ? "(" + at + ")" : "") +
            (m.unit ? "   " + m.unit : "");
        })
        .join("\n"),
      { cls: "is-short" },
    );

    /* step 8 — the wire */
    $("#s00-wire").innerHTML = pane(
      "submit_policy(...)", "the first three of " + CARD.moves.length,
      JSON.stringify(CARD.moves.slice(0, 3), null, 1) .replace(/\n\]$/, ",\n  …\n]"),
      { cls: "is-short", tint: "json" },
    );

    /* step 9 — the night as it played out */
    const boardB = renderBoard($("#s00-board-b"), truthCells(crop), {
      cols: crop.cols, rows: crop.rows, cellW: 13, cellH: 13,
    });
    const trails = {};
    for (const m of CARD.moves) {
      const at = m.at || m.to;
      if (!at) continue;
      const el = cellOf(boardB, crop, at[0], at[1]);
      if (!el) continue;
      if (m.a === "probe") { setProbe(el, 3, "--seat-p1"); continue; }
      bumpTrail(el, trails, crop.idx(at[0], at[1]));
      el.classList.add(m.a === "drop" ? "ag-mark-drop" : "ag-mark-route");
    }
    for (const s of BOARD.redsigns) {
      for (const [x, y] of s.pure_cells) mark(boardB, crop, x, y, "ag-mark-pure");
    }
    $("#s00-board-b").insertAdjacentHTML(
      "beforeend",
      '<div class="ag-legend"><span class="ag-key-route"><i></i>where the two ' +
      "harvesters walked</span>" +
      '<span class="ag-key-pure"><i></i>the two pure cells, one at each end</span>' +
      "</div>",
    );
  });

  /* ── 01 · it is all text ───────────────────────────────────────── */

  scene(function s01() {
    const stats = $("#s01-stats");
    const wv = CARD.world_view.length;
    const cells = W * H;
    const rows = [
      [nfmt(CARD.prompt_chars), "CHARACTERS SENT", false],
      [String(wv), "CELLS IT CAN SEE", true],
      [nfmt(cells), "CELLS ON THE BOARD", false],
      [String(CARD.options.length), "PLAYS OFFERED", false],
      [String(CARD.plan_ids.length), "PLAYS CHOSEN", false],
      [String(CARD.moves.length), "MOVES SENT BACK", false],
    ];
    stats.innerHTML = rows
      .map(([v, k, hot]) =>
        '<div class="ag-stat"><div class="v' + (hot ? " is-hot" : "") +
        '">' + v + '</div><div class="k">' + k + "</div></div>")
      .join("");

    // Where the 85k actually goes. The point of the bar is that the board is
    // the smallest slice of the page.
    const cols = ["#4a4a5a", "#7ab8ff", "#ff8585", "#4dca5e"];
    const total = CARD.sections.reduce((a, s) => a + s.chars, 0) || 1;
    $("#s01-split").innerHTML =
      '<div class="ag-bar">' +
      CARD.sections
        .map((s, i) => {
          const pct = (s.chars / total) * 100;
          const short = s.label.replace(/^SECTION \d+ — /, "");
          return '<div style="width:' + pct.toFixed(2) + "%;background:" +
            cols[i % cols.length] + '" title="' + esc(s.label) + " · " +
            nfmt(s.chars) + ' chars">' +
            (pct > 9 ? esc(short) + " " + pct.toFixed(0) + "%" : "") + "</div>";
        })
        .join("") +
      "</div>" +
      '<div class="ag-legend">' +
      CARD.sections
        .map((s, i) =>
          '<span style="color:' + cols[i % cols.length] + '"><i></i>' +
          esc(s.label.replace(/^SECTION \d+ — /, "")) + " · " +
          nfmt(s.chars) + " chars</span>")
        .join("") +
      "</div>";

    const wvText = JSON.stringify(CARD.world_view, null, 0)
      .replace(/^\[/, "[\n  ")
      .replace(/\},\{/g, "},\n  {")
      .replace(/\]$/, "\n]");
    $("#s01-worldview").innerHTML = pane(
      "WORLD VIEW", "verbatim, from the card", wvText,
      { cls: "is-mid", tint: "json", right: wv + " cells" },
    );
  });

  /* ── 02 · the loop ─────────────────────────────────────────────── */

  const FLOW = [
    {
      k: "ENGINE", n: "build_agent_view", engine: true,
      f: "snowpark/engine.py",
      d: "The engine takes the true session state and strips it down to one " +
         "seat's knowledge — live probe disks, remembered echoes, public " +
         "signs, intel about rivals. This is the only way information enters " +
         "the agent. There is no second channel and no way to widen it.",
    },
    {
      k: "HARNESS", n: "compute", f: "value_pyramid · seam_control · frontier",
      d: "Before any text is written, the harness works out what is worth " +
         "doing. It ranks every visible target, builds multi-wave attack " +
         "patterns for each live sign, picks probe placements, and prices " +
         "each play's yield, crush and collision risk.",
    },
    {
      k: "HARNESS", n: "write the card", f: "prompt.py",
      d: "The document is assembled: rules, doctrine, the board as the seat " +
         "sees it, what happened last night, the running journal, and the " +
         "option menu the compute step produced.",
    },
    {
      k: "MODEL", n: "THINK", f: "cortex_chat · Haiku",
      d: "Bounded prose reasoning. No schema, no commitment — the pass exists " +
         "so the model can read the board out loud and be wrong cheaply.",
    },
    {
      k: "MODEL", n: "PLAN", f: "chat_schema.py",
      d: "The whole card again, plus the think output, and a strict JSON " +
         "schema. It returns a posture, an ordered list of option IDs, an " +
         "intent for tomorrow's memory, and a reflection on last night.",
    },
    {
      k: "HARNESS", n: "package", f: "packager.py",
      d: "Chosen IDs become concrete drop / step / pickup / probe moves. It " +
         "checks legality only — inventory, drop coverage, known-green, hold " +
         "cap, ordering — and reports anything it refuses rather than " +
         "silently rewriting the plan.",
    },
    {
      k: "HARNESS", n: "sanitize", f: "tabula_v12/_v7/move_sanitizer.py",
      d: "A last mechanical pass over the move list to catch anything " +
         "malformed before it reaches the engine.",
    },
    {
      k: "ENGINE", n: "submit_policy", engine: true,
      f: "snowpark/engine.py",
      d: "The moves are handed back. This call and its orbit-phase twin are " +
         "the ONLY two ways this package writes game state. Overnight the " +
         "simulator resolves every seat's moves hour by hour.",
    },
    {
      k: "HARNESS", n: "remember", f: "last_night.py · journal.py",
      d: "After resolution the harness reads the engine's own per-hour replay " +
         "frames and writes down what really happened, next to what the agent " +
         "said it would do. That pairing is tomorrow's Section 3.",
    },
  ];

  scene(function s02() {
    const flow = $("#s02-flow");
    flow.innerHTML = FLOW.map(
      (s, i) =>
        '<div class="ag-flow-step' + (s.engine ? " is-engine" : "") +
        (i === 0 ? " is-on" : "") + '" data-i="' + i + '">' +
        '<span class="k">' + s.k + "</span>" +
        '<span class="n">' + esc(s.n) + "</span>" +
        '<span class="f">' + esc(s.f) + "</span></div>",
    ).join('<span class="ag-flow-arrow">›</span>');

    const detail = $("#s02-detail");
    function show(i) {
      [...flow.querySelectorAll(".ag-flow-step")].forEach((el, j) =>
        el.classList.toggle("is-on", i === j));
      const s = FLOW[i];
      detail.innerHTML =
        '<div class="mn-stage-caption" style="max-width:none">' +
        '<div class="mn-stage-label">' + esc(s.n) + "</div>" +
        '<div class="mn-stage-body">' + esc(s.d) + "</div>" +
        '<div class="mn-stage-cite dim">' + esc(s.f) + "</div></div>";
    }
    flow.addEventListener("click", (e) => {
      const el = e.target.closest("[data-i]");
      if (el) show(+el.dataset.i);
    });
    show(0);
  });

  /* ── 03 · live vision ──────────────────────────────────────────── */

  const MY_PROBES = BOARD.probes.filter((p) => p.owner === "p1");
  const LIVE_SET = new Set(BOARD.live.map((c) => c.x + "," + c.y));
  const ECHO_BY = new Map(BOARD.echo.map((c) => [c.x + "," + c.y, c]));

  scene(function s03() {
    const crop = new Crop({});
    const host = $("#s03-board");
    const board = renderBoard(host, truthCells(crop), {
      cols: crop.cols, rows: crop.rows, cellW: 15, cellH: 15,
    });

    $("#s03-legend").innerHTML =
      '<span class="ag-key-disk"><i></i>your probe disk (live sight)</span>' +
      '<span class="ag-key-pure"><i></i>pure red · 765 pts</span>' +
      '<span style="color:var(--dim)"><i></i>fogged / never seen</span>';

    const steps = [
      {
        label: "engine truth",
        title: "ENGINE TRUTH",
        text:
          "The whole 40&times;28 board as the simulator holds it: " +
          nfmt(BOARD.truth.filter((c) => c[0] === TILE.RED).length) +
          " red cells, " +
          nfmt(BOARD.truth.filter((c) => c[0] === TILE.BLUE).length) +
          " blue, " +
          nfmt(BOARD.truth.filter((c) => c[0] === TILE.GREEN).length) +
          " already stripped. No seat is ever shown this.",
        paint: () => {
          for (const el of board.children) {
            setFog(el, false);
            el.classList.remove("ag-dim");
          }
        },
      },
      {
        label: "what p1 sees",
        title: "WHAT P1 RECEIVES",
        text:
          "Fog closes over everything outside one probe disk. p1 holds " +
          MY_PROBES.length + " probe, at (" + MY_PROBES[0].x + "," +
          MY_PROBES[0].y + "), with " + MY_PROBES[0].nights_remaining +
          " night of life left. That circle is the seat's entire live sight: " +
          BOARD.counts.live + " of " + nfmt(W * H) + " cells.",
        paint: () => {
          for (let y = 0; y < H; y++) {
            for (let x = 0; x < W; x++) {
              const el = cellOf(board, crop, x, y);
              setFog(el, !LIVE_SET.has(x + "," + y));
            }
          }
          for (const p of MY_PROBES) {
            for (const [x, y] of disk(p.x, p.y)) mark(board, crop, x, y, "ag-mark-disk");
            const el = cellOf(board, crop, p.x, p.y);
            if (el) setProbe(el, p.nights_remaining || 1, "--seat-p1");
          }
        },
      },
      {
        label: "+ echo memory",
        title: "PLUS WHAT IT REMEMBERS",
        text:
          "Add the " + BOARD.echo.length + " cells this seat has seen at some " +
          "point and no longer watches. Exact coordinates, stale contents — a " +
          "rival may have stripped any of them since. This is most of what the " +
          "agent actually plans with.",
        paint: () => {
          for (const [key, c] of ECHO_BY) {
            const [x, y] = key.split(",").map(Number);
            const el = cellOf(board, crop, x, y);
            if (!el || LIVE_SET.has(key)) continue;
            setFog(el, false);
            el.classList.add("ag-dim");
          }
        },
      },
      {
        label: "the prize",
        title: "THE PRIZE",
        text:
          "p1's own pure sits at (31,18) — outside the live disk, known only " +
          "from an echo one night old. It is worth 765 points on its own, and " +
          "the rival was told it exists the moment p1 found it.",
        paint: () => {
          const pure = BOARD.redsigns.find((s) => s.mine).pure_cells[0];
          mark(board, crop, pure[0], pure[1], "ag-mark-pure");
          const el = cellOf(board, crop, pure[0], pure[1]);
          if (el) { setFog(el, false); el.classList.remove("ag-dim"); }
        },
      },
    ];

    // Steps are cumulative, so replay from the top on every move.
    const holder = document.createElement("div");
    $("#s03-controls").appendChild(holder);
    stepper(holder, steps, (i) => {
      for (const el of board.children) {
        el.classList.remove("ag-dim", "ag-mark-disk", "ag-mark-pure");
        setEntity(el, "");
      }
      for (let k = 0; k <= i; k++) steps[k].paint();
      $("#s03-label").textContent = steps[i].title;
      $("#s03-text").innerHTML = "<p>" + steps[i].text + "</p>";
    }, { autoMs: 1600 });
  });

  /* ── 04 · signs and echoes ─────────────────────────────────────── */

  scene(function s04() {
    const crop = new Crop({});
    const host = $("#s04-board");
    const board = renderBoard(host, truthCells(crop, { fog: true }), {
      cols: crop.cols, rows: crop.rows, cellW: 15, cellH: 15,
    });

    $("#s04-legend").innerHTML =
      '<span class="ag-key-smear"><i></i>the broadcast smear</span>' +
      '<span class="ag-key-pure"><i></i>where the pure really is</span>' +
      '<span class="ag-key-disk"><i></i>probe on the seam</span>';

    const mine = BOARD.redsigns.find((s) => s.mine);
    const theirs = BOARD.redsigns.find((s) => !s.mine);

    function paintSign(sign) {
      for (const [x, y, w] of sign.cells) {
        const el = cellOf(board, crop, x, y);
        if (!el) continue;
        el.classList.add("ag-mark-smear");
        // Weight is the engine's own confidence in that cell.
        el.style.opacity = String(0.35 + 0.65 * w);
      }
    }
    function paintPure(sign) {
      for (const [x, y] of sign.pure_cells) {
        const el = cellOf(board, crop, x, y);
        if (!el) continue;
        setFog(el, false);
        el.style.opacity = "1";
        el.classList.add("ag-mark-pure");
      }
    }
    const dist = (s) => {
      const [px, py] = s.pure_cells[0];
      return Math.hypot(s.center[0] - px, s.center[1] - py).toFixed(1);
    };

    const steps = [
      {
        label: "p1's own sign",
        title: "YOUR OWN SIGN",
        text:
          "p1 found a pure on day 3, so a sign went out to every seat. The " +
          "broadcast is " + mine.cells.length + " weighted cells centred on (" +
          mine.center[0] + "," + mine.center[1] + ").",
        paint: () => paintSign(mine),
      },
      {
        label: "the real cell",
        title: "AND THE REAL CELL",
        text:
          "The pure is actually at (" + mine.pure_cells[0] + "). The " +
          "broadcast centre is " + dist(mine) + " cells away from it. Even the " +
          "seat that found it is handed a blur in the public feed — it knows " +
          "the true square only from its own echo.",
        paint: () => paintPure(mine),
      },
      {
        label: "the rival's sign",
        title: "THE RIVAL'S SIGN",
        text:
          "p2 found one the same night, across the map, and p1 was told. " +
          theirs.cells.length + " cells centred on (" + theirs.center[0] + "," +
          theirs.center[1] + "), true pure at (" + theirs.pure_cells[0] +
          ") — " + dist(theirs) + " cells off. p1 has never had sight of any " +
          "of it.",
        paint: () => { paintSign(theirs); paintPure(theirs); },
      },
      {
        label: "who is watching",
        title: "WHO IS WATCHING",
        text:
          "p2 holds a probe at (16,9), lighting its own seam. p1 holds one at " +
          "(27,17), lighting p1's. Each seat can see its own prize and is " +
          "blind on the other's — and both were told about both.",
        paint: () => {
          for (const p of BOARD.probes) {
            const el = cellOf(board, crop, p.x, p.y);
            if (!el) continue;
            setFog(el, false);
            el.style.opacity = "1";
            el.classList.add("ag-mark-probe");
            setProbe(el, 3, p.owner === "p1" ? "--seat-p1" : "--seat-p2");
          }
        },
      },
    ];

    const holder = document.createElement("div");
    $("#s04-controls").appendChild(holder);
    stepper(holder, steps, (i) => {
      for (const el of board.children) {
        el.classList.remove("ag-mark-smear", "ag-mark-pure", "ag-mark-probe");
        el.style.opacity = "";
        setEntity(el, "");
        setFog(el, true);
      }
      for (let k = 0; k <= i; k++) steps[k].paint();
      $("#s04-label").textContent = steps[i].title;
      $("#s04-text").innerHTML = "<p>" + steps[i].text + "</p>";
    }, { autoMs: 1800 });

    $("#s04-oog").innerHTML = pane(
      "OUT-OF-GRID KNOWLEDGE", "verbatim, from the card",
      CARD.excerpts.out_of_grid + "\n\n" + CARD.excerpts.red_signs,
      { cls: "is-mid", tint: "prompt" },
    );
  });

  /* ── 05 · the value pyramid ────────────────────────────────────── */

  const TIERS = [
    ["1", "LIVE PURE", "A probe is on it now. Grab it — smash straight down.",
      "unconditional"],
    ["2", "ECHO PURE, WALKABLE",
      "Seen before, now dark, but a live cell is close enough to land on and " +
      "walk in from. Steps are not vision-gated; only the drop is.",
      "unconditional"],
    ["3", "LIVE MASS", "The best thing actually in sight. Chain it.", "ranked"],
    ["4", "RICH LIVE BLUE",
      "Fissile blue for the vault. Real points, but it never outranks a pure.",
      "ranked"],
    ["5", "EXPECTED — SIGN FOG",
      "Nobody has seen it; a sign says it is near. Needs a probe to pin, and " +
      "belongs to the seam machinery rather than here.",
      "priced as a gamble"],
  ];

  scene(function s05() {
    $("#s05-pyramid").innerHTML = TIERS.map(
      ([r, h, body, w], i) =>
        '<div class="ag-tier t' + (i + 1) + '">' +
        '<span class="r">' + r + "</span>" +
        "<span><span class=\"h\">" + esc(h) + "</span><br>" +
        '<span style="color:var(--dim);font-size:12.5px">' + esc(body) +
        "</span></span>" +
        '<span class="w">' + esc(w) + "</span></div>",
    ).join("");
  });

  /* ── 06 · the option menu ──────────────────────────────────────── */

  scene(function s06() {
    $("#s06-preamble").innerHTML = pane(
      "MENU PREAMBLE", "verbatim, from the card", CARD.excerpts.menu_preamble,
      { cls: "is-short", tint: "prompt" },
    );

    const picked = new Set(CARD.plan_ids);
    const rows = [
      ["why", "WHY"],
      ["walk", "WALK"],
      ["yield", "YIELD"],
      ["dedupe", "OVERLAP"],
      ["risk", "CRUSH / RISK"],
    ];
    $("#s06-options").innerHTML = CARD.options
      .map((o) => {
        // The card puts the probe cost at the tail of the headline.
        const m = /·\s*(no probe|needs \d+ probes?)\s*$/.exec(o.headline);
        const cost = m ? m[1] : "";
        const head = m ? o.headline.slice(0, m.index).trim() : o.headline;
        return (
          '<div class="ag-opt' + (picked.has(o.id) ? " is-picked" : "") + '">' +
          '<div class="ag-opt-head">' +
          '<span class="ag-opt-id">' + esc(o.id) + "</span>" +
          '<span class="ag-opt-h">' + esc(head) + "</span>" +
          '<span class="ag-opt-cost">' + esc(cost) +
          (picked.has(o.id) ? " · CHOSEN" : "") + "</span></div>" +
          '<div class="ag-opt-body">' +
          rows
            .filter(([k]) => o[k])
            .map(([k, lbl]) =>
              '<div class="ag-opt-row' + (k === "why" ? " is-why" : "") +
              '"><span class="k">' + lbl + '</span><span class="v">' +
              esc(o[k]) + "</span></div>")
            .join("") +
          "</div></div>"
        );
      })
      .join("");

    $("#s06-options").addEventListener("click", (e) => {
      const head = e.target.closest(".ag-opt-head");
      if (head) head.parentElement.classList.toggle("is-open");
    });
  });

  /* ── 07 · the card ─────────────────────────────────────────────── */

  const SECTION_NOTES = {
    "RULES & DOCTRINE":
      "Scoring, the hour clock, the hold cap, weapons, drop legality — then " +
      "how to play: the value pyramid, redsign combat, risk profiles, named " +
      "plays. Near-identical every night.",
    "THE BOARD NOW":
      "Your assets and stocks, the world view, out-of-grid knowledge, " +
      "drop-legal zones, known enemy probes, orbital directives.",
    "LAST NIGHT & JOURNAL":
      "The intent you set, the engine's own per-hour log of what became of " +
      "it, what you banked against what you expected, and the running thread " +
      "of every night so far.",
    "THE OPTION MENU":
      "Thirteen fully-formed plays, each with its route drawn, its yield " +
      "priced, its overlaps flagged and its risk named. Physically part of " +
      "section 3 in the card, but a thing of its own.",
    "YOUR TASK":
      "The question itself, plus the output schema. Small, because the " +
      "framing has already been done.",
  };

  scene(function s07() {
    const total = CARD.sections.reduce((a, s) => a + s.chars, 0) || 1;
    $("#s07-anatomy").innerHTML =
      '<div class="ag-mods">' +
      CARD.sections
        .map((s) => {
          const pct = ((s.chars / total) * 100).toFixed(0);
          return (
            '<div class="ag-mod"><div class="f">' + esc(s.label) + "</div>" +
            '<div class="d">' + esc(SECTION_NOTES[s.label] || "") + "</div>" +
            '<div class="fired">' + nfmt(s.chars) + " CHARS · " + pct +
            "% OF THE CARD</div></div>"
          );
        })
        .join("") +
      "</div>";

    // The whole prompt, verbatim. It is deliberately not trimmed — the length
    // is the argument.
    const full = CARD.full_think_prompt || "";
    $("#s07-full").innerHTML = pane(
      "THINK PROMPT", "verbatim · scroll", full || "(not exported)",
      { cls: "is-tall", tint: "prompt",
        right: nfmt(CARD.prompt_chars) + " chars" },
    );
  });

  /* ── 08 · think and plan ───────────────────────────────────────── */

  scene(function s08() {
    // The card hard-wraps the JSON to a fixed column, so re-serialise the
    // parsed object rather than showing the ragged original.
    const P = CARD.plan_obj || {};
    const pretty = Object.keys(P).length
      ? JSON.stringify(P, null, 2)
      : CARD.plan_json;
    // The card hard-wraps THINK at ~90 columns for the text file; the model
    // wrote paragraphs. Rejoin within a paragraph so the pane can reflow to
    // its own width, and keep the blank lines that are really the model's.
    const think = CARD.think
      .split(/\n\s*\n/)
      .map((p) => p.replace(/\n(?![-*#\d])/g, " ").trim())
      .join("\n\n");

    $("#s08-pair").innerHTML =
      pane("STAGE 1 · THINK", "the model's reasoning", think,
        { cls: "is-tall", right: nfmt(CARD.think.length) + " chars" }) +
      pane("STAGE 2 · PLAN", "the committed JSON", pretty,
        { cls: "is-tall", tint: "json",
          right: Object.keys(P).length + " fields" });

    // Value shown is the real one from this turn; the note explains the field.
    const words = (s) => String(s || "").trim().split(/\s+/).length;
    const FIELDS = [
      ["plan", CARD.plan_ids.join(" · "),
        "The whole instruction to the game — option IDs in execution order."],
      ["posture", P.posture,
        "How the night is meant to be read. Tunes nothing mechanically; it is " +
        "a claim the next turn can be judged against."],
      ["situational",
        P.situational && typeof P.situational === "object"
          ? Object.entries(P.situational).map(([k, v]) => k + "=" + v).join(" ")
          : P.situational,
        "The card lists SITUATIONAL FACTS and asks for them back. Echoing " +
        "them is a cheap check that the board was actually read."],
      ["chaff_react", String(P.chaff_react),
        "The model expects to be jammed. Advisory only — it does not shorten " +
        "anything on the model's behalf."],
      ["intent", words(P.intent) + " words",
        "Written for tomorrow. This is the line the agent will be asked to " +
        "reflect on next turn."],
      ["reflection", words(P.reflection) + " words",
        "Last night's intent, judged against what the engine actually did."],
      ["reasoning", words(P.reasoning) + " words",
        "The justification, kept for the post-mortem."],
    ];
    $("#s08-fields").innerHTML =
      '<div class="ag-mods">' +
      FIELDS.filter(([, v]) => v && v !== "undefined")
        .map(
          ([k, v, d]) =>
            '<div class="ag-mod is-fired"><div class="f">' + esc(k) + "</div>" +
            '<div class="d">' + esc(d) + "</div>" +
            '<div class="fired">' + esc(String(v).toUpperCase()) + "</div></div>",
        ).join("") +
      "</div>";
  });

  /* ── 09 · the packager ─────────────────────────────────────────── */

  scene(function s09() {
    // Group the flat move list back into the runs the option IDs produced, so
    // the expansion from 3 ids -> 15 moves is legible.
    const runs = [];
    let cur = null;
    for (const m of CARD.moves) {
      if (m.a === "probe") {
        runs.push({ unit: null, moves: [m] });
        cur = null;
        continue;
      }
      if (!cur || cur.unit !== m.unit) {
        cur = { unit: m.unit, moves: [] };
        runs.push(cur);
      }
      cur.moves.push(m);
    }

    const fmt = (m) =>
      m.a === "probe" ? `probe (${m.at})`
        : m.a === "drop" ? `drop ${m.unit} @(${m.at})`
        : m.a === "pickup" ? `pickup ${m.unit}`
        : `step -> (${m.to})`;

    $("#s09-expand").innerHTML =
      '<div class="ag-cols">' +
      pane("CHOSEN", "what the model returned",
        JSON.stringify(CARD.plan_ids, null, 1),
        { cls: "is-short", tint: "json", right: CARD.plan_ids.length + " ids" }) +
      pane("COMPILED", "what the packager produced",
        runs
          .map((r) =>
            (r.unit ? "── " + r.unit + " ──\n" : "── probe ──\n") +
            r.moves.map((m) => "  " + fmt(m)).join("\n"))
          .join("\n"),
        { cls: "is-short", right: CARD.moves.length + " moves" }) +
      "</div>";

    $("#s09-log").innerHTML = pane(
      "PACKAGER LOG", "verbatim",
      CARD.packager_log.length
        ? CARD.packager_log.map((l) => "· " + l).join("\n\n")
        : "(nothing to report)",
      { cls: "is-short" },
    );
  });

  /* ── 10 · the wire, and the walk ───────────────────────────────── */

  const TIER_WORD = { pure: "PURE", mass: "mass", vein: "vein", trace: "trace" };

  /** Describe what engine truth actually holds at a cell. */
  function describe(x, y) {
    const [tile, purity] = truthAt(x, y);
    if (tile === TILE.RED) return "RED " + TIER_WORD[tierOf(purity)] + " · p" + purity;
    if (tile === TILE.BLUE) return "BLUE · p" + purity;
    if (tile === TILE.GREEN) return "GREEN — already stripped, worth -100";
    return "empty";
  }

  scene(function s10() {
    $("#s10-wire").innerHTML = pane(
      "SUBMITTED TO THE ENGINE", "submit_policy(moves=[...])",
      JSON.stringify(CARD.moves)
        .replace(/^\[/, "[\n  ")
        .replace(/\},\{/g, "},\n  {")
        .replace(/\]$/, "\n]"),
      { cls: "is-mid", tint: "json", right: CARD.moves.length + " moves" },
    );

    // Both runs plus both probes fit in this window; the full board at a
    // legible cell size would not fit the column.
    const crop = new Crop({ x0: 14, y0: 3, x1: 33, y1: 25 });
    const board = renderBoard($("#s10-board"), truthCells(crop), {
      cols: crop.cols, rows: crop.rows, cellW: 21, cellH: 21,
    });

    $("#s10-legend").innerHTML =
      '<span class="ag-key-route"><i></i>walked this night</span>' +
      '<span style="color:var(--seat-p1)"><i></i>your harvester</span>' +
      '<span class="ag-key-pure"><i></i>pure red</span>' +
      '<span class="ag-key-smear"><i></i>rival sign smear</span>';

    for (const s of BOARD.redsigns) {
      if (s.mine) continue;
      for (const [x, y] of s.cells) mark(board, crop, x, y, "ag-mark-smear");
    }

    const UNIT_LABEL = { harvester_p1: "harvester 1", harvester_p1_2: "harvester 2" };
    const NOTES = {
      1: "Land on the live frontier — the one cell near the seam p1 can still " +
         "legally drop on, because its probe lights it.",
      5: "Onto the pure at (31,18). 255 at the pure multiplier: 765 points, " +
         "the single biggest cell on the board.",
      6: "Lift. Cargo is only banked once the harvester is back in orbit — " +
         "anything still on the ground when the night ends is lost.",
      7: "A probe onto (16,9) — the cell the rival's own probe is sitting on. " +
         "It does two jobs at once: landing on theirs blinds it while yours " +
         "survives, and the new disk is what makes the next drop legal.",
      8: "The second harvester lands beside the RIVAL's sign, forty cells " +
         "away. When this route was chosen nobody on this seat had ever seen " +
         "any of this ground — the option's yield line reads simply " +
         "\"unknown\".",
      13: "The comb steps onto (16,6), which engine truth says is the rival's " +
          "pure. Found blind. Steps are not vision-gated, and that is the " +
          "entire basis of attacking a seam you cannot see.",
      15: "The last probe lands on the rival's probe at (20,24) — a supersede. " +
          "It banks nothing; it takes two nights of vision away from p2.",
    };

    const steps = CARD.moves.map((m, i) => {
      const n = i + 1;
      const at = m.at || m.to;
      let head;
      if (m.a === "probe") head = "probe → (" + at + ")";
      else if (m.a === "drop") head = "drop " + UNIT_LABEL[m.unit] + " @ (" + at + ")";
      else if (m.a === "pickup") head = "pickup " + UNIT_LABEL[m.unit];
      else head = "step → (" + at + ")";
      return {
        label: head,
        n, m, at,
        text:
          "<p><b>H" + String(n).padStart(2, "0") + " · " + esc(head) + "</b></p>" +
          (at ? "<p>Engine truth here: " + esc(describe(at[0], at[1])) + "</p>" : "") +
          (NOTES[n] ? "<p>" + NOTES[n] + "</p>" : ""),
      };
    });

    const trails = {};
    const holder = document.createElement("div");
    $("#s10-controls").appendChild(holder);
    stepper(holder, steps, (i) => {
      // Cumulative replay: rebuild from move 1 so scrubbing backwards works.
      for (const k in trails) delete trails[k];
      for (const el of board.children) {
        el.classList.remove("ag-mark-route", "ag-mark-drop", "ag-mark-pure");
        setEntity(el, "");
        const t = el.querySelector(".mn-cell-trail");
        t.textContent = "";
        t.classList.remove("is-on");
      }
      const heads = {};
      for (let k = 0; k <= i; k++) {
        const m = steps[k].m;
        const at = m.at || m.to;
        if (m.a === "probe") {
          const el = cellOf(board, crop, at[0], at[1]);
          if (el) setProbe(el, 3, "--seat-p1");
          continue;
        }
        if (m.a === "pickup") {
          delete heads[m.unit];
          continue;
        }
        const idx = crop.idx(at[0], at[1]);
        const el = cellOf(board, crop, at[0], at[1]);
        if (el) {
          bumpTrail(el, trails, idx);
          el.classList.add(m.a === "drop" ? "ag-mark-drop" : "ag-mark-route");
        }
        heads[m.unit] = at;
      }
      for (const unit in heads) {
        const el = cellOf(board, crop, heads[unit][0], heads[unit][1]);
        if (el) setEntity(el, GLYPH_HARVESTER, "--seat-p1");
      }
      for (const s of BOARD.redsigns) {
        for (const [x, y] of s.pure_cells) mark(board, crop, x, y, "ag-mark-pure");
      }
      $("#s10-label").textContent = "H" + String(i + 1).padStart(2, "0");
      $("#s10-text").innerHTML = steps[i].text;
    }, { autoMs: 950 });
  });

  /* ── 11 · memory ───────────────────────────────────────────────── */

  const MEM = [
    {
      k: "TONIGHT", n: "intent", f: "chat_schema.py",
      d: "The PLAN JSON carries an `intent`: one sentence on what tonight is " +
         "for. It changes nothing mechanically. It exists purely so there is " +
         "a claim on record to check tomorrow.",
    },
    {
      k: "OVERNIGHT", n: "replay frames", f: "SOC_REPLAY_FRAME",
      d: "The simulator resolves every seat hour by hour and persists what " +
         "happened at each one — drops, steps, harvests, collisions, jams, " +
         "losses. This is engine testimony, written by the engine.",
    },
    {
      k: "TOMORROW", n: "reconcile", f: "last_night.py",
      d: "The harness reads those frames back and builds the LAST NIGHT " +
         "block: your orders, the per-hour log of what became of them, the " +
         "yield actually banked against the yield expected, and what your " +
         "vision picked up about rivals.",
    },
    {
      k: "TOMORROW", n: "the thread", f: "journal.py",
      d: "One compressed line per night — intent, outcome, sightings, " +
         "reflection — so the agent can see a pattern across a season " +
         "without carrying a season's worth of text.",
    },
    {
      k: "TOMORROW", n: "answer for it", f: "prompt.py",
      d: "The card demands a `reflection` anchored to the log: did what " +
         "happened match the intent, and if not, name the hour and the cause. " +
         "That reflection becomes tomorrow's journal entry, and the loop " +
         "closes.",
    },
  ];

  scene(function s11() {
    const flow = $("#s11-flow");
    flow.innerHTML = MEM.map(
      (s, i) =>
        '<div class="ag-flow-step' + (i === 0 ? " is-on" : "") +
        '" data-i="' + i + '"><span class="k">' + s.k + "</span>" +
        '<span class="n">' + esc(s.n) + "</span>" +
        '<span class="f">' + esc(s.f) + "</span></div>",
    ).join('<span class="ag-flow-arrow">›</span>');

    const detail = $("#s11-detail");
    function show(i) {
      [...flow.querySelectorAll(".ag-flow-step")].forEach((el, j) =>
        el.classList.toggle("is-on", i === j));
      const s = MEM[i];
      detail.innerHTML =
        '<div class="mn-stage-caption" style="max-width:none">' +
        '<div class="mn-stage-label">' + esc(s.n) + "</div>" +
        '<div class="mn-stage-body">' + esc(s.d) + "</div>" +
        '<div class="mn-stage-cite dim">' + esc(s.f) + "</div></div>";
    }
    flow.addEventListener("click", (e) => {
      const el = e.target.closest("[data-i]");
      if (el) show(+el.dataset.i);
    });
    show(0);

    $("#s11-lastnight").innerHTML = pane(
      "LAST NIGHT", "verbatim, from the card", CARD.excerpts.last_night,
      { cls: "is-tall", tint: "prompt" },
    );
    $("#s11-journal").innerHTML = pane(
      "STRATEGY JOURNAL", "verbatim, from the card", CARD.excerpts.journal,
      { cls: "is-mid", tint: "prompt" },
    );
  });

  /* ── 12 · the module map ───────────────────────────────────────── */

  // `ev` is the thing on THIS card you can point at as that module's output.
  // Modules with no `ev` are part of the harness but left nothing identifiable
  // on this particular night — claiming otherwise would be guesswork.
  const MODULES = [
    ["harness.py", "Entry point and turn pipeline: build the view, call the " +
      "model twice, package, submit, remember.", "the turn itself"],
    ["prompt.py", "Assembles the card — section order, headings, wording.",
      "the card's structure"],
    ["rules.py", "Engine mechanics the agent must obey. The physics half of " +
      "section 1, and deliberately separate from the strategy half.",
      "section 1 mechanics"],
    ["doctrine.py", "The strategy half: the value pyramid, redsign combat, " +
      "risk profiles, named plays.", "section 1 doctrine"],
    ["world_view.py", "Everything in live sight, as JSON.", "the 17 cells"],
    ["out_of_grid.py", "Signs and echoes — what is known without being seen.",
      "the OUT-OF-GRID block"],
    ["value_pyramid.py", "Provenance-tagged ranking, and the force-surface " +
      "rule that guarantees a takeable pure is always on the menu.",
      "GRAB1, the forced pure grab"],
    ["seam_control.py", "Redsign campaigns — multi-wave attack patterns for " +
      "your own seam and for a rival's.",
      "WALKIN_* and BLIND_* options"],
    ["supersede.py", "Picks which rival probe is worth landing on.",
      "SS1 and SS2"],
    ["agency.py", "Builds the option registry and renders the menu.",
      "the option IDs"],
    ["option_economics.py", "Prices every option: yield, odds, crush, " +
      "collision risk.", "the risk lines"],
    ["comb_shapes.py", "Walk geometry — serpentines and combs over a disk, " +
      "with the value gradient that decides which way each step turns.",
      "the 6-cell blind comb"],
    ["chat_schema.py", "The PLAN response schema the model must satisfy.",
      "the JSON fields"],
    ["packager.py", "Option IDs to wire moves. Legality only.",
      "the log and the 15 moves"],
    ["last_night.py", "Reads engine replay frames back into a memory block.",
      "the LAST NIGHT block"],
    ["journal.py", "The continuous season thread.", "days 1-3"],
    ["card.py", "Writes the audit card this guide is built from.",
      "this whole document"],
    ["frontier.py", "Exploration probe placement — enemy-aware, edge-seeking, " +
      "and jittered so two seats do not converge on the same cell.", null],
    ["hazard_memory.py", "Persistent hazards across nights: EMP scars and " +
      "the trails rivals have already stripped.", null],
    ["speculative.py", "Hot drops into fog when nothing better is lit — which " +
      "on this night it was.", null],
    ["chain_filter.py", "Dedupes candidate chains by body overlap and drops " +
      "the ones under a value floor, so the menu is not four spellings of one " +
      "walk.", null],
    ["hint_dispersion.py", "Seat-differentiated dispersion, so two agents " +
      "reading the same board do not both pick the same drop.", null],
    ["digest.py", "Turns raw event channels into causal prose for the recap.", null],
    ["orbit.py", "The dawn phase — refining, building, shipping. A separate " +
      "call from the night.", null],
  ];

  const BOUNDARY = [
    { k: "READ", n: "build_agent_view", f: "one seat's fogged view, per turn",
      engine: true },
    { k: "WRITE", n: "submit_policy", f: "the night's moves", engine: true },
    { k: "WRITE", n: "submit_orbit_actions", f: "the dawn phase", engine: true },
  ];

  scene(function s12() {
    $("#s12-mods").innerHTML = MODULES.map(
      ([f, d, ev]) =>
        '<div class="ag-mod' + (ev ? " is-fired" : "") + '">' +
        '<div class="f">' + esc(f) + "</div>" +
        '<div class="d">' + esc(d) + "</div>" +
        (ev ? '<div class="fired">ON THIS CARD: ' + esc(ev).toUpperCase() +
          "</div>" : "") +
        "</div>",
    ).join("");

    $("#s12-boundary").innerHTML = BOUNDARY.map(
      (s) =>
        '<div class="ag-flow-step is-engine"><span class="k">' + s.k + "</span>" +
        '<span class="n">' + esc(s.n) + "</span>" +
        '<span class="f">' + esc(s.f) + "</span></div>",
    ).join('<span class="ag-flow-arrow">·</span>');
  });

  // <<SCENES>>

  // ═══ RUN ═════════════════════════════════════════════════════════

  document.addEventListener("DOMContentLoaded", () => {
    initNav();
    for (const fn of scenes) {
      try {
        fn();
      } catch (err) {
        // One broken section must not blank the whole page.
        console.error("scene failed:", fn.name || "(anon)", err);
      }
    }
    const foot = document.getElementById("ag-foot");
    if (foot) {
      foot.innerHTML =
        "Every figure on this page is generated from one real turn — snapshot " +
        "<code>" + esc(D.snapshot) + "</code>, day " + BOARD.day + " of " +
        BOARD.day_cap + ", seat p1 — by " +
        "<code>scripts/export_agent_guide_data.py</code> reading " +
        "<code>" + esc(D.card_path) + "</code>.<br>" +
        "The harness itself is documented in " +
        "<code>sea_of_colours/orchestrator_2/harnesses/tabula_v12/README.md</code>, " +
        "and the engine boundary in <code>ENGINE_INTERFACE.md</code>.";
    }
  });
})();
