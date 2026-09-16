# Building animation loops for the manual

This is a playbook for adding new interactive animation loops to the manual
(the "One Nox" loop on Tab 3 is the reference implementation). Every future
section that needs to explain a game concept — Vault refine, Solar jettison,
Chaff flare, Blue-sign, Weapons, EMP, Refinery — should follow the same
skeleton so the manual reads coherently and reuses the same helpers.

## Core principles

1. **Engine parity, not approximation.** Every visual (palette, glyphs,
   animation timing, FX) is a direct port of the actual game's rendering
   code in `server/static/app.js` and `server/static/styles.css`. When
   you build a new loop, hunt down the corresponding engine function first
   and port its constants verbatim. Cross-reference by line number in a
   comment above your port so the next person can diff.

2. **Stages, not scripts.** Every animation loop is a **sequence of
   numbered stages**. A stage has:
   - a `label` / `body` / `cite` for the caption panel
   - a `settle()` function that idempotently snaps the world to the
     "end of stage k" state (no animation)
   - an `animate()` function that plays the transition from stage k-1 to
     stage k
   - a `dur` in ms (used only by auto-play — manual step-through ignores it)

   Users navigate with `←` / `→` arrows and `[ ◄ PREV ]` / `[ NEXT ► ]`
   buttons. `[ ⏵ PLAY ]` toggles auto-advance.

3. **Deterministic replay.** `goStage(n)` cancels every pending timer +
   FX node, resets the board and vault, then silently runs
   `STAGES[0..n-1].settle()` before playing `STAGES[n].animate()`.
   Backward navigation is just forward-from-scratch through the target
   stage's settle chain. Never rely on animation callbacks to advance
   state — the settle() functions must be the single source of truth.

4. **State outside the stage.** Anything that survives across stages
   (harvester position, parcels in hold, probe rings, heat applied,
   vault contents) lives on `tab3_state`. Anything one-shot to a stage
   (a scheduled setTimeout, a floating ghost DOM node) goes in
   `state.timers` / `state.fxNodes` so `tab3_stop` and `goStage` can
   wipe it cleanly on transition.

## File layout

```
manual/
├── index.html        # skeleton: right-nav, tabs, tab panes, tooltip
├── manual.css        # palette from styles.css + all animation styles
├── manual.js         # single IIFE with paint/render + tab-N modules
└── ANIMATION_LOOPS.md  ← you are here
```

The whole thing is dependency-free. Open `index.html` with a `file://`
URL or serve from any static host.

## The rendering primitives (already built)

These live near the top of `manual.js`. Every loop composes them.

### `paintTile(tile, purity) → {fg, bg, ch}`

Mirrors `app.js:2155 computeParcelPaintFallback` and `render.py cell_visual`.
Same 4-tier ramp:

| Purity  | Glyph | Tier (RED)  | Tier (BLUE) |
|---------|-------|-------------|-------------|
| 1–50    | `░░`  | trace       | shallow     |
| 51–150  | `▒▒`  | vein        | mid         |
| 151–254 | `▓▓`  | mass        | sink        |
| 255     | `██`  | pure        | deep        |

Colours are pulled directly from the engine palette:

- `--tile-void: rgb(14,11,22)` — EMPTY / cell background
- `RED_FG = rgb(255,0,0)`
- `BLUE_FG = rgb(59,143,224)`
- `GREEN_FG = rgb(63,185,80)` (always full `██`, no tiers)

### `renderBoard(host, cells, {cols, rows, cellW, cellH}) → boardEl`

Absolute-positioned cell grid. Every cell is `position: absolute` with
explicit `left`/`top` computed from `(idx % cols) * cellW` and
`floor(idx / cols) * cellH`. We deliberately avoid CSS grid because
grid-auto-rows + inherited line-height inflated tracks in Chrome.

Each cell contains four stacked spans, drawn in this z-order:

