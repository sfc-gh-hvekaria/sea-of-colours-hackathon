# Sea of Colours — Agent Eval Scenarios

This is the canonical list of every scenario in the agent eval harness.
Keep it updated as scenarios are added — it's the index that lets you
(or anyone else iterating on the prompt / spec) find a target test for
a specific failure mode without reading `scenarios.py` cover-to-cover.

The harness itself lives in `sea_of_colours.evals`. See the package
docstring for architecture; see `scripts/run_evals.py` for CLI usage;
see `tests/test_eval_scenarios.py` for the pytest sentinels.

## Quick reference

| # | Name | Tags | Purpose |
|---|------|------|---------|
| 1 | `probes_only` | vision, probes | Day 2, full fog — don't blind-drop the harvester. |
| 2 | `collision_avoidance` | collision, tactics | Two seams visible, one contested by an enemy harvester — pick the safe one. |
| 3 | `tier_choice` | strategy, tier | Trace/vein/mass clusters at different distances — don't settle for trace. |
| 4 | `blind_dawn` | vision, probes, opening | Day 1 — spread ≥2 probes across distinct quadrants; no blind drop. |
| 5 | `enemy_telegraph` | opponent, strategy | Enemy probe at cluster A — go to uncontested cluster B (same value). |
| 6 | `enemy_trail_in_seam` | opponent, synthesis | Enemy already harvested mid-seam → synth-green; harvest the still-RED edges. |
| 7 | `vault_pressure` | vault, tactics | Cargo 5/6, hoard 47/50, surrounded by green — `pickup`, don't step. |
| 8 | `damaged_harvester` | damage, legality | Damaged harvester on surface — only legal play is `pickup`. |
| 9 | `final_night` | endgame | `day == season_day_cap` — probes can't pay off, harvest only. |
| 10 | `probe_collision_risk` | probes, collision | Enemy probe at fog centroid — don't probe the same cell (§3.16 mutual destruction). |
| 11 | `green_detour` | synthesis, pathing | Two REDs split by one synth-green — detour one row, don't walk through. |
| 12 | `green_corridor` | synthesis, pathing, tradeoff | 3 REDs alternating with 2 synth-greens — commit to the 2-clean branch. |
| 13 | `friendly_probe_in_path` | probes, pathing, self-collision | Own probe sits on empty cell between two REDs — detour, don't crush. |
| 14 | `multi_hop_seam` | spatial, pathing, phase1 | Six-cell L-shaped seam — agent must follow the curve, not dive at the deepest tile. |
| 15 | `enemy_intercept` | opponent, spatial, pathing, phase1 | Enemy trail east toward a RED seam — approach from N/S, not through their projected step. |
| 16 | `two_seams_choose_one` | spatial, budget, phase1 | Two equal-value seams; one is reachable in the 5-step budget, the other isn't. |

## Detail per scenario

### 1. `probes_only`
- **Fixture:** Day 2, harvester in orbit, full fog (no memory tiles).
- **Right answer:** Submit 1-3 probes spread across ≥2 quadrants; leave the harvester in orbit (no drop, no step).
- **Assertions:** `ProbeCount(1, 3)`, `ProbesInDistinctQuadrants(2)`, `NoHarvesterDeployment`.
- **Heuristic baseline:** FAIL (drops harvester at centre for a vision tick — classic heuristic gotcha).

### 2. `collision_avoidance`
- **Fixture:** Pure seam at (12,8)..(13,9) with enemy harvester sitting on (12,8); vein seam at (30,15) clear; both seams in live LOS via planted probes.
- **Right answer:** Harvest the safe seam OR enter the pure seam from a non-conflicting tile.
- **Assertions:** `MustAvoid([(12,8)])`, `HarvesterChainHits` over the safe seam.
- **Heuristic baseline:** FAIL (greedily walks the pure seam through (12,8) — drops into a mutual-destruction collision).

