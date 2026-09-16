"""sea_of_colours.orchestrator_2 — the experimental binding-driven orchestrator.

THIS IS A PARALLEL ORCHESTRATOR. The production orchestrator continues to
live in :mod:`sea_of_colours.agent.runtime` and serves PILOT (v1),
GRID_FAST, SOC_RED_REAPER, LIST_V2, and the heuristic. orchestrator_2 is
an isolated, opt-in re-implementation that supports a fully-decoupled
"harness per agent" pattern.

What's different:

* **Binding-driven dispatch.** Each ``(session_id, player)`` pair maps
  to an :class:`~sea_of_colours.orchestrator_2.binding_registry.AgentBinding`
  that names a ``kind`` (``cortex_agent``, ``harness_in_process``,
  ``harness_proc``, ``harness_spcs``, ``heuristic``) and a ``locator``.
  The orchestrator routes the turn to the right backend; it does NOT
  know what's inside.
* **Universal STATE.** The orchestrator builds ONE consistent envelope
  for every agent: meta / hud / last_night / competitor_intel / world /
  navigation / my_assets. No agent-specific preprocessing in the
  orchestrator path.
* **Harnesses are self-contained.** V12's option-menu compiler,
  threat brief, season memory, and prompt assembly all live inside
  :mod:`sea_of_colours.orchestrator_2.harnesses.tabula_v12`. The
  orchestrator imports nothing from there.

Read ``README.md`` to plug an agent in, ``ARCHITECTURE.md`` for why it
is shaped this way, and ``harnesses/tabula_v12/README.md`` for the
agent you fork.
"""