1. `.mn-cell-terrain` — the tile paint (`██`, `▓▓`, etc.) from `paintTile`
2. `.mn-cell-trail` — path/harvest shading (`░░ ▒▒ ▓▓ ██` in off-white)
3. `.mn-cell-entity` — the harvester `X`, probe `·`, or orblift `▲`
4. `.mn-cell-fog` — engine-parity fog: `░░` in `--dim` at `opacity: 0.24`
   over the same `--tile-void` background as an empty tile. When
   `.mn-cell.is-fogged` is set, terrain/trail/entity are
   `visibility: hidden` — nothing beneath the fog leaks through.

### Mutation helpers

- `setTerrain(cellEl, tile, purity)` — repaint the terrain span
- `setEntity(cellEl, glyph, seatVar)` — place `X`/`·`/`▲`; strips any
  probe classes
- `setProbe(cellEl, nightsRemaining, seatVar)` — probe glyph with
  `nightsRemaining-1` concentric `box-shadow` rings on `::after`
  (2 rings = fresh probe; 1 = aging; 0 = bare)
- `bumpTrail(cellEl, trailByIdx, idx)` — increment visit count, upgrade
  the trail glyph to the next tier
- `setFog(cellEl, on)` — atomic toggle of the fog span + the parent's
  `.is-fogged` class

## The stage machine (per loop)

Every loop follows this shape. Copy it into a new `tabN_start()`.

```js
function tab3_start() {
  tab3_stop();
  const host = document.getElementById("mn-board-loop");
  // ... other DOM handles ...

  // 1. World data — cells and any seam/pocket layout.
  const cols = 20, rows = 12;
  const base = new Array(cols * rows).fill(null).map(() => ({
    tile: TILE.EMPTY, purity: 0, fog: true,
  }));
  const seamPlan = [ /* {x, y, tier} */ ];
  function rerollSeam() { /* stamp base[] with random purities per tier */ }
  rerollSeam();

  const board = renderBoard(host, base.map(c => ({...c})), { cols, rows, cellW: 22, cellH: 22 });

  // 2. Per-loop state.
  const state = {
    stopped: false, timers: [], fxNodes: new Set(),
    trailByIdx: {}, parcels: [], stageIdx: 0, autoPlay: false,
    // ... any other loop-specific bookkeeping ...
  };
  tab3_state = state;

  // 3. Utility helpers scoped to this loop (references board + state):
  //    - resetBoard(): repaint every cell from base[]; fog on
  //    - fireProbeRipple, spawnHarvestFx, animateStep, playAuroraSweep, playVesperaSweep, etc.
  //    - _settleXXX(): compose settle() functions so later stages can build on earlier ones.

  // 4. STAGES array (indices 0..N-1).
  const STAGES = [
    {
      label: "0 · NOX BEGINS",
      body:  "The planet is only harvestable during the night.",
      cite:  "§3.2",
      settle: () => {},          // fully fogged (default state after reset)
      animate: () => { pushLog(...); statusEl.textContent = ...; },
      dur: 2800,
    },
    // ... more stages ...
  ];

  // 5. goStage(idx): cancel + reset + settle 0..idx-1 + animate idx.
  function goStage(idx) {
    idx = ((idx % STAGES.length) + STAGES.length) % STAGES.length;
    state.timers.forEach(clearTimeout);
    state.timers = [];
    state.fxNodes.forEach(n => n.remove());
    state.fxNodes.clear();
    resetBoard(); resetVault(); state.parcels = [];
    for (let i = 0; i < idx; i++) STAGES[i].settle();
    state.stageIdx = idx;
    updateCaption(idx);
    STAGES[idx].animate();
    if (state.autoPlay) {
      state.timers.push(setTimeout(() => {
        goStage(idx + 1 >= STAGES.length ? 0 : idx + 1);
      }, STAGES[idx].dur));
    }
  }
  state.goStage = goStage;                            // exposed for arrow-key handler

  // 6. Button wiring + initial call.
  btnPrev.onclick = () => goStage(state.stageIdx - 1);
  btnNext.onclick = () => goStage(state.stageIdx + 1);
  // ... play toggle, restart button ...
  goStage(0);
}
```

## Animation primitives (drop-in for new loops)

