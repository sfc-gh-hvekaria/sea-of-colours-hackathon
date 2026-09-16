"""Sea of Colours agent runtimes.

This package holds the **deterministic** agents:

* **RED_HARVEST** — the Python heuristic (:class:`HeuristicAgent`). Our
  mainstay: the default behind the ``[ LET RED_HARVEST PLAY ]`` button,
  runs without any Snowflake dependency, and is fully tested.
* **RED_HARVEST_LITE** — the same playbook with weapons disabled. The
  hackathon's first opponent.

**LLM agents live elsewhere.** The shipped LLM agent is V12
(:mod:`sea_of_colours.orchestrator_2.harnesses.tabula_v12`), dispatched
by :func:`sea_of_colours.orchestrator_2.runtime.run_agent_turn`. The
Cortex *Agents-API* runtime that once lived in this package was removed
along with its ``soc_create_agent*.sql`` specs;
:mod:`sea_of_colours.agent.cortex_invoker` survives only as the shared
SSE/PAT transport that the orchestrator harnesses build on.

:func:`run_agent_turn` returns::

    {
        "ok": bool,
        "agent_id": str,            # "RED_HARVEST" / "RED_HARVEST_LITE"
        "runtime": "heuristic",
        "rationale": str,
        "moves": [<wire-format move>...],
        "tool_calls": list[dict],
        "ms_elapsed": int,
    }
"""

from sea_of_colours.agent.heuristic_agent import HeuristicAgent, plan_moves
from sea_of_colours.agent.runtime import (
    HEURISTIC_AGENT_NAME,
    HEURISTIC_LITE_AGENT_NAME,
    run_agent_turn,
)

__all__ = [
    "HEURISTIC_AGENT_NAME",
    "HEURISTIC_LITE_AGENT_NAME",
    "HeuristicAgent",
    "plan_moves",
    "run_agent_turn",
]
