"""4-way dispatch for orchestrator_2.

Given a resolved :class:`AgentBinding`, route the turn to the right
backend and return a uniform :class:`DispatchResult`. The orchestrator
itself doesn't know what's behind each ``kind`` — only the dispatcher
does.

Kind table:

* ``cortex_agent``        — POST universal envelope to ``agents/<locator>:run``.
* ``harness_in_process``  — dynamic-import ``<module>:<callable>``, call
                            with the universal envelope. The harness
                            decides everything else.
* ``harness_proc``        — (FUTURE) CALL Snowflake stored procedure.
                            Stub raises NotImplementedError today.
* ``harness_spcs``        — (FUTURE) HTTPS POST to SPCS service.
                            Stub raises NotImplementedError today.
* ``heuristic``           — in-process Python (RED_HARVEST).
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from sea_of_colours.orchestrator_2.binding_registry import AgentBinding
from sea_of_colours.orchestrator_2.envelope import build_universal_envelope


# ── Result dataclass ─────────────────────────────────────────────────
@dataclass
class DispatchResult:
    """Uniform result envelope from any binding kind."""

    ok: bool
    elapsed_ms: int
    submitted_policy: bool
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    response: str = ""
    error: Optional[str] = None
    rationale: str = ""
    wallclock_capped: bool = False
    # Extra info per kind (e.g. inner agent name for cortex_agent;
    # candidate-compiler metadata for harnesses).
    extras: Dict[str, Any] = field(default_factory=dict)


# ── Per-kind dispatchers ─────────────────────────────────────────────
def _dispatch_cortex_agent(
    *,
    binding: AgentBinding,
    session_id: str,
    view: Mapping[str, Any],
) -> DispatchResult:
    """Bare Cortex Agent: build universal envelope, invoke, return result."""
    from sea_of_colours.orchestrator_2.cortex_invoker import CortexAgentInvoker

    prompt = build_universal_envelope(session_id, view)
    inv = CortexAgentInvoker(agent_name=binding.locator)
    if not inv.is_ready():
        return DispatchResult(
            ok=False,
            elapsed_ms=0,
            submitted_policy=False,
            error=f"Cortex invoker not ready for {binding.locator!r} (missing PAT/account)",
            extras={"binding_kind": "cortex_agent", "agent_name": binding.locator},
        )
    raw = inv.invoke(prompt)
    return DispatchResult(
        ok=bool(raw.get("ok")),
        elapsed_ms=int(raw.get("elapsed_ms") or 0),
        submitted_policy=bool(raw.get("submitted_policy")),
        tool_calls=list(raw.get("tool_calls") or []),
        response=str(raw.get("response") or ""),
        error=raw.get("error"),
        wallclock_capped=bool(raw.get("wallclock_capped")),
        extras={
            "binding_kind": "cortex_agent",
            "agent_name": binding.locator,
            "hallucinated_tools": raw.get("hallucinated_tools") or [],
            "tool_errors": raw.get("tool_errors") or [],
        },
    )


def _import_callable(locator: str):
    """Import ``"module.path:callable_name"``."""
    if ":" not in locator:
        raise ValueError(f"harness locator {locator!r} must be 'module:callable'")
    mod_path, name = locator.split(":", 1)
    mod = importlib.import_module(mod_path)
    fn = getattr(mod, name, None)
    if not callable(fn):
        raise ImportError(f"{mod_path!r} has no callable {name!r}")
    return fn


def _dispatch_harness_in_process(
    *,
    binding: AgentBinding,
    store,
    session_id: str,
    player: str,
    view: Mapping[str, Any],
) -> DispatchResult:
    """In-process harness: import + call. Harness owns its own pipeline.

    The orchestrator passes the UNIVERSAL envelope (same as every other
    agent gets). The harness can read ``view`` directly if it wants
    structured access, or use ``build_universal_envelope`` itself if it
    wants the serialised STATE.
    """
    start = time.time()
    try:
        fn = _import_callable(binding.locator)
    except (ValueError, ImportError, AttributeError) as exc:
        return DispatchResult(
            ok=False,
            elapsed_ms=int((time.time() - start) * 1000),
            submitted_policy=False,
            error=f"harness import failed: {exc}",
            extras={"binding_kind": "harness_in_process", "locator": binding.locator},
        )

    try:
        raw = fn(store=store, session_id=session_id, player=player, view=view)
    except Exception as exc:  # pragma: no cover - defensive
        return DispatchResult(
            ok=False,
            elapsed_ms=int((time.time() - start) * 1000),
            submitted_policy=False,
            error=f"harness raised: {exc}",
            extras={"binding_kind": "harness_in_process", "locator": binding.locator},
        )

    raw = raw or {}
    return DispatchResult(
        ok=bool(raw.get("ok", True)),
        elapsed_ms=int(raw.get("elapsed_ms") or int((time.time() - start) * 1000)),
        submitted_policy=bool(raw.get("submitted_policy") or raw.get("submitted")),
        tool_calls=list(raw.get("tool_calls") or []),
        response=str(raw.get("response") or ""),
        error=raw.get("error"),
        wallclock_capped=bool(raw.get("wallclock_capped")),
        rationale=str(raw.get("rationale") or ""),
        extras={
            "binding_kind": "harness_in_process",
            "locator": binding.locator,
            "agent_label": binding.agent_label,
            **(raw.get("extras") or {}),
        },
    )


def _dispatch_harness_proc(**kwargs) -> DispatchResult:
    raise NotImplementedError(
        "harness_proc kind is reserved for the Snowflake-hosted harness "
        "follow-up. See sea_of_colours/orchestrator_2/snowflake/."
    )


def _dispatch_harness_spcs(**kwargs) -> DispatchResult:
    raise NotImplementedError(
        "harness_spcs kind is reserved for the SPCS-hosted harness "
        "follow-up. See sea_of_colours/orchestrator_2/snowflake/."
    )


def _dispatch_heuristic(
    *,
    store,
    session_id: str,
    player: str,
    view: Mapping[str, Any],
    binding: Optional[AgentBinding] = None,
) -> DispatchResult:
    """In-process RED_HARVEST — delegates to the legacy heuristic agent.

    ``binding.locator == "RED_HARVEST_LITE"`` (see binding_registry.py)
    plays the same deterministic playbook with weapons (chaff/EMP)
    disabled — the hackathon's easy first opponent.
    """
    from sea_of_colours.agent.heuristic_agent import plan_moves
    from sea_of_colours.snowpark import engine as soc_engine

    weapons_enabled = (binding is None) or (binding.locator != "RED_HARVEST_LITE")
    start = time.time()
    try:
        agent_view = view.get("agent_view") or view
        moves, rationale = plan_moves(agent_view, weapons_enabled=weapons_enabled)
        soc_engine.submit_policy(store, session_id, player, moves)
        return DispatchResult(
            ok=True,
            elapsed_ms=int((time.time() - start) * 1000),
            submitted_policy=True,
            rationale=rationale or "",
            extras={
                "binding_kind": "heuristic",
                "moves_count": len(moves),
                "weapons_enabled": weapons_enabled,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        return DispatchResult(
            ok=False,
            elapsed_ms=int((time.time() - start) * 1000),
            submitted_policy=False,
            error=f"heuristic raised: {exc}",
            extras={"binding_kind": "heuristic", "weapons_enabled": weapons_enabled},
        )


# ── Public entrypoint ────────────────────────────────────────────────
def dispatch_turn(
    *,
    store,
    session_id: str,
    player: str,
    view: Mapping[str, Any],
    binding: AgentBinding,
) -> DispatchResult:
    """Route a turn to the binding's backend; return a uniform result."""
    kind = binding.kind
    if kind == "cortex_agent":
        return _dispatch_cortex_agent(
            binding=binding, session_id=session_id, view=view,
        )
    if kind == "harness_in_process":
        return _dispatch_harness_in_process(
            binding=binding, store=store, session_id=session_id,
            player=player, view=view,
        )
    if kind == "harness_proc":
        return _dispatch_harness_proc(binding=binding)
    if kind == "harness_spcs":
        return _dispatch_harness_spcs(binding=binding)
    if kind == "heuristic":
        return _dispatch_heuristic(
            binding=binding,
            store=store, session_id=session_id, player=player, view=view,
        )
    return DispatchResult(
        ok=False,
        elapsed_ms=0,
        submitted_policy=False,
        error=f"unknown binding kind {kind!r}",
        extras={"binding_kind": kind, "locator": binding.locator},
    )