### Probe launch — `spawnProbeTrail(cell, trailColor, probeColor, onLand, dur, state)`

Direct port of `app.js:8654-8804 spawnProbeTrail`. Fires from
`(toX+360, toY-360)` viewport coords to the centre of `cell` on a
**quadratic bezier** with a perpendicular midpoint offset. Trail is
stamped per-pixel via **Bresenham** into an ImageData buffer with
additive blend; landing flash is a `0.35*cw → 0.90*cw` square that
fades over 280ms. Trail pixels random-fade individually.

Use for: any ballistic launch (probes, chaff, EMP payloads).
Vary `trailColor` per weapon (probe = white, EMP = cyan, chaff = grey).
The minelayer arc used the same primitive; it is kept for archived
seasons only, since the caltrop was retired in v1.31 (RULEBOOK §4.9.4).

### Orblift arc — `runOrbitalArcAnimation(board, cell, kind, seatVar, glyph, onDeposit, state)`

Direct port of `app.js:7447-7616 runOrbitalArcAnimation`. One `▲`
glyph on a **cubic bezier**, sweeps east-west (or west-east random,
±20° tilt), exits off both sides. Duration 1100ms. Engine easing:
`t<=0.5 ? 0.5*(1-(1-u)^2.5) : 0.5+0.5*u^2.5` (fast at edges, slow at
midpoint). At **t=0.5**, cargo detaches (drop) / attaches (pickup).

Use for: harvester drop, harvester pickup, refinery ferry, any orbital
deposit/extract.

### Step ghost — `animateStep(prevCell, nextCell, seatVar, blinkColor, onArrive)`

Direct port of `app.js:7671-7712 step animation` +
`styles.css:6501 .replay-anim-ghost--step`. A floating `X` ghost slides
from source to destination over 180ms with `transition: transform
180ms steps(3, end)` (choppy pixelated feel, matches engine). Hides
both cells' entity overlays for the flight. On arrival: invokes
`onArrive()`; if `blinkColor` is non-null, fires `spawnHarvestFx`.

Use for: any unit walking one cell, or a "flick" animation for units
teleporting a short distance.

### Harvest FX — `spawnHarvestFx(cell, color)`

Direct port of `app.js:7619-7658 spawnHarvestEffects`. Two effects:
- **Blink**: absolutely-positioned overlay flashes `color` three times
  over 360ms with `step-start` timing (hard on/off, no fade). Masks the
  RED → GREEN terrain switch.
- **Particles**: 8 tiny squares (`22%` of cell size) fly outward on random
  angles at `1.8–3.2× cell width`, fading over 420ms.

Use for: harvests, refines, jettisons, any "this cell just did something".

### Aurora heat sweep — `playAuroraSweep(onDone)` + `playVesperaSweep(onDone)`

Direct port of `app.js:9353-9540 _runHeatSweep + _heatApplyCell`.
Diagonal front `x*0.85 + y*1.15` advances across the board. Per-cell:
- Non-fog: `filter: saturate(1+h*1.5) brightness(1+h*0.45)` on the
  terrain glyph, warm `text-shadow` bloom, and cell background lifts
  toward `rgb(cr+h*42, cg+h*32, cb+h*24)`.
- Fog: glyph swaps `░░ → ▒▒` in warming grey `rgb(90+h*165, …)`,
  background lifts to a warm dark.
- Shimmer: sine wobble on the glyph while `h > 0.15`.

`playAuroraSweep` holds indefinitely at `h=0.52` after the sweep-in
front reaches `MAX_D`; `playVesperaSweep` cools back to 0 via the
reverse front. `state.heatApplied` / `state.heatCells` / `state.heatRaf`
track the sweep between stages. In `goStage(0)`, if `heatApplied` is
true, play vespera as a prelude — the game loop's dawn→dusk transition.

Use for: any board-wide phase transition (Aurora, EMP field expiring
across the map, refinery burn).

### Vault fill + score tick — `fillVaultSlot(idx, tile, purity)` + `_animateScoreBy(delta)`

