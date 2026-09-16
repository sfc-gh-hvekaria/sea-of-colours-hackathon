"""The redsign battles — a scenario suite for agents that have to fight.

Start at ``README.md`` in this directory. The short version:

    from sea_of_colours.evals.battles import boards, ladder, stage

    battle = stage.stage(
        boards.get("two_pures_poker"),
        ladder.get_rung("armed"),
        ladder.get_loadout("both"),
    )

Nine boards, five difficulty rungs, four weapon loadouts. Everything
runs offline on ``SOC_BACKEND=memory``; only the agent itself needs
credentials, and the heuristic does not even need those.
"""

from __future__ import annotations

from sea_of_colours.evals.battles import boards, ladder, stage  # noqa: F401

__all__ = ["boards", "ladder", "stage"]