### 3. `tier_choice`
- **Fixture:** Trace cluster (~30 purity), vein cluster (~120), mass cluster (~200) all visible.
- **Right answer:** Reach at least the vein cluster — projected value ≥250.
- **Assertions:** `MinExpectedValue(250)`, `EndsWithPickup`.
- **Heuristic baseline:** PASS.

### 4. `blind_dawn`
- **Fixture:** Day 1, full fog, harvester in orbit.
- **Right answer:** ≥2 probes across distinct quadrants; no blind drop.
- **Assertions:** `ProbeCount(2, 3)`, `ProbesInDistinctQuadrants(2)`, `NoHarvesterDeployment`.
- **Heuristic baseline:** FAIL (1 probe + centre blind drop).

### 5. `enemy_telegraph`
- **Fixture:** Two equal-value clusters; enemy launched a probe yesterday at one of them.
- **Right answer:** Take the uncontested cluster.
- **Assertions:** `HarvesterChainHits` over the uncontested region.
- **Heuristic baseline:** FAIL (chooses target purely by purity — doesn't read enemy intent).

### 6. `enemy_trail_in_seam`
- **Fixture:** Originally-RED seam where the middle 3 cells were harvested by `p2` yesterday → now synth-green; the edges (18,12) and (22,12) are still RED.
- **Right answer:** Harvest the still-RED edges; don't walk over the synth-green middle.
- **Assertions:** `NoSyntheticGreenSteps`, `HarvesterChainHits` on the still-RED edges.
- **Heuristic baseline:** PASS.

### 7. `vault_pressure`
- **Fixture:** Harvester on surface at (20,14) with 5/6 cargo, all four neighbours are synth-green, hoard 47/50.
- **Right answer:** `pickup` — don't step further (every step would bank a worthless GREEN and displace banked value).
- **Assertions:** `NoSyntheticGreenSteps`, `EndsWithPickup`.
- **Heuristic baseline:** PASS.

### 8. `damaged_harvester`
- **Fixture:** Surface harvester at (20,14), `damaged=True`, surrounded by tempting RED.
- **Right answer:** `pickup` immediately — damaged units can't step (engine would reject).
- **Assertions:** `EndsWithPickup`, `MustAvoid` over all four orthogonal neighbours.
- **Heuristic baseline:** FAIL (heuristic isn't damage-aware — submits step moves the engine will reject).

### 9. `final_night`
- **Fixture:** `day=7, season_day_cap=7`. Visible 4-cell seam with high purity.
- **Right answer:** No probes (no future to harvest in); use all actions for harvest.
- **Assertions:** `ProbeCount(0, 0)`, `HarvesterChainHits` over the seam.
- **Heuristic baseline:** FAIL (still places probes — not endgame-aware).

### 10. `probe_collision_risk`
- **Fixture:** Enemy probe at (15,10); echoes around it visible to player.
- **Right answer:** Don't drop your own probe on the same cell — §3.16 destroys both.
- **Assertions:** `MustAvoid([(15,10)], check_probes=True)`.
- **Heuristic baseline:** PASS (heuristic happens not to target this cell — coincidence, not reasoning).

### 11. `green_detour`
- **Fixture:** REDs at (15,10) and (17,10); synth-green at (16,10); detour corridors clear on rows 9 and 11.
- **Right answer:** Detour ONE row to grab both REDs without the synth-green waste.
- **Assertions:** `MustTouch([(15,10), (17,10)])`, `NoSyntheticGreenSteps`, `MinExpectedValue(400)`, `EndsWithPickup`.
- **Heuristic baseline:** FAIL on `NoSyntheticGreenSteps` (3/4) — walks straight through the green.

### 12. `green_corridor`
- **Fixture:** REDs at (10/12/14, 10); synth-greens at (11/13, 10); 5-step harvester budget can't cover all 3 REDs cleanly.
- **Right answer:** Commit to the no-waste branch — bank 2 REDs cleanly via row 9 or 11.
- **Assertions:** `NoSyntheticGreenSteps`, `HarvesterChainHits(min_hits=2)` over the 3 REDs, `MinExpectedValue(400)`, `EndsWithPickup`.
- **Heuristic baseline:** FAIL (drops directly on a synth-green cell — heuristic doesn't read green-tile metadata before choosing the drop site).

### 13. `friendly_probe_in_path`
- **Fixture:** REDs at (11,10) and (13,10); empty cell (12,10) carries the agent's own probe; detour corridors clear.
- **Right answer:** Detour via row 9 or 11 — preserves the probe (one extra step, probe stays as intel asset).
- **Assertions:** `MustAvoid([(12,10)], check_harvester=True)`, `MustTouch([(11,10), (13,10)])`, `MinExpectedValue(400)`, `EndsWithPickup`.
- **Heuristic baseline:** FAIL (drops directly on the probe cell — heuristic doesn't read friendly-probe positions before choosing the drop site).

### 14. `multi_hop_seam`
- **Fixture:** Six-cell L-shaped seam at (12,9)→(12,10)→(12,11)→(13,11)→(14,11)→(14,12) with rising purity (130 → 250).
- **Right answer:** Drop on (12,9), step south twice, east twice, south once — banks all six cells in one outing.
- **Assertions:** `HarvesterChainHits(seam, min_hits=4)`, `MinExpectedValue(600)`, `EndsWithPickup`.
- **Heuristic baseline:** PASS (closest-first re-evaluation walks the curve naturally).
- **Phase 1:** Headline differentiator — grid view exposes the L-shape directly via `grid[y][x]` adjacency; list view forces the agent to mentally project the layout.

### 15. `enemy_intercept`
- **Fixture:** Enemy harvester at (10,10) with fresh trail from (8,10)→(9,10)→(10,10) (heading east). RED seam at (12-14, 10).
- **Right answer:** Approach from row 9 or row 11 — don't drop or step through (11,10) where the enemy is projected to walk tonight.
- **Assertions:** `MustAvoid([(10,10), (11,10)])`, `HarvesterChainHits(seam, min_hits=2)`, `EndsWithPickup`.
- **Heuristic baseline:** PASS (drops at (13,11) and approaches from south, which avoids the trail).
- **Phase 1:** Tests whether the agent reads enemy trail direction from the world view and projects the next step.

### 16. `two_seams_choose_one`
- **Fixture:** Surface harvester at (12,13). West seam at cols 2-4 (distance 8 to first cell — out of 5-step budget). East seam at cols 16-18 (distance 4 to first cell — reachable). Both seams value 200 per cell, total 600.
- **Right answer:** Commit to the east (reachable) seam; never step toward the west seam.
- **Assertions:** `MustAvoid(west_seam)`, `HarvesterChainHits(east_seam, min_hits=1)`, `EndsWithPickup`.
- **Heuristic baseline:** PASS (sort by distance ASC handles this trivially).
- **Phase 1:** Tests whether the agent reasons about step budget when choosing between equal-value targets.

## Orchestrator configs (Phase 1 A/B)

The harness emits the world view in one of two shapes. Pick a shape
by setting `SOC_AGENT_WORLD_VIEW` (defaults to `list`) — the runtime
auto-detects the shape and emits the matching "READING THE WORLD"
prompt section. Each shape is paired with its own deployed Cortex
agent so the agent's reading-instructions match the payload exactly.

| Config | `world_view` | Cortex agent | Notes |
|--------|--------------|--------------|-------|
| `list_v1` | `list` | `SOC_RED_REAPER_LIST` | Baseline. `world.live[]` / `world.echo[]` flat arrays with explicit `(x, y)` per row. |
| `grid_v1` | `grid` | `SOC_RED_REAPER_GRID` | `world.grid[y][x]` 2D nested JSON. `null` = fog, dict = cell. Adjacency is `grid[y][x±1]` / `grid[y±1][x]`. |

The configs are registered in `sea_of_colours/evals/configs.py` and
exposed through the public eval API (`from sea_of_colours.evals
import CONFIGS, EvalConfig`). The wire contract for the grid shape
is locked by `tests/test_world_grid.py`.

### Comparing configs

```bash
# Single config (just sets SOC_AGENT_WORLD_VIEW + SOC_CORTEX_AGENT
# before each scenario run)
SOC_BACKEND=snowflake python -m scripts.run_evals \
  --backend cortex --config grid_v1 --samples 3

# Side-by-side comparison report — runs every scenario against both
# configs and emits a markdown table per scenario plus totals.
SOC_BACKEND=snowflake python -m scripts.run_evals \
  --backend cortex --compare list_v1,grid_v1 \
  --samples 3 --out reports/phase1.md
```

The comparison report is the artefact that names a winner for
Phase 1. Once we have it, the loser becomes a legacy reference and
later configs (`grid_v2`, `list_v2`) layer on agent-prompt
iterations while keeping the harness shape pinned.

## Heuristic baseline summary

Re-run anytime with `python -m scripts.run_evals --format compact`. As
of v0.7.5:

```
PASS tier_choice
PASS enemy_trail_in_seam
PASS vault_pressure
PASS probe_collision_risk
PASS multi_hop_seam            — phase1, follows curving seam
PASS enemy_intercept           — phase1, side-approach to seam
PASS two_seams_choose_one      — phase1, picks reachable seam
FAIL probes_only               — blind-drops for vision
FAIL collision_avoidance       — walks into enemy harvester
FAIL blind_dawn                — only 1 probe + blind drop
FAIL enemy_telegraph           — chases enemy-contested cluster
FAIL damaged_harvester         — submits step on damaged unit
FAIL final_night               — places probes on last night
FAIL green_detour              — walks through synth-green
FAIL green_corridor            — drops directly on synth-green
FAIL friendly_probe_in_path    — drops directly on own probe
```

The PASS scenarios act as regression sentinels in
`tests/test_eval_scenarios.py::test_heuristic_passes_known_scenarios`.

## Adding a new scenario

1. Factory in `sea_of_colours/evals/scenarios.py`:

```python
def my_scenario() -> Scenario:
    def build():
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red([...])
            .grant_live_vision([...])
            .build()
        )
    return Scenario(
        name="my_scenario",
        summary="Concrete description of what this fixture tests.",
        build=build,
        assertions=[
            MustTouch(...),
            NoSyntheticGreenSteps(),
        ],
        tags=("some-tag",),
    )
```

2. Append to `SCENARIOS` at the bottom of `scenarios.py`.
3. Add a row to the quick-reference table above and a detail section.
4. If the heuristic should reliably pass it, add the name to
   `HEURISTIC_GREEN_SCENARIOS` in `tests/test_eval_scenarios.py`.
5. Re-run `python -m scripts.run_evals --format compact` and update
   the baseline summary above.

The pytest harness will auto-discover the new scenario via the
parametrised `test_scenario_runs_cleanly` and confirm the fixture
builds + runs without crashing — that catches schema drift early.

## CLI cheat sheet

```bash
# List scenarios
python -m scripts.run_evals --list

# Full markdown report
python -m scripts.run_evals

# One scenario
python -m scripts.run_evals --scenario green_detour

# Multi-scenario, JSON output, saved to disk
python -m scripts.run_evals \
  --scenario green_detour --scenario green_corridor \
  --format json --out reports/green-family.json

# Compact PASS/FAIL list (good for CI logs)
python -m scripts.run_evals --format compact

# Live Cortex run (needs SOC_BACKEND=snowflake in env)
SOC_BACKEND=snowflake python -m scripts.run_evals \
  --backend cortex --samples 3 --out reports/cortex-latest.md

# List orchestrator configs
python -m scripts.run_evals --list-configs

# Pin one orchestrator config
SOC_BACKEND=snowflake python -m scripts.run_evals \
  --backend cortex --config grid_v1 --samples 3

# Side-by-side A/B comparison (markdown report)
SOC_BACKEND=snowflake python -m scripts.run_evals \
  --backend cortex --compare list_v1,grid_v1 \
  --samples 3 --out reports/phase1.md
```