Any parcel-banking loop can reuse the 6-slot vault directly. Slots
stamp `data-purity`, `data-tier`, `data-mult`, `data-glyph` for the
hover tooltip. `_animateScoreBy` ease-cubics the running total from
current to `current + delta` over 500ms.

Use for: banking parcels, refining rewards, catapult shipping, any
running-total moment.

## Authoring a new loop — checklist

1. Add a new tab pane to `index.html` with a board host, controls,
   caption panel, event log, and (if scoring) a vault panel.
2. In `manual.js`, add `tabN_start()` / `tabN_stop()` following the
   stage-machine template.
3. Register the tab in `activateTab()` and in the `.mn-tabs` strip.
4. For each stage:
   - **Look up the engine's version** of whatever concept the stage
     illustrates. Port the timings and colour math verbatim. Comment
     the app.js line number.
   - Write the `settle()` function first — it's the ground truth. Make
     it idempotent (calling it twice must produce the same visual
     state, and it must not push to `state.parcels` more than once).
   - Then write `animate()` — assume the world is already at the "end
     of stage k-1" state. Do **not** re-run earlier `settle()` calls.
   - Log a line via `pushLog(kind, msg)` on every meaningful beat.
   - Update `statusEl.textContent` for the terse status line.
5. Wire the caption text (`label` / `body` / `cite`) with a rulebook
   citation. Every stage should cite at least one `§n.m`.
6. Test the whole loop three ways:
   - Auto-play from stage 0 through end and back
   - Manual `←` / `→` walk, forward and backward
   - Restart button mid-stage — should not leak timers / FX nodes

## Gotchas learned

- **The double-push bug.** If a stage's `animate()` calls a `settle()`
  helper as a "get me to the right state first" shortcut, it will
  double-push onto shared state arrays like `state.parcels`. `goStage`
  already runs all preceding settles for you — animate() should
  **assume** the world is set up, not re-run any settle helpers.

- **rAF loops need an unconditional queue.** The very first Aurora hold
  frame ran `if (!state.heatApplied) return` before `state.heatApplied`
  had a chance to flip true, killing the rAF at t≈2200ms and freezing
  the sweep. rAF loops should only exit on explicit cancel — vespera
  cancels via `_cancelHeatRaf()`, so the loop itself just queues the
  next frame every tick.

- **CSS grid + inherited line-height inflates cells.** Chrome inflates
  grid rows when the parent has non-zero `line-height`. The board uses
  absolute positioning per cell with explicit `left`/`top` to sidestep
  this entirely.

- **Fog opacity vs. background.** Setting `opacity: 0.24` on a span
  with a background also makes the background 24% transparent, which
  lets terrain bleed through. The fix: don't paint a background on the
  fog span at all, and use `.mn-cell.is-fogged` to `visibility: hidden`
  the terrain/trail/entity so the cell's own void background shows
  through instead.

- **Media query traps.** An `@media (max-width: 800px) { … }` block
  whose opening line gets accidentally deleted turns the mobile-collapse
  rules into unconditional rules, which broke the desktop layout badly.
  Sanity-check the media wrapper after every CSS edit that touches
  layout.

- **Position:fixed for FX ghosts.** Any FX node that needs to survive a
  DOM repaint (probe canvas, orblift ghost, step ghost, particles)
  attaches to `document.body` with `position: fixed` and viewport-
  relative geometry. Cell rects come from `getBoundingClientRect()` at
  animation-start time. This matches the engine's own pattern in
  `app.js:7554 document.body.appendChild(ghost)`.

- **Track every FX node in `state.fxNodes`.** So `tab3_stop` and
  `goStage` can rip them out on transition. A ghost left dangling after
  a stage jump is a broken UI 100% of the time.

## Palette + glyph reference (single source of truth)

If any of these change in the engine, update `manual.css` `:root` +
the corresponding JS constants in one pass.

