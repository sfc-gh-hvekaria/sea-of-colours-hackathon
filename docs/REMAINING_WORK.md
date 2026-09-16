# Remaining work — player identity, colours, replays, probe degradation

Status snapshot as of the v0.9.18 player-identity / colour pass. Everything
here is **offline-first** (file-backed store under `./seasons`, watch server at
`SOC_BACKEND=file SOC_STORE_DIR=./seasons`).

---

## ✅ Done this pass (for context)

- **7-night canonical season** (`SEASON_DAY_CAP = 7`; API/modal/run_season defaults + tests).
- **Colour picker hardened** — full 9-colour fallback so swatches always render even if `/api/game/palette` hiccups.
- **Bot identity** — bots now get Latin display names + **random palette colours** (seeded by game seed) + **distinct, name-derived 3-letter tags** (e.g. `Aureus Mustela → AMU`). `run_season.py` now marks both seats as bots at init so this actually generates.
- **Replay carries identity** — `/api/game/{id}/replay` forwards `player_profiles`; the frontend replay loader adopts it via `setPlayerMeta()`.
- **Centralised colour** — `ownerColor()` / `ownerColour()` now defer to `__SOC_PLAYER_META__`; `setPlayerMeta()` also pushes colours into the `--seat-pN` root CSS vars (`applySeatColorVars()`) so every stylesheet rule (catapult slots, timeline, now-caption, scoreboards, vault borders) recolours at once.
- **Labels** — replaced hard-coded `P1/P2` across: pre-orbital recap cards, orbital-observation boxes, orbit-log columns, shipped-score panel, RED/GREEN catapult totals + legend + tooltips, vault seat tabs, seat-identity badge, invite/share modal. End screen name+colour fixed server-side in `get_endgame_summary` (uses `profile.display_name` + `profile.color`).
- **Offline battery + stats** — `scripts/season_stats.py` reads the file store; confirmed **0 free/auto repairs**.

---

## 🔧 Still to do

### 1. Replay board pieces render the WRONG colour during animation (correct when paused) — HIGH
**Symptom:** during replay playback the board glyphs/pieces are mis-coloured; pausing re-renders them correctly.

**Hypothesis / root cause:** two paint paths disagree.
- The *paused* render re-derives seat colour via the now-profile-aware `ownerColor()` → correct.
- The *streaming/animation* path paints the **server-baked dense-view cell colours** (entity glyph `fg`/`bg` baked into replay frames at season-run time) and/or paints before `__SOC_PLAYER_META__` is populated → wrong/default colour.

**Where to look:**
- `server/static/app.js`: the dense-view tick/animation painter (frame `cells[]` → glyph fg/bg) vs the pause/full re-render path. Confirm both go through `ownerColor()` and that `setPlayerMeta()` runs before the first animated frame paints.
- `sea_of_colours/game/session.py`: `_glyph_for_entity()` / dense-view serialization — entity glyph colours are baked here using seat colour. Make sure the baked colour uses `seat_color()` (profile-aware) AND/OR have the frontend recolour entity glyphs at paint time instead of trusting baked `fg`.
- `sea_of_colours/snowpark/engine.py`: `get_replay()` frame assembly.

**Fix direction:** prefer recolouring entity pieces at *paint* time on the client from `ownerColor(owner)` so the colour is always live, regardless of what's baked into the frame. Verify against a fresh replay.

### 2. Replays are only 2-player — MEDIUM
The canonical battery is all 2-seat heuristic-vs-heuristic. Add variety:
- 3- and 4-seat seasons (`init_session(players=[...], agents={...})`).
- Agent (cortex) vs heuristic matchups.
- Add a small batch driver (or extend `run_season.py`) for N-seat + mixed-agent runs, still file-backed/offline.

### 3. Bots mishandle disappearing probes — HIGH (ties to the degradation decision)
**Symptom:** probes expire after 3 nights; under live-only drops the bot then tries to drop on a cell with no live sensor and the action fails (`no live sensor beacon …`). The heuristic plans drops against stale coverage.

**Requirement (user):** probes destroy/expire *during the day*, so **before dropping the agent must compute the current live/echo coverage** (accounting for probes that will have expired) and only plan drops on cells that are actually visible/available — re-probing first to refresh coverage when needed.

**Where to look:**
- Heuristic planner (`sea_of_colours/agent/…`, the `red_harvest` strategy) — drop target selection must filter by *current* live/echo coverage, not last-known.
- Probe lifetime logic in `simulator.py` / `session.py` (3-night expiry, live-only drop gate, RULEBOOK §3.9.7).
- UI: surface probe expiry clearly (per-probe countdown / fading disk) so a human reads coverage at a glance.

### 4. Non-free-repair consequence barely triggers in bot-v-bot — MEDIUM (depends on #3)
With cautious bots, `strandings=0` / `damaged_at_end=0` even with collisions. Once #3 makes agents overstay/lose harvesters (or we pick the canonical probe-degradation rule), re-run the battery and confirm destroy/damage stats actually move. This is the canonical degradation decision point.

### 5. Full colour QA pass after #1 — LOW
Once the replay painter is fixed, verify colours end-to-end: live HUD, replay animation, pause, end screen, vault borders, catapult grid, timeline, now-playing caption — all using the seat's real custom/random colour.

---

## Handy commands (offline)

```bash
# watch server (file-backed, no Snowflake)
SOC_BACKEND=file SOC_STORE_DIR=./seasons python -m uvicorn server.app:app --host 127.0.0.1 --port 8099

# run a season (offline, saved as a watchable replay)
python scripts/run_season.py --seed 101 --p1 heuristic --p2 heuristic \
  --season-name "Canon7_HvH_s101" --backend file --store-dir ./seasons --quiet

# canonical battery stats
python scripts/season_stats.py seasons --prefix Canon7_
```
