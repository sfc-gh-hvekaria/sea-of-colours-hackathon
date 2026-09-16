"""Named orchestrator configurations for A/B testing the world view.

A *config* binds two knobs that together describe one "way of presenting
the world to the agent":

* ``world_view`` — which shape the harness emits inside the agent's
  ``world`` block. See :mod:`sea_of_colours.snowpark.view`:

  - ``"list"`` (default): ``world.live[]`` / ``world.echo[]`` flat
    arrays of visible cells with explicit (x, y) per row.
  - ``"grid"``: ``world.grid[y][x]`` — a 2D nested JSON array,
    ``null`` for fog, dict for live/echo cells. The spatial structure
    of the JSON IS the map.

* ``cortex_agent`` — the deployed Cortex agent whose "READING THE
  WORLD" instructions match the world_view above. Listed alongside the
  view because the harness shape and the agent's reading primer must
  agree (otherwise the agent reasons about a payload that doesn't
  exist).

The runner applies a config by setting two environment variables before
invoking :func:`sea_of_colours.agent.runtime.run_agent_turn`:

  ``SOC_AGENT_WORLD_VIEW``  — read by :mod:`sea_of_colours.snowpark.view`
  ``SOC_CORTEX_AGENT``      — read by :mod:`sea_of_colours.agent.runtime`

The eval CLI (``scripts/run_evals.py``) exposes ``--config`` for a
single run, and ``--compare`` for a side-by-side markdown report.

Phase 1 ships two configs (``list_v1`` and ``grid_v1``); after the
matrix run names a winner, the loser becomes a legacy reference and
new configs (``grid_v2`` etc.) can layer on agent-prompt iterations
while keeping the harness shape pinned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class EvalConfig:
    """One (harness shape, Cortex agent name) pairing.

    The label is the short string the CLI accepts (``list_v1``,
    ``grid_v1``). ``description`` shows up in markdown reports so the
    reader doesn't have to remember what each label means.
    """

    label: str
    world_view: str
    cortex_agent: str
    description: str

    def env_overrides(self) -> Dict[str, str]:
        """Environment variables this config wants set for the agent run.

        Centralised here so the runner and the CLI agree on exactly
        which env vars participate in the A/B — no scattered string
        literals. The :func:`apply` context manager below uses this
        directly.
        """
        return {
            "SOC_AGENT_WORLD_VIEW": self.world_view,
            "SOC_CORTEX_AGENT": self.cortex_agent,
        }


CONFIGS: Dict[str, EvalConfig] = {
    "list_v1": EvalConfig(
        label="list_v1",
        world_view="list",
        cortex_agent="SOC_RED_REAPER_LIST",
        description=(
            "Baseline. Harness emits world.live[] / world.echo[] flat "
            "arrays. Agent reasons about adjacency from explicit (x, y)."
        ),
    ),
    "grid_v1": EvalConfig(
        label="grid_v1",
        world_view="grid",
        cortex_agent="SOC_RED_REAPER_GRID",
        description=(
            "Harness emits world.grid[y][x] as a 2D nested JSON array. "
            "Adjacency is `grid[y][x±1]` / `grid[y±1][x]` — spatial "
            "structure of the JSON IS the map."
        ),
    ),
    # V2 — same harness shapes as V1, but the agent runs a
    # deterministic TURN COMPILER instead of narrative route debate:
    # fixed-order neighbour evaluation (x, y-1) → (x+1, y) → (x, y+1)
    # → (x-1, y), numeric move scoring, explicit penalties (stale
    # echo, synthetic green, collision), and a strict commit-then-
    # describe contract on the tool calls. Tested for whether
    # coordinate-arithmetic discipline beats natural-language
    # direction reasoning on the same eval scenarios.
    "list_v2": EvalConfig(
        label="list_v2",
        world_view="list",
        cortex_agent="SOC_RED_REAPER_LIST_V2",
        description=(
            "TURN COMPILER over world.live[] / world.echo[]. "
            "Coordinate-driven neighbour evaluation, no direction "
            "debate. Drop-in replacement for list_v1 — same harness."
        ),
    ),
    "grid_v2": EvalConfig(
        label="grid_v2",
        world_view="grid",
        cortex_agent="SOC_RED_REAPER_GRID_V2",
        description=(
            "TURN COMPILER over world.grid[y][x]. Numeric move "
            "scoring with stale-echo / collision / synthetic-green "
            "penalties. Drop-in replacement for grid_v1."
        ),
    ),
    # FAST — same GRID world view as V2, but the agent spec pins
    # claude-3-5-haiku and a 50s / 12k-token orchestration budget so
    # turns land in well under the 55s wall-clock cap enforced by
    # ``cortex_invoker.WALLCLOCK_CAP_OVERRIDES``. The prompt is a
    # compact TURN COMPILER distilled from the RED_HARVEST heuristic
    # playbook (cluster scoring, RED-bracket probes, GREEN avoidance,
    # mandatory pickup). Use this config when the wall-clock budget
    # matters more than reasoning depth (e.g. live multiplayer).
    "grid_fast": EvalConfig(
        label="grid_fast",
        world_view="grid",
        cortex_agent="SOC_RED_REAPER_GRID_FAST",
        description=(
            "Haiku-pinned sub-60s GRID compiler. 50s / 12k-token "
            "agent budget + 55s harness cap. Heuristic-informed "
            "TURN COMPILER (cluster scoring, RED-bracket probes, "
            "GREEN avoidance). Drop-in replacement for grid_v2 "
            "when latency matters."
        ),
    ),
    # PILOT — rules-in-spec map-reading agent. The doctrine (rules, move
    # grammar, world.grid reading guide) lives in the agent spec, so the
    # harness sends a SLIM per-turn prompt (envelope + STATE JSON, full
    # grid intact) instead of ~16k chars of re-transmitted rules. Tests
    # whether haiku can process a full-size board and logic out one good
    # harvester drop when it actually receives a clean, complete map.
    "pilot": EvalConfig(
        label="pilot",
        world_view="grid",
        cortex_agent="SOC_RED_REAPER_PILOT",
        description=(
            "Rules-in-spec map-reading pilot. claude-3-5-haiku, 120s/30k "
            "budget, single submit tool. Per-turn prompt is the slim "
            "state-only brief (full grid, no doctrine). Mission: one good "
            "harvester drop + harvest chain on a full-size grid."
        ),
    ),
    # PILOT_V2 — lives in sea_of_colours/orchestrator_2/. The orchestrator_2
    # eval runner registers its own pilot_v2 config; this v1 eval module
    # stays focused on the agents that go through the original orchestrator
    # (PILOT, GRID, GRID_V2, GRID_FAST, LIST, LIST_V2).
}


def get_config(label: str) -> EvalConfig:
    """Resolve a config by short label.

    Raises :class:`KeyError` with a list of valid labels so CLI users
    get a helpful "did you mean" trace when they typo a config name.
    """
    if label in CONFIGS:
        return CONFIGS[label]
    known = ", ".join(sorted(CONFIGS.keys()))
    raise KeyError(f"unknown eval config {label!r}. Known: {known}")