| Symbol | Meaning | Source |
|--------|---------|--------|
| `██` | full block | trace/pure/empty base |
| `▓▓` | dark shade | mass tier |
| `▒▒` | medium shade | vein tier |
| `░░` | light shade | trace tier / fog / trail-tier-1 |
| `X` | harvester | `session.py:125 ENTITY_GLYPHS["harvester"]` |
| `·` | probe (U+00B7) | `session.py:127 ENTITY_GLYPHS["probe"]` |
| `▲` | orblift | `session.py:128 ENTITY_GLYPHS["orblift"]` |

## Section coverage roadmap

The manual currently ships tab 3 (One Nox loop) covering §3.2 · §3.9 ·
§3.10 · §3.11 · §3.13 · §3.15 · §3.9.1 · §0.4. Every additional
concept below wants its own tab or its own loop within an existing tab.
Suggested pairings:

| Section | Concept | Recommended loop |
|---------|---------|------------------|
| §2.2 / §2.4 | Purity ramps | already covered (Tab 2) |
| §3.3–3.5 | Tile identities | already covered (Tab 1) |
| §3.4 · §4.7 | Green catapult / solar jettison | new loop: hoard fills → jettison stage → RED fuel forfeit |
| §3.5 · §4.10 | Blue-sign radiative smear | new loop: pocket + orbital blue-sign heatmap on fog |
| §4.3 | Refine trace → vein | new loop: hoard grid + refine cost animation |
| §4.4 | RED shipping catapult | new loop: sealed-bid draft between two seats |
| §4.9 | Weapons (EMP / chaff) | new loop per weapon, each firing spawnProbeTrail with its colour |
| §3.16 | Probe collisions | new loop: two probes contest same tile, supersede resolution |
| §3.17 | Harvester collisions | new loop: two seats' harvesters collide, damage flag, cargo forfeit |
| §3.12 | Square identity + Vault ledger | new loop: harvest → parcel → vault → shipped record chain |

Each of these should reuse the stage machine template above. Where the
concept naturally has "before / event / after", that's usually 3 stages.
Where there's a decision point (e.g., refine yes/no), that's a stage
with a branch shown by two side-by-side outcomes.

## Manual plan (post-basics roadmap)

Captured 2026-08 — the intended structure the manual is building toward.
Tabs are grouped into two named books: **Basics** and **Advanced Concepts**.
Each new tab follows the same stage-machine + engine-parity discipline
documented above.

### Basics — the reader must finish this before anything else

Ordered tab list. Anything above the horizontal rule ships; anything
below is roadmap.

| # | Tab | Status | Notes |
|---|-----|--------|-------|
| 1 | TILES         | shipped | red / blue / green identities |
| 2 | PURITIES      | shipped | tier ramps + harvester lane |
| 3 | GAME LOOP     | shipped | one-Nox fog→probe→harvest→lift→aurora |
| 4 | ORDERS        | shipped | 21-hour policy + PLAN → PRAXIS in 10 stages |
| — | *(illegal moves + opponent moves)*    | **TODO** | new Basics tab; see below |
| — | *(MAPS moved here from current tab 5)* | **TODO** | see relocation note below |

**Basics closes on the maps tab.** After the reader groks orders,
illegal move handling, and opponent moves, MAPS caps the book with
"here's the world you're playing in".

**Illegal moves + opponent moves tab (post-ORDERS, pre-MAPS).**
Two things to teach in one tab:

1. **Illegal move processing** — what the engine does when you queue a
   move it can't run:
   - drop onto a fogged square (no probe intel there)
   - step onto a cell that isn't Manhattan-1 from the harvester's
     current position
   - step off the map edge
   - pickup a harvester that isn't yours or isn't on the surface
   - probe target that's out of bounds
   - anything else covered by the engine's move-legality rules
   Show: policy with an illegal move highlighted, the runner's
   error-handling behavior (skipped? substituted? logged?), and what
   the roster looks like after. This is where players learn that a
   sloppy policy WASTES an hour rather than doing something clever.

2. **Opponent moves** — up to now the manual shows one player's
   policy. Introduce that PRAXIS actually runs *every* seat's queue
   interleaved. Show: two houses' rosters side-by-side, both
   policies running in the same 21-hour sequence, resolving contests
   (probe collisions, harvester collisions) as they occur. This is
   the on-ramp into the Advanced Concepts book.

