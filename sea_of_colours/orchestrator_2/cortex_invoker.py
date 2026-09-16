"""Cortex Agent invoker for orchestrator_2.

Thin subclass of :class:`sea_of_colours.agent.cortex_invoker.CortexAgentInvoker`
that adds orchestrator_2-specific per-agent caps. The SSE parser, tool-call
detection, and hallucination tracking come from the parent class
unchanged — that way fixes flow to both orchestrators.

What's overridden:

* :data:`WALLCLOCK_CAP_OVERRIDES` — per-agent hard per-turn ceiling. A
  turn that hasn't submitted by then is cut off and the orchestrator
  falls back to the doctrine policy.
* :data:`RESPONSE_CAP_OVERRIDES` — per-agent response ceiling, set
  generously so a long rationale lands intact in audit.

Add an entry for your fork if it thinks for longer than the default.
"""

from __future__ import annotations

from typing import Dict

from sea_of_colours.agent.cortex_invoker import (
    CortexAgentInvoker as _LegacyInvoker,
)


class CortexAgentInvoker(_LegacyInvoker):
    """orchestrator_2 invoker with per-agent caps.

    v1.12 — the override tables used to carry ~35 entries covering the
    whole retired pilot_*/tabula_v2..v11 lineage. Every one of those
    agents is gone, so the entries were noise that made it hard to see
    which caps still bind.

    Only the V7 finisher remains, and it is on borrowed time: see the
    note in :mod:`...harnesses.tabula_v12.harness` about the finisher
    path calling a Cortex *agent object* that no longer exists. Kept so
    the cap doesn't silently change if that path is repaired.

    Add an entry here if your fork needs longer than the default.
    """

    WALLCLOCK_CAP_OVERRIDES: Dict[str, int] = {
        **_LegacyInvoker.WALLCLOCK_CAP_OVERRIDES,
        # V12's parse-failure finisher. A short leash on purpose: this
        # fires only when the mover produced unparseable JSON, and a
        # human is waiting on the turn.
        "SOC_RED_REAPER_TABULA_V7_FINISHER": 20,
    }

    RESPONSE_CAP_OVERRIDES: Dict[str, int] = {
        **_LegacyInvoker.RESPONSE_CAP_OVERRIDES,
        "SOC_RED_REAPER_TABULA_V7_FINISHER": 4_000,
    }
