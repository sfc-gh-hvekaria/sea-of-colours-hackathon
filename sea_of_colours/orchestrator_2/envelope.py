"""Universal STATE envelope builder for orchestrator_2.

The orchestrator's contract with every agent (bare Cortex or harnessed):
"here is the same envelope, regardless of who you are."

This module produces ONE shape. No adapter hooks, no agent-specific
keys. V12's option menu lives INSIDE its harness — not here.

Identical to the legacy slim builder in
:mod:`sea_of_colours.agent.runtime._build_cortex_prompt_slim` except:

* No ``agent_name`` parameter (the envelope is agent-agnostic).
* No adapter dispatch (harnesses do their own preprocessing).
* No per-agent envelope branch.
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict, List, Mapping, Tuple


# Match the legacy orchestrator's cap so prompts stay drop-in compatible.
SLIM_PROMPT_CAP_CHARS = 28_000


def build_universal_envelope(
    session_id: str,
    view: Mapping[str, Any],
) -> str:
    """Build the per-turn envelope every orchestrator_2 agent receives.

    Pure function: returns the string body. The caller decides whether
    to POST it to a Cortex agent, pass it to a harness, or hand it to
    a heuristic.
    """
    agent_view = view.get("agent_view") or {}
    meta = agent_view.get("meta") or {}
    hud = agent_view.get("hud") or {}
    day = meta.get("day") or hud.get("day", view.get("day"))
    player = meta.get("player") or hud.get("player", "p1")
    season_cap = hud.get("season_day_cap") or 7
    policy_max = meta.get("policy_actions_max", 21)
    policy_left = meta.get("policy_actions_left", policy_max)

    structured_payload: Dict[str, Any] = {
        "meta": agent_view.get("meta") or {},
        "hud": agent_view.get("hud") or {},
        "last_night": agent_view.get("last_night") or {},
        "competitor_intel": agent_view.get("competitor_intel") or {},
        "world": dict(agent_view.get("world") or {}),
        "navigation": agent_view.get("navigation") or {},
        "my_assets": agent_view.get("my_assets") or [],
    }

    def _serialise(payload: Dict[str, Any]) -> str:
        return _json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    envelope = (
        "SEA OF COLOURS — turn brief. The rules, move grammar, and how to "
        "read world.grid are in your system instructions; do not expect "
        "them here.\n"
        f"session={session_id} day={day}/{season_cap} seat={player} "
        f"actions_left={policy_left}/{policy_max}\n"
        "Read STATE below, then CALL soc_submit_policy on your FIRST "
        'tool call with p_policy as a JSON STRING: {"moves":[...]}.\n'
    )

    state_block = "\nSTATE (JSON):\n```json\n" + _serialise(structured_payload) + "\n```\n"
    body = envelope + state_block
    if len(body) <= SLIM_PROMPT_CAP_CHARS:
        return body

    # Over cap — window the grid to the active region (same algorithm
    # as the v1 slim builder).
    world = dict(structured_payload["world"] or {})
    grid = world.get("grid")
    if isinstance(grid, list) and grid:
        anchors: List[Tuple[int, int]] = []
        for a in structured_payload["my_assets"] or []:
            at = (a or {}).get("at")
            if isinstance(at, (list, tuple)) and len(at) == 2:
                try:
                    anchors.append((int(at[0]), int(at[1])))
                except (TypeError, ValueError):
                    pass
        if not anchors:
            anchors = [(int(world.get("width", 0)) // 2, int(world.get("height", 0)) // 2)]
        radius = 8
        min_x = max(0, min(a[0] for a in anchors) - radius)
        max_x = max(a[0] for a in anchors) + radius
        min_y = max(0, min(a[1] for a in anchors) - radius)
        max_y = max(a[1] for a in anchors) + radius
        new_grid: List[List[Any]] = []
        for y, row in enumerate(grid):
            new_row: List[Any] = []
            for x, cell in enumerate(row):
                if min_x <= x <= max_x and min_y <= y <= max_y:
                    new_row.append(cell)
                else:
                    new_row.append(None)
            new_grid.append(new_row)
        world["grid"] = new_grid
        structured_payload["world"] = world
        state_block = "\nSTATE (JSON):\n```json\n" + _serialise(structured_payload) + "\n```\n"
        body = envelope + state_block

    return body