### MAPS relocation — future move

**TODO: move MAPS out of position 5.** The 3-seed generator tab
currently lives at tab index 5 but conceptually it belongs at the
END of Basics — a reader should understand tiles, purities, the
loop, orders, illegal moves, and opponent moves *before* being
shown "here's the world the game generates for you". Retarget MAPS
to the last Basics tab whenever the illegal / opponent tab lands.

The tab id (`data-tab="maps"`) and `tab5_start` / `tab5_stop`
symbols should stay — only the tab-strip ordering and the tab
number in the label need to change.

### MAPS — revisit generator output (sparse maps)

**TODO: revisit the map generation port — the rendered maps look
too sparse.** After landing the noise-scale, blue-density, and
depth-sentinel fixes, the maps still don't feel as dense / textured
as the game's own maps at 80×50. Likely causes to investigate when
we come back:

- **Min-clamp regime.** At 40×28 the engine's own scale formulas
  clamp: red `max(8, min(w,h)/4)` = 8 (clamped), green `max(4, w/12)`
  = 4 (clamped), blue `max(4, w/16)` = 4 (clamped). These clamps
  are designed for 80×50+ maps and produce large-lattice, blobby
  noise at 40×28. This is faithful to the engine — but the visual
  result is sparser than intended for teaching. Options:
  - Render maps at 80×50 (game-canonical) at very small cell size
    (needs layout width review)
  - Add a caption acknowledging that these are small-session maps
    and the actual game feels denser
  - Do NOT retune the scale formulas — that would drift from
    engine parity, which the rulebook must never do
- **Ridge / mask flatness.** Even with the sentinel fix, the ridge
  distribution at 40×28 may not have enough Manhattan-3+ interior
  cells to produce many pure cores. Worth counting the per-tier
  cell distribution across a few seeds to confirm the shapes match
  the engine's own generate_grid output at the same size.
- **Blue coverage.** blue_density=0.03 gives ~34 raw pocket-seed
  cells on a 40×28 map, then cellular smoothing (≥ 4 of 9
  neighbours survives) knocks that down to a handful of pockets
  with only a few cells each. That matches the engine — but visually
  it looks like almost-no-blue. Same "small session" caveat.
- **Ground truth check.** Run the Python generator at 40×28 with
  the same seeds (7, 42, 1729) and eyeball the output. If our JS
  port produces materially different tile counts, the port has a
  drift bug. If it matches, the sparseness is intrinsic to the
  small-map regime and the fix is presentation-level (bigger
  render, or explain the size caveat), not generator-level.

Do not retune generator constants to make the demo look better —
that violates rulebook faithfulness. The generator's parameters are
in `P` inside `generateMap()` with inline references to
`generator.py` field names; those must stay locked to engine
defaults.

### Advanced Concepts — next book

The advanced book unpacks the mechanics that Basics glosses over.
First planned section:

**Probes, landings, pickups, harvester moves, crashes, vision.**
Everything about entity-on-surface behaviour, in one dedicated
section. Rough beat list:

- **Probe lifecycle** — ballistic launch, disk radius, 3-Nox life,
  ring decrementing at each Aurora, forfeit conditions
- **Harvester landings** — orblift arc, live-only drop targeting,
  drop-through-fog rules, seed-of-first-cell harvest on landing
- **Pickups** — reverse orblift, hold cap 6, banking parcels to
  vault, "picked up = banked, stranded = destroyed at Aurora"
- **Harvester moves** — Manhattan-1 cardinal, trail glyphs on
  visited cells, harvest-on-enter semantics, GREEN scarring
- **Crashes** — probe crush (harvester walks over own probe),
  probe supersede (two probes contest same tile), harvester
  collision (two seats' harvesters occupy same cell, damage flag,
  cargo forfeit)
- **Vision** — probe disk, harvester onboard sensor radius,
  blue-sign radiative smear (blue tiles glow through fog),
  what your rivals see of your moves

