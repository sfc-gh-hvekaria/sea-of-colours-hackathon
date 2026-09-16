/*
  The divergence view — one turn, two agents, everything they were given
  and everything they said, lined up.

  This is the page that answers "how is mine different from V12". It is
  built as a standalone document and opened in its own tab rather than
  squeezed into the drawer, because the interesting artefacts here are
  an 84KB prompt and a 1300-word reasoning pass, and neither of those
  fits in a 350px column.

  It loads as a plain <script> into a document the opener wrote, so it
  never fetches: the two takes are handed to it on window.__LAB_DIFF__.
  Same reason the battle room works that way — a page you might open off
  disk cannot rely on a server being there.

  The page reads in the order a person actually asks the questions:
  what did it do, why did it say it did that, what was it offered, and
  only then the whole prompt it was working from.
*/
(function () {
  "use strict";

  const DATA = window.__LAB_DIFF__ || {};
  const MINE = DATA.mine || {};
  const BASE = DATA.base || {};

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function slug(s) {
    return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "").slice(0, 40) || "x";
  }

  // ── the diff ────────────────────────────────────────────────────────
  //
  // A plain LCS over lines. Deliberately not a word-level or fuzzy diff:
  // the two things being compared are usually the SAME prompt with a
  // handful of lines moved, and a cleverer algorithm tends to report
  // that as a large rewrite. Lines are the unit a reader can act on.

  function lcsTable(a, b) {
    // Rolled to two rows — an 84KB prompt is ~1800 lines, and the full
    // table would be 3.2M cells for no benefit.
    const m = a.length, n = b.length;
    const table = [];
    let prev = new Uint32Array(n + 1);
    for (let i = m - 1; i >= 0; i--) {
      const cur = new Uint32Array(n + 1);
      for (let j = n - 1; j >= 0; j--) {
        cur[j] = a[i] === b[j] ? prev[j + 1] + 1 : Math.max(prev[j], cur[j + 1]);
      }
      table.push(cur);
      prev = cur;
    }
    table.reverse();
    table.push(new Uint32Array(n + 1));
    return table;
  }

  /** Walk two sequences of anything comparable with ``===``. */
  function diffSeq(a, b) {
    const t = lcsTable(a, b);
    const out = [];
    let i = 0, j = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { out.push({ op: "same", text: a[i] }); i++; j++; }
      else if (t[i + 1][j] >= t[i][j + 1]) { out.push({ op: "del", text: a[i] }); i++; }
      else { out.push({ op: "add", text: b[j] }); j++; }
    }
    while (i < a.length) out.push({ op: "del", text: a[i++] });
    while (j < b.length) out.push({ op: "add", text: b[j++] });
    return out;
  }

  // ── diffing two documents ───────────────────────────────────────────
  //
  // `diffSeq` above builds a full LCS table, which is O(n·m) in both time
  // and memory. That is fine for the tokens of one line and hopeless for
  // two prompts: a take runs to about 4,100 lines a side, so the table
  // wants 17 million cells. There used to be a guard at six million that
  // returned a apology row instead of a diff — and because the caller
  // counts changed rows to print "N lines, M changed", the apology
  // counted as zero and the page confidently reported that two visibly
  // different takes were identical. Every raw view was over the guard,
  // so it was never anything else.
  //
  // Three passes replace it, cheapest first, and none of them can give
  // up. Common prefix and suffix come off, because two prompts from the
  // same harness share long stretches at both ends. What is left is cut
  // at *anchors* — lines occurring exactly once on each side, matched up
  // in order (patience diff). Prompt text is full of such lines, so this
  // shatters the problem into chunks of a few lines each. Only those
  // chunks reach Myers, whose cost is driven by how *different* the two
  // sides are rather than how long they are.
  //
  // The result is an exact, minimal-ish edit script over the whole
  // document, so the count printed on the page is always a count of
  // something that was actually computed.

  function interner() {
    const ids = new Map();
    return (s) => {
      let v = ids.get(s);
      if (v === undefined) { v = ids.size; ids.set(s, v); }
      return v;
    };
  }

  /** Myers' O(ND) diff. Cost scales with the size of the edit, not the
   *  size of the input, which is what makes the anchored chunks cheap. */
  function myers(a, b) {
    const n = a.length, m = b.length;
    if (!n && !m) return [];
    if (!n) return b.map((t) => ({ op: "add", text: t }));
    if (!m) return a.map((t) => ({ op: "del", text: t }));

    // Myers keeps one row of its search per edit, so a chunk that is
    // both long and wholly rewritten wants a lot of memory. Reaching
    // here with one means prefix/suffix trimming and anchoring both
    // found nothing to hold on to — the two sides share no line that
    // occurs once on each — so "all of this replaced all of that" is
    // very close to the minimal answer anyway. It is a *correct* edit
    // script either way, which is what matters: the row count stays a
    // real count of real changes rather than a number stood in for one.
    if (n + m > 3000) {
      return a.map((t) => ({ op: "del", text: t }))
        .concat(b.map((t) => ({ op: "add", text: t })));
    }

    const max = n + m, off = max;
    let v = new Int32Array(2 * max + 1);
    const trace = [];
    for (let d = 0; d <= max; d++) {
      trace.push(v.slice());
      for (let k = -d; k <= d; k += 2) {
        let x = (k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]))
          ? v[off + k + 1]
          : v[off + k - 1] + 1;
        let y = x - k;
        while (x < n && y < m && a[x] === b[y]) { x++; y++; }
        v[off + k] = x;
        if (x >= n && y >= m) return myersWalk(trace, a, b, off);
      }
    }
    return myersWalk(trace, a, b, off);
  }

  function myersWalk(trace, a, b, off) {
    const out = [];
    let x = a.length, y = b.length;
    for (let d = trace.length - 1; d >= 0; d--) {
      const v = trace[d];
      const k = x - y;
      const prevK = (k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]))
        ? k + 1 : k - 1;
      const prevX = v[off + prevK];
      const prevY = prevX - prevK;
      while (x > prevX && y > prevY) { x--; y--; out.push({ op: "same", text: a[x] }); }
      if (d > 0) {
        if (x > prevX) { x--; out.push({ op: "del", text: a[x] }); }
        else if (y > prevY) { y--; out.push({ op: "add", text: b[y] }); }
      }
    }
    return out.reverse();
  }

  /** Lines that occur exactly once on each side, paired up. These are
   *  the only matches patience trusts, which is what stops a diff
   *  anchoring on a blank line or a repeated separator. */
  function anchors(a, b) {
    const ca = new Map(), cb = new Map(), at = new Map();
    for (const s of a) ca.set(s, (ca.get(s) || 0) + 1);
    b.forEach((s, j) => { cb.set(s, (cb.get(s) || 0) + 1); at.set(s, j); });

    const pairs = [];
    a.forEach((s, i) => {
      if (ca.get(s) === 1 && cb.get(s) === 1) pairs.push([i, at.get(s)]);
    });

    // Longest increasing subsequence over the b-side indices: the
    // biggest set of anchors that do not cross each other.
    const tails = [], back = new Array(pairs.length).fill(-1), idx = [];
    for (let p = 0; p < pairs.length; p++) {
      const j = pairs[p][1];
      let lo = 0, hi = tails.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (tails[mid] < j) lo = mid + 1; else hi = mid;
      }
      tails[lo] = j;
      idx[lo] = p;
      back[p] = lo > 0 ? idx[lo - 1] : -1;
      }
    const out = [];
    for (let p = tails.length ? idx[tails.length - 1] : -1; p >= 0; p = back[p]) {
      out.push(pairs[p]);
    }
    return out.reverse();
  }

  /** Patience: split at anchors, Myers the gaps. */
  function patience(a, b, out, depth) {
    if (!a.length && !b.length) return;
    if (!a.length || !b.length || depth > 6) {
      for (const r of myers(a, b)) out.push(r);
      return;
    }
    const found = anchors(a, b);
    if (!found.length) {
      for (const r of myers(a, b)) out.push(r);
      return;
    }
    let i = 0, j = 0;
    for (const [ai, bj] of found) {
      patience(a.slice(i, ai), b.slice(j, bj), out, depth + 1);
      out.push({ op: "same", text: a[ai] });
      i = ai + 1; j = bj + 1;
    }
    patience(a.slice(i), b.slice(j), out, depth + 1);
  }

  function diffLines(oldText, newText) {
    const aText = String(oldText || "").split("\n");
    const bText = String(newText || "").split("\n");

    // Compare by interned id: two prompt lines can run to hundreds of
    // characters, and the inner loop asks "are these equal?" constantly.
    const num = interner();
    const a = aText.map(num), b = bText.map(num);

    let head = 0;
    while (head < a.length && head < b.length && a[head] === b[head]) head++;
    let tail = 0;
    while (tail < a.length - head && tail < b.length - head
           && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;

    const rows = [];
    for (let i = 0; i < head; i++) rows.push({ op: "same", text: aText[i] });

    const mid = [];
    patience(a.slice(head, a.length - tail), b.slice(head, b.length - tail), mid, 0);
    // Patience worked on ids; put the text back.
    let ai = head, bi = head;
    for (const r of mid) {
      if (r.op === "add") rows.push({ op: "add", text: bText[bi++] });
      else if (r.op === "del") rows.push({ op: "del", text: aText[ai++] });
      else { rows.push({ op: "same", text: aText[ai++] }); bi++; }
    }

    for (let i = a.length - tail; i < a.length; i++) {
      rows.push({ op: "same", text: aText[i] });
    }
    return annotate(rows);
  }

  // ── inside a changed line ───────────────────────────────────────────
  //
  // Lines are the right unit to diff on and the wrong unit to *read* a
  // change in. A prompt line here runs to several hundred characters
  // and wraps over eight visual rows, so the edit — `area_gain=63`
  // against `area_gain=66`, or `0 parcel(s)` against `1` — is invisible
  // unless it is pointed at. Rendered plain, a red row above a green
  // one sharing the same first sixty characters reads as the diff being
  // broken, which is what sent us here.

  function tokenise(s) {
    // Whitespace is a token in its own right, so a pair that differs
    // only in spacing highlights the gap rather than claiming a change
    // with nothing visible in it.
    return String(s == null ? "" : s).split(/(\s+)/).filter((t) => t !== "");
  }

  //: Below this much shared text the pair is a replacement rather than
  //: an edit, and marking every token just paints the whole row.
  const EDIT_FLOOR = 0.45;

  function highlight(oldLine, newLine) {
    const parts = diffSeq(tokenise(oldLine), tokenise(newLine));
    let kept = 0, total = 0;
    for (const p of parts) {
      total += p.text.length;
      if (p.op === "same") kept += p.text.length;
    }
    if (!total || kept / total < EDIT_FLOOR) return null;
    const side = (want) => parts
      .filter((p) => p.op === "same" || p.op === want)
      .map((p) => (p.op === "same" ? esc(p.text) : `<mark>${esc(p.text)}</mark>`))
      .join("");
    return { del: side("del"), add: side("add") };
  }

  /** Mark up the rows a reader would otherwise call a false positive.
   *
   *  Two of them. A line that was rewritten gets its changed tokens
   *  highlighted. A line that is byte-identical but appears on both
   *  sides was *moved* — the LCS is entitled to report that as a delete
   *  plus an add, and it is correct, but shown in red and green it
   *  looks like a change to text that plainly did not change.
   */
  function annotate(rows) {
    const gone = new Set(), came = new Set();
    for (const r of rows) {
      if (!r.text || !r.text.trim()) continue;
      if (r.op === "del") gone.add(r.text);
      else if (r.op === "add") came.add(r.text);
    }
    for (const r of rows) {
      if ((r.op === "del" || r.op === "add")
          && gone.has(r.text) && came.has(r.text)) {
        r.moved = true;
      }
    }

    // A run of deletions followed by a run of additions is one passage
    // rewritten, in order — so pair them off and diff each pair.
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].op !== "del") continue;
      let d = i;
      while (d < rows.length && rows[d].op === "del") d++;
      let a = d;
      while (a < rows.length && rows[a].op === "add") a++;
      for (let k = 0; k < Math.min(d - i, a - d); k++) {
        const x = rows[i + k], y = rows[d + k];
        if (x.moved || y.moved) continue;
        const hl = highlight(x.text, y.text);
        if (!hl) continue;
        x.html = hl.del;
        y.html = hl.add;
        // The one difference that cannot be seen even highlighted.
        if (x.text.trim() === y.text.trim()) { x.ws = true; y.ws = true; }
      }
      i = a - 1;
    }
    return rows;
  }

  /** Collapse long stretches of agreement — the changes are the point. */
  function withFolds(rows, context) {
    const ctx = context == null ? 3 : context;
    const keep = new Array(rows.length).fill(false);
    rows.forEach((r, i) => {
      if (r.op === "same") return;
      for (let k = Math.max(0, i - ctx); k <= Math.min(rows.length - 1, i + ctx); k++) {
        keep[k] = true;
      }
    });
    const out = [];
    let run = 0;
    rows.forEach((r, i) => {
      if (keep[i]) {
        if (run) { out.push({ op: "fold", text: `${run} identical line${run === 1 ? "" : "s"}` }); run = 0; }
        out.push(r);
      } else run++;
    });
    if (run) out.push({ op: "fold", text: `${run} identical line${run === 1 ? "" : "s"}` });
    return out;
  }

  /** Whether a diff is even the right thing to show here. */
  function compare(oldText, newText) {
    const a = String(oldText == null ? "" : oldText).trim();
    const b = String(newText == null ? "" : newText).trim();
    if (!a && !b) return { kind: "neither" };
    // A heuristic has no prompt and no reasoning at all, so diffing V12's
    // 84KB think prompt against nothing yields a thousand deletion rows
    // that say one thing: it isn't an LLM. Say that instead.
    if (!a || !b) {
      return { kind: "one", text: a || b, who: a ? "base" : "mine" };
    }
    return { kind: a === b ? "same" : "differ", a, b };
  }

  const WHO = { base: "V12", mine: "this agent" };

  function renderDiff(oldText, newText) {
    const cmp = compare(oldText, newText);
    if (cmp.kind === "neither") return '<div class="empty">neither has this</div>';
    if (cmp.kind === "one") {
      const has = WHO[cmp.who], lacks = WHO[cmp.who === "base" ? "mine" : "base"];
      const n = cmp.text.split("\n").length;
      return `<div class="onesided">only <b>${esc(has)}</b> has this `
        + `— ${n} line${n === 1 ? "" : "s"}. <b>${esc(lacks)}</b> produced `
        + `nothing here, so there is nothing to line up against.</div>`
        + `<details><summary>show it anyway</summary>`
        + `<pre>${esc(cmp.text)}</pre></details>`;
    }
    const rows = withFolds(diffLines(oldText, newText));
    if (!rows.some((r) => r.op === "add" || r.op === "del")) {
      // Folded, not dropped. "Identical" on its own asks the reader to
      // take the page's word for it, and this page exists because its
      // word was once wrong — so the text it is making the claim about
      // stays one click away.
      const n = String(oldText || "").split("\n").length;
      return '<div class="identical">identical</div>'
        + `<details><summary>show the ${n} line${n === 1 ? "" : "s"} both `
        + "were handed</summary>"
        + `<pre>${esc(String(oldText || ""))}</pre></details>`;
    }
    return renderRows(rows);
  }

  function badgeFor(oldText, newText) {
    const cmp = compare(oldText, newText);
    if (cmp.kind === "neither") return "";
    if (cmp.kind === "same") return '<span class="badge same">identical</span>';
    if (cmp.kind === "one") {
      return `<span class="badge one">only ${esc(WHO[cmp.who])}</span>`;
    }
    const rows = diffLines(oldText, newText);
    // Moved lines are counted apart from real edits. Lumped in, a
    // section whose text is untouched but reordered scores "+9 −9" and
    // sends you looking for nine changes that are not there.
    const add = rows.filter((r) => r.op === "add" && !r.moved).length;
    const del = rows.filter((r) => r.op === "del" && !r.moved).length;
    const moved = rows.filter((r) => r.op === "del" && r.moved).length;
    if (!add && !del) return `<span class="badge">${moved} moved</span>`;
    return `<span class="badge">+${add} −${del}`
      + `${moved ? ` ↕${moved}` : ""}</span>`;
  }

  // ── the orders, which get their own treatment ───────────────────────
  //
  // A move list is not prose. Diffing it as text buries the thing you
  // actually want — that hour 3 went from a step to a probe — under
  // punctuation, so the two lists are laid out hour by hour instead.

  const VERB_CLASS = {
    drop: "v-drop", step: "v-step", pickup: "v-pickup", probe: "v-probe",
    emp_launch: "v-weapon", chaff_flare: "v-weapon", wait: "v-wait",
  };

  function moveText(m) {
    if (!m || typeof m !== "object") return String(m == null ? "" : m);
    const verb = String(m.a || "");
    const at = Array.isArray(m.at) ? m.at : Array.isArray(m.to) ? m.to : null;
    const cells = Array.isArray(m.cells) ? m.cells : null;
    const where = cells
      ? cells.map((c) => `(${c[0]},${c[1]})`).join(" ")
      : at ? `(${at[0]},${at[1]})` : "";
    const unit = m.u || m.unit || "";
    return [verb, unit, where].filter(Boolean).join(" ");
  }

  function verbOf(m) {
    return String((m && m.a) || "");
  }

  function renderOrders() {
    const mine = MINE.moves || [];
    const base = BASE.moves || [];
    const n = Math.max(mine.length, base.length);
    if (!n) return '<div class="empty">neither agent issued an order</div>';

    let rows = "";
    let same = 0;
    for (let i = 0; i < n; i++) {
      const a = base[i], b = mine[i];
      const at = moveText(a), bt = moveText(b);
      const agree = at === bt;
      if (agree) same++;
      const cls = (m) => VERB_CLASS[verbOf(m)] || "";
      rows +=
        `<tr class="${agree ? "agree" : "differ"}">`
        + `<td class="hour">H${String(i + 1).padStart(2, "0")}</td>`
        + `<td class="base ${cls(a)}">${a ? esc(at) : '<span class="none">—</span>'}</td>`
        + `<td class="mine ${cls(b)}">${b ? esc(bt) : '<span class="none">—</span>'}</td>`
        + "</tr>";
    }
    return (
      `<p class="tally">${same} of ${n} hours identical · `
      + `${n - same} differ</p>`
      + '<table class="orders"><thead><tr>'
      + '<th></th><th>V12 (baseline)</th><th>this agent</th>'
      + "</tr></thead><tbody>" + rows + "</tbody></table>"
    );
  }

  // ── the directive, field by field ───────────────────────────────────

  /** Flatten a directive field for display.
   *
   *  ``situational`` and ``targets`` are sometimes objects and sometimes
   *  strings depending on what the model returned, and ``String()`` on
   *  the object case gives "[object Object]" — which looks like a value
   *  and is not one.
   */
  function flat(v) {
    if (v == null || v === "") return "";
    if (typeof v === "string") return v;
    if (Array.isArray(v)) {
      return v.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(", ");
    }
    if (typeof v === "object") {
      return Object.entries(v)
        .filter(([, x]) => x !== "" && x != null)
        .map(([k, x]) => `${k}: ${typeof x === "string" ? x : JSON.stringify(x)}`)
        .join("\n");
    }
    return String(v);
  }

  function renderDirective() {
    const fields = [
      ["posture", (p) => p.posture],
      ["chose", (p) => p.selected_options],
      ["plan ids", (p) => p.plan_ids],
      ["targets", (p) => p.targets],
      ["avoid", (p) => p.avoid],
      ["note", (p) => p.note],
      ["situational", (p) => p.situational],
      ["sanitiser", (p) => p.sanitizer_changes],
      ["packager", (p) => String(!!p.packager_used)],
      ["fallback", (p) => String(!!p.fallback_used)],
    ];
    const mp = MINE.plan || {}, bp = BASE.plan || {};
    const rows = fields.map(([label, get]) => {
      const a = flat(get(bp)), b = flat(get(mp));
      const agree = a === b;
      return `<tr class="${agree ? "agree" : "differ"}">`
        + `<td class="k">${esc(label)}</td>`
        + `<td class="base">${a ? esc(a) : '<span class="none">—</span>'}</td>`
        + `<td class="mine">${b ? esc(b) : '<span class="none">—</span>'}</td></tr>`;
    }).join("");
    return '<table class="kv"><thead><tr><th></th><th>V12 (baseline)</th>'
      + "<th>this agent</th></tr></thead><tbody>" + rows + "</tbody></table>";
  }

  // ── the offer: what was on the menu ─────────────────────────────────
  //
  // V12 does not let the model invent coordinates. It precomputes a
  // catalogue of plays with the geometry filled in and asks the model to
  // pick IDs. So the menu is half the decision: an agent that was never
  // offered a play cannot have declined it, and a fork that changes what
  // goes on the menu has changed the agent even if the model is
  // untouched.
  //
  // Diffed as text this is unreadable — the renderer restates walk,
  // yield, crush and collision risk under every single row, so a menu
  // with one option added reports forty changed lines. Parsed back into
  // options and compared by ID, the same menu is one row per play with
  // the boilerplate folded away.

  function parseMenu(text) {
    const out = new Map();
    let group = "";
    let cur = null;
    for (const raw of String(text || "").split("\n")) {
      const line = raw.replace(/\s+$/, "");
      if (!line.trim()) continue;
      const opt = /^\s*\[([A-Za-z0-9_]+)\]\s*(.*)$/.exec(line);
      if (opt) {
        cur = { id: opt[1], group, head: opt[2].trim(), detail: [] };
        out.set(cur.id, cur);
        continue;
      }
      // Group headers end in a colon and sit at the left. Option detail
      // ("WHY: …", "WALK: …") also carries a colon but never ends on
      // one, which is what keeps the two apart.
      if (/:$/.test(line.trim()) && /^\s{0,4}\S/.test(line)) {
        group = line.trim().replace(/:$/, "");
        cur = null;
        continue;
      }
      if (cur) cur.detail.push(line.trim());
    }
    return out;
  }

  function menuText(o) {
    return o ? [o.head, ...o.detail].join("\n") : "";
  }

  function pickedBy(take) {
    const p = take.plan || {};
    return new Set([...(p.plan_ids || []), ...(p.selected_options || [])].map(String));
  }

  function renderMenu() {
    const bm = parseMenu((BASE.thinking || {}).option_menu);
    const mm = parseMenu((MINE.thinking || {}).option_menu);
    if (!bm.size && !mm.size) {
      return '<div class="empty">neither agent was shown an option menu — '
        + 'a heuristic picks its own geometry</div>';
    }
    const bp = pickedBy(BASE), mp = pickedBy(MINE);

    // Ordered by group, not by menu position: a play only this agent was
    // offered belongs beside the others of its kind, not orphaned at the
    // bottom under a second copy of its own heading.
    const order = [];
    const seen = new Set();
    const push = (id) => { if (!seen.has(id)) { seen.add(id); order.push(id); } };
    for (const [id, o] of bm) {
      push(id);
      for (const [oid, oo] of mm) if (oo.group === o.group) push(oid);
    }
    for (const id of mm.keys()) push(id);
    const ids = order;

    let rows = "", group = null, same = 0, reworded = 0, oneSided = 0;
    for (const id of ids) {
      const a = bm.get(id), b = mm.get(id);
      const g = (a || b).group;
      if (g !== group) {
        group = g;
        rows += `<tr class="grp"><td colspan="3">${esc(group || "ungrouped")}</td></tr>`;
      }
      let cls;
      if (!a || !b) { cls = "differ"; oneSided++; }
      else if (menuText(a) === menuText(b)) { cls = "agree"; same++; }
      else { cls = "differ"; reworded++; }

      const cell = (o, chosen) => {
        if (!o) return '<span class="none">not offered</span>';
        const body = o.detail.length
          ? `<details><summary>${o.detail.length} more line${o.detail.length === 1 ? "" : "s"}`
            + `</summary><pre>${esc(o.detail.join("\n"))}</pre></details>`
          : "";
        return `<div class="opt${chosen ? " picked" : ""}">`
          + `${chosen ? '<span class="tick">TOOK IT</span> ' : ""}`
          + `${esc(o.head)}</div>${body}`;
      };
      rows += `<tr class="${cls}"><td class="k">${esc(id)}</td>`
        + `<td class="base">${cell(a, bp.has(id))}</td>`
        + `<td class="mine">${cell(b, mp.has(id))}</td></tr>`;
    }

    const listed = (s) => (s.size ? [...s].join(", ") : "nothing");
    return (
      `<p class="tally">${bm.size} play(s) offered to V12 · ${mm.size} to this `
      + `agent · ${same} identical · ${reworded} reworded · ${oneSided} on one `
      + `menu only<br />V12 took <b>${esc(listed(bp))}</b> · this agent took `
      + `<b>${esc(listed(mp))}</b></p>`
      + '<table class="menu"><thead><tr><th>id</th><th>V12 (baseline)</th>'
      + '<th>this agent</th></tr></thead><tbody>' + rows + "</tbody></table>"
    );
  }

  // ── the prompt, taken apart ─────────────────────────────────────────
  //
  // V12 hands the model one card in three labelled sections and then
  // calls it three times with different closing contracts. Diffing the
  // three calls whole — which is what this page used to do — shows the
  // same 55KB of rules and doctrine three times over, because sections
  // 1 to 3 are byte-identical between the think and the plan pass. The
  // change you came here to find is a hundred characters somewhere in
  // that, buried three times.
  //
  // So: split each pass on its own banner lines and show every distinct
  // piece once, labelled with the passes that carry it. The split is
  // generic rather than a table of V12's section names, deliberately —
  // a fork is expected to rewrite this prompt, and a hardcoded list
  // would quietly swallow whatever it added.

  //: Boundaries that are not `=== … ===` banners. V12's agency layer
  //: writes these as bare title lines.
  const EXTRA_BOUNDARIES = [
    /^SITUATIONAL FACTS\b/,
    /^OPTION MENU\b/,
    /^OUTPUT CONTRACT\b/,
    /^OUTPUT ONE JSON OBJECT NOW\b/,
    /^OUTPUT THE JSON OBJECT NOW\b/,
    /^!!! SETUP NIGHT\b/,
  ];

  const MENU_PIECE = /^OPTION MENU\b/;

  function isBoundary(line) {
    const t = line.trim();
    // A banner has words between the equals. A bare rule of sixty of
    // them is a divider inside the redsign playbook, and treating those
    // as boundaries chopped SECTION 1 into eight untitled fragments.
    if (/^={3,}.*={3,}$/.test(t) && t.replace(/=/g, "").trim()) return true;
    return EXTRA_BOUNDARIES.some((re) => re.test(t));
  }

  function tidyTitle(line) {
    return line.trim().replace(/^=+\s*/, "").replace(/\s*=+$/, "").trim()
      || "(untitled)";
  }

  function splitPrompt(text) {
    const out = [];
    let cur = { title: "(opening)", lines: [] };
    for (const ln of String(text || "").split("\n")) {
      if (isBoundary(ln)) {
        out.push(cur);
        // The banner stays in its own body. Partly so the whole prompt
        // is accounted for — "honest" is the point of this view — and
        // partly because a fork that rewrites a heading has changed the
        // prompt, and dropping the heading would hide exactly that.
        cur = { title: tidyTitle(ln), lines: [ln] };
      } else cur.lines.push(ln);
    }
    out.push(cur);
    return out
      .map((p) => ({ title: p.title, body: p.lines.join("\n").trim() }))
      .filter((p) => p.body);
  }

  // ── the passes, and the one that usually is not a pass ──────────────
  //
  // V12 makes two model calls a night: THINK (prose) and PLAN (the
  // decision JSON — option ids, posture, intent). The moves are then
  // compiled from those ids by the PACKAGER, which is ordinary Python
  // and asks the model nothing.
  //
  // The MOVER is a third model call that only runs when the packager
  // has no recipe to compile — an off-seam night, a prose-recovery
  // miss. Its prompt is nevertheless BUILT every turn and recorded in
  // the audit whether or not it is sent, so a take from a normal night
  // carries a full mover prompt that no model ever read. Showing that
  // beside the two real ones invented a third pass V12 does not have.
  const PASSES = ["think", "plan", "mover"];
  const PASS_NOTE = {
    think: "pass 1 — the prose",
    plan: "pass 2 — the decision",
    mover: "fallback pass — only sent when the packager has no recipe",
  };

  /** Did this take's mover actually go to a model?
   *
   *  Both halves matter. A heuristic reports `packager_used: false`
   *  quite truthfully — it has no packager — so the flag alone reads
   *  every bot as having run a mover it does not possess. Requiring the
   *  prompt as well is what distinguishes "the packager declined" from
   *  "there was never a pipeline here".
   */
  function moverRan(take) {
    return !!(take.prompts || {}).mover && (take.plan || {}).packager_used === false;
  }

  /** The passes worth showing for one take. */
  function passesFor(take) {
    return PASSES.filter((p) => p !== "mover" || moverRan(take));
  }

  const MOVER_SHOWN = moverRan(BASE) || moverRan(MINE);

  /** A nav-sized name. V12's headings carry their own explanation in
   *  parentheses, which is useful in place and useless in a tab strip. */
  function shortNav(title) {
    const cut = title.replace(/\s*[(—-].*$/, "").trim() || title;
    return cut.length > 30 ? `${cut.slice(0, 29)}…` : cut;
  }

  /** ``title -> {think, plan, …}`` for the prompts this take really sent. */
  function promptPieces(take) {
    const out = new Map();
    for (const pass of passesFor(take)) {
      const text = (take.prompts || {})[pass] || "";
      if (!text) continue;
      for (const piece of splitPrompt(text)) {
        if (!out.has(piece.title)) out.set(piece.title, {});
        const slot = out.get(piece.title);
        // A title that recurs inside one pass is appended rather than
        // overwriting, so nothing is silently dropped.
        slot[pass] = slot[pass] ? `${slot[pass]}\n${piece.body}` : piece.body;
      }
    }
    return out;
  }

  const BASE_PIECES = promptPieces(BASE);
  const MINE_PIECES = promptPieces(MINE);

  /** One entry per distinct piece, collapsed across the passes.
   *
   *  Pieces that came through untouched are rolled into one foldaway
   *  block rather than getting a section each. Fourteen full-height
   *  boxes reading "identical" is the same complaint as showing the
   *  prompt three times — it buries the two that changed. They are
   *  still here in full, because "honest" means the whole prompt is on
   *  the page; they are just not shouting.
   */
  function promptSections() {
    const titles = [...BASE_PIECES.keys()];
    for (const t of MINE_PIECES.keys()) if (!titles.includes(t)) titles.push(t);

    const out = [];
    const quiet = { same: [], one: [] };
    for (const title of titles) {
      if (MENU_PIECE.test(title)) continue; // it has its own section
      const b = BASE_PIECES.get(title) || {};
      const m = MINE_PIECES.get(title) || {};
      const here = PASSES.filter((p) => b[p] != null || m[p] != null);
      if (!here.length) continue;

      // Group the passes by what they actually contain. Nearly always
      // one group of three — which IS the finding, so it gets said out
      // loud rather than shown three times.
      const groups = new Map();
      for (const pass of here) {
        const sig = `${b[pass] || ""}\u0000${m[pass] || ""}`;
        if (!groups.has(sig)) {
          groups.set(sig, { passes: [], a: b[pass] || "", z: m[pass] || "" });
        }
        groups.get(sig).passes.push(pass);
      }

      let n = 0;
      for (const g of groups.values()) {
        n++;
        const which = g.passes.join(" + ");
        const plural = g.passes.length > 1 ? "s" : "";
        const size = Math.max(g.a.length, g.z.length);
        // Two different "same"s live on this page and reading one as the
        // other is the fastest way to misread the whole view. This one
        // is ACROSS PASSES — V12 sends the same block to think and to
        // plan. The badge on the heading is the other one, across the
        // two AGENTS, and that is the one the page is actually about.
        const sub = (groups.size === 1 && here.length > 1
          ? `sent unchanged to both the ${which} passes`
          : `in the ${which} prompt${plural}`)
          + ` · ${size.toLocaleString()} chars`;

        const cmp = compare(g.a, g.z);
        if (cmp.kind === "same" || cmp.kind === "one") {
          quiet[cmp.kind].push({ title, sub, cmp });
          continue;
        }
        out.push({
          id: `q-${slug(title)}${n > 1 ? `-${n}` : ""}`,
          // Some closing contracts are a single 300-character sentence,
          // and it is the whole heading. The full text is in the body —
          // the banner line is kept there — so the head can be cut.
          title: title.length > 76 ? `${title.slice(0, 75)}…` : title,
          // Two entries for one section (think+plan, then mover) must be
          // tellable apart in the nav without opening both.
          nav: groups.size > 1 ? `${shortNav(title)} · ${which}` : shortNav(title),
          sub,
          html: renderDiff(g.a, g.z),
          badge: badgeFor(g.a, g.z),
          group: "the prompt, as sent",
        });
      }
    }

    const fold = (items) => '<div class="quiet">' + items.map((q) =>
      `<details><summary><b>${esc(q.title)}</b>`
      + `<span class="qsub">${esc(q.sub)}</span></summary>`
      + `<pre>${esc(q.cmp.text || q.cmp.a || "")}</pre></details>`).join("")
      + "</div>";

    if (quiet.same.length) {
      out.push({
        id: "q-unchanged", title: "Same in both agents",
        group: "the prompt, as sent",
        sub: `${quiet.same.length} piece(s) your fork and V12 were handed word `
          + "for word — nothing to compare, kept so the whole prompt is here",
        html: fold(quiet.same),
      });
    }
    if (quiet.one.length) {
      const who = WHO[quiet.one[0].cmp.who];
      out.push({
        id: "q-onesided", title: `Only in ${who}'s prompt`,
        group: "the prompt, as sent",
        sub: `${quiet.one.length} piece(s) with nothing on the other side to `
          + "line up against — a heuristic is not handed a prompt at all",
        html: fold(quiet.one),
      });
    }
    return out;
  }

  // ── raw ─────────────────────────────────────────────────────────────
  //
  // The whole take as one document, diffed top to bottom, in the order
  // the turn happened. No sectioning, no roll-ups, no per-pass grouping.
  //
  // It exists because the structured view answers "what differs, and
  // where" by taking the take apart, and taking a thing apart is exactly
  // what makes it hard to see whether two of them are the same. This is
  // the other question — "show me both, in full, with the changes marked"
  // — and it wants one scroll and one set of rules.

  function rawDoc(take) {
    const out = [];
    const push = (title, body) => {
      const text = String(body == null ? "" : body).trim();
      if (!text) return;
      out.push(`\n########## ${title} ##########\n`, text, "");
    };

    push("ISSUED ORDERS", (take.moves || []).map(
      (m, i) => `H${i + 1}  ${moveText(m)}`).join("\n"));

    const p = take.plan || {};
    push("THE DIRECTIVE IT COMMITTED TO", [
      ["posture", p.posture], ["plan ids", (p.plan_ids || []).join(", ")],
      ["targets", flat(p.targets)], ["avoid", flat(p.avoid)],
      ["note", p.note], ["situational", flat(p.situational)],
      ["selected options", (p.selected_options || []).join(", ")],
      ["executed by", p.packager_used ? "packager (no model call)" : "LLM mover"],
      ["fallback used", p.fallback_used ? "yes" : "no"],
      ["sanitiser changes", (p.sanitizer_changes || []).join("\n")],
    ].filter(([, v]) => String(v || "").trim())
      .map(([k, v]) => `${k}: ${v}`).join("\n"));

    const th = take.thinking || {};
    push("REASONING (pass 1 output)", th.reasoning);
    push("INTENT", th.intent);
    push("REFLECTION ON LAST NIGHT", th.reflection);
    push("RATIONALE (the game log line)", take.rationale);

    for (const pass of passesFor(take)) {
      push(`PROMPT — ${pass.toUpperCase()} (${PASS_NOTE[pass]})`,
        (take.prompts || {})[pass]);
    }
    return out.join("\n").trim();
  }

  function renderRaw(context) {
    const a = rawDoc(BASE), b = rawDoc(MINE);
    const rows = withFolds(diffLines(a, b), context);
    const changed = rows.filter(
      (r) => (r.op === "add" || r.op === "del") && !r.moved).length;
    const lines = Math.max(a.split("\n").length, b.split("\n").length);
    return { html: renderRows(rows), changed, lines };
  }

  /** The row painter, shared with `renderDiff` so raw and structured
   *  cannot drift into disagreeing about what a change looks like. */
  function renderRows(rows) {
    return (
      '<div class="diff">' +
      rows.map((r) => {
        if (r.op === "fold") return `<div class="row fold">⋯ ${esc(r.text)}</div>`;
        const sign = r.moved ? "↕" : r.op === "add" ? "+" : r.op === "del" ? "−" : " ";
        const cls = r.moved ? `${r.op} moved` : r.op;
        const note = r.ws
          ? '<span class="rownote">whitespace only</span>'
          : r.moved ? '<span class="rownote">moved, text unchanged</span>' : "";
        return `<div class="row ${cls}"><span class="sign">${sign}</span>`
          + `<span class="txt">${r.html || esc(r.text) || "&nbsp;"}${note}</span></div>`;
      }).join("") +
      "</div>"
    );
  }

  // ── page ────────────────────────────────────────────────────────────

  const PROSE = [
    ["reasoning", (t) => (t.thinking || {}).reasoning],
    ["intent", (t) => (t.thinking || {}).intent],
    ["reflection", (t) => (t.thinking || {}).reflection],
    ["rationale", (t) => t.rationale],
  ];

  /** How much agreement to keep around each change in the raw view.
   *  Effectively infinite by default: "the entire thing" is the point of
   *  the raw view, and a fold is a claim you have to take on trust. */
  const RAW_ALL = 1e9;
  let rawContext = RAW_ALL;
  let mode = "structured";

  function buildRaw() {
    const { html, changed, lines } = renderRaw(rawContext);
    document.getElementById("body").innerHTML = `
      <section id="raw">
        <h2>The whole take, line by line</h2>
        <p class="sub">Both takes flattened into one document in the order
        the turn happened — orders, directive, prose, then every prompt
        that was actually sent — and diffed end to end. ${lines.toLocaleString()}
        lines, ${changed.toLocaleString()} changed.</p>
        ${html}
      </section>`;
    document.getElementById("nav").innerHTML = "";
  }

  function build() {
    const secs = [];

    secs.push({
      id: "orders", title: "Issued orders", group: "what it did",
      sub: "what the engine actually accepted, hour by hour",
      html: renderOrders(),
    });
    secs.push({
      id: "directive", title: "The plan it chose", group: "what it did",
      sub: "the directive behind the orders",
      html: renderDirective(),
    });

    for (const [label, get] of PROSE) {
      const a = get(BASE) || "", b = get(MINE) || "";
      if (!a && !b) continue;
      secs.push({
        id: "p-" + slug(label), title: label, group: "what it said",
        sub: "", html: renderDiff(a, b), badge: badgeFor(a, b),
      });
    }

    secs.push({
      id: "menu", title: "The offer it received", group: "what it was offered",
      sub: "the precomputed plays it could pick by id — an agent cannot "
        + "decline a play it was never shown",
      html: renderMenu(),
    });

    secs.push(...promptSections());

    document.getElementById("body").innerHTML = secs.map((s) => `
      <section id="${s.id}">
        <h2>${esc(s.title)}${s.badge || ""}</h2>
        ${s.sub ? `<p class="sub">${esc(s.sub)}</p>` : ""}
        ${s.html}
      </section>`).join("");

    let seenGroup = null;
    document.getElementById("nav").innerHTML = secs.map((s) => {
      const lead = s.group !== seenGroup
        ? `<span class="navgrp">${esc(s.group)}</span>` : "";
      seenGroup = s.group;
      const label = s.nav
        || (s.title.length > 30 ? `${s.title.slice(0, 29)}…` : s.title);
      return `${lead}<a href="#${s.id}" title="${esc(s.title)}">${esc(label)}</a>`;
    }).join("");

  }

  /** How this take's moves were actually produced, in one line.
   *
   *  Worth stating outright: the pipeline is two model calls and a
   *  compiler, and every reading of this page depends on knowing that
   *  the moves usually come from Python rather than from a third
   *  prompt.
   */
  function pipelineNote() {
    const of = (take) => {
      if (!(take.prompts || {}).think) return "no prompts — not an LLM agent";
      return "think → plan → " + (moverRan(take)
        ? "LLM mover (the packager had no recipe)"
        : "packager (deterministic, no third model call)");
    };
    const b = of(BASE), m = of(MINE);
    // Saying it twice when it is the same twice is the repetition this
    // page was already too full of.
    if (b === m) return `both: ${b}`;
    return `V12: ${b} · ${MINE.agent || "yours"}: ${m}`;
  }

  function head() {
    const rec = (BASE.baseline || {}).recorded || "unknown";
    const btn = (id, label, on) =>
      `<button type="button" data-mode="${id}"`
      + `${on ? ' class="on"' : ""}>${label}</button>`;
    document.getElementById("head").innerHTML =
      `<h1>DIVERGENCE</h1>`
      + `<div class="meta">`
      + `<b>${esc(MINE.agent || "this agent")}</b> against `
      + `<b>${esc(BASE.agent || "tabula_v12")}</b>`
      + ` · seat ${esc(MINE.seat || "")} · day ${esc(MINE.day || "")}`
      + `<br />board ${esc(DATA.board || "")}`
      + `<br />baseline frozen ${esc(rec)}`
      + `<br />${esc(pipelineNote())}`
      + `</div>`
      + `<div class="modes"><span class="k">view</span>`
      + btn("structured", "[ STRUCTURED ]", mode === "structured")
      + btn("raw", "[ RAW ]", mode === "raw")
      + (mode === "raw"
        ? `<span class="k">show</span>`
          + `<button type="button" data-ctx="all"`
          + `${rawContext === RAW_ALL ? ' class="on"' : ""}>[ everything ]</button>`
          + `<button type="button" data-ctx="near"`
          + `${rawContext === RAW_ALL ? "" : ' class="on"'}>[ changes only ]</button>`
        : "")
      + `</div>`;

    for (const b of document.querySelectorAll(".modes button")) {
      b.addEventListener("click", () => {
        if (b.dataset.mode) mode = b.dataset.mode;
        if (b.dataset.ctx) rawContext = b.dataset.ctx === "all" ? RAW_ALL : 3;
        render();
      });
    }
  }

  function render() {
    if (mode === "raw") buildRaw(); else build();
    head();
    window.scrollTo(0, 0);
  }

  render();
})();