Each of these gets its own stage-machine tab (or a nested set of
stages within one tab) using the primitives already documented:
`spawnProbeTrail`, `runOrbitalArcAnimation`, `animateStep`,
`spawnHarvestFx`, `playAuroraSweep`. New primitives only where the
concept demands it (e.g. probe-crush needs a "harvester walks over
probe → probe destroyed" animation that doesn't exist yet).

### Book after that

Not yet defined. Likely covers: refining, catapult draft (§4.4),
weapons (§4.9), blue-sign orbital, jettison, and end-of-season
scoring. The `Section coverage roadmap` table above pre-scopes most
of these; they get re-slotted into books once Basics closes.

---

## Planned: BASICS · Tab 8 — SEASON · THE FULL LOOP

Final Basics tab. Zooms out from "one Nox" to "one Season": 7 nights
and 7 days, ending on Day 7. Explains that Red is harvested on the
planet → orbited → shipped to Earth, and the highest shipped score
at Day 7 wins.

### Anchor visualization: SEASON STRIP

Fixed 14-block strip at the top of the stage, alternating night/day:

  N1 D1 N2 D2 N3 D3 N4 D4 N5 D5 N6 D6 N7 D7

Each block ~48px wide. Nights are dark blue, days are amber. The
strip highlights the current phase as stages progress. Below the
strip: a compact score readout (Planet · Orbit · Earth), which
increments across stages 4–5.

### Stages

- **Stage 0 · SEASON overview** — Strip appears block-by-block.
  Caption: "A season is 7 nights + 7 days. Ends on Day 7."
- **Stage 1 · NOX · 21 HOURS** — Highlight N1. Show a 21-slot
  hour bar filling left-to-right in ~3s. Caption: "Each Nox is
  21 hours of PRAXIS. Every hour, every seat fires simultaneously."
- **Stage 2 · AURORA → DAY 1 · ORBITAL** — Engine Aurora sweep
  (playAuroraSweepLocal) into 3 orbital slot pips filling one by
  one. Caption: "Aurora ends the night. Day is orbital — 3 orders
  for repair, shopping, refining, shipping."
- **Stage 3 · VESPERA → NOX 2** — Engine Vespera cool-down.
  Highlight advances to N2. Caption: "Vespera closes the day. Next
  Nox opens with fresh 21 hours to plan."
- **Stage 4 · THE RED PIPELINE** — Diagram: PLANET → ORBIT →
  EARTH. Mini-loop shows one parcel travel each leg. Planet counter
  down, Orbit up, then Orbit down, Earth up. Caption: "Red flows in
  one direction. Only what reaches Earth counts."
- **Stage 5 · 7 DAYS PASS** — Rapid montage: strip lights up
  N3→D3→N4→D4→...→D7. Score board increments Planet/Orbit/Earth
  chips across seats.
- **Stage 6 · SEASON END** — Strip fully lit. Final scoreboard,
  highest-Earth wins. Caption: "Highest shipped score at Day 7
  wins."

### Reuse

- `playAuroraSweepLocal` + `playVesperaSweepLocal` from Tab 4 (or
  the new Tab 6 copies once wired) — the engine-correct sweep.
- Stage-machine pattern (settle / animate / dur) from every
  existing tab.
- Score chip styling: pull from existing roster/policy panels.

### New primitives

- **Season Strip** — 14-block horizontal bar, per-block
  highlight class, block-by-block reveal.
- **21-hour bar** — 21-slot fill animation.
- **Orbital slot row** — 3 pips filling sequentially with verbs
  (repair · refine · ship).
- **Red pipeline diagram** — three columns (planet · orbit ·
  earth), arrow motion for a chip.
- **Scoreboard** — per-seat Planet/Orbit/Earth counters, tick
  animation.

### Open decisions

1. 4-seat vs 2-seat scenario. Default: **2-seat** (mechanics
   overview, not a rivalry demo).
2. Red pipeline static or animated. Default: **animated
   mini-loop** to tie back to OPPONENTS.

Both defaults chosen without a hard user commit — revisit if it
reads wrong.

