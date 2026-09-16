"""Orchestrator-side glue: heuristic agent → SOC engine → audit row.

Exposed as :func:`run_agent_turn`, the single function FastAPI's
``/api/game/{id}/agent/think`` route calls. The contract:

1. Fetch the structured view via :func:`engine.get_view`.
2. Run the in-process :class:`HeuristicAgent` (``RED_HARVEST``, or
   ``RED_HARVEST_LITE`` with weapons disabled).
3. Submit the move queue via :func:`engine.submit_policy` (the engine
   resolves the night once every seat is ready — same path as a human
   player).
4. Persist a row in SOC_AGENT_INVOCATION via
   :func:`engine.save_agent_rationale`.
5. Return an envelope describing what happened (rationale, tool calls,
   night_resolved flag, elapsed milliseconds, runtime label) for the UI
   to render in the LOG panel.

**LLM seats do not come through here.** The Cortex *Agents-API* runtime
that used to live in this module was removed along with its
``soc_create_agent*.sql`` specs. The shipped LLM agent is V12, an
in-process harness dispatched by
:func:`sea_of_colours.orchestrator_2.runtime.run_agent_turn`, which
reaches Cortex over the inference REST endpoint with a PAT and needs no
deployed agent object.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from sea_of_colours.agent.heuristic_agent import HeuristicAgent
from sea_of_colours.snowpark import engine as soc_engine
from sea_of_colours.snowpark.store import SocStore


# ── Agent identity catalogue ─────────────────────────────────────────
#
# RED_HARVEST is the deterministic Python heuristic — our "mainstay"
# agent. It runs by default, requires no Snowflake access, and is what
# powers the [LET RED_HARVEST PLAY] button out of the box.
HEURISTIC_AGENT_NAME = "RED_HARVEST"
HEURISTIC_AGENT_TAG = "RHV"  # v0.9.18 — 3-letter tag for UI display

# Hackathon "training wheels" opponent (RULEBOOK-adjacent, see docs/) —
# the same deterministic HeuristicAgent playbook with weapons
# (build_chaff / build_emp / chaff_flare / emp_launch) disabled.
# Selected via ``runtime_override="red_harvest_lite"``.
HEURISTIC_LITE_AGENT_NAME = "RED_HARVEST_LITE"
HEURISTIC_LITE_RUNTIME_KEY = "red_harvest_lite"

# Back-compat alias for callers that imported the old name.
AGENT_NAME = HEURISTIC_AGENT_NAME

# Runtime labels this module accepts. Anything else is a caller error —
# notably ``"cortex"``, which used to select the retired Agents-API path
# and must now go through the orchestrator's V12 harness instead.
_SUPPORTED_RUNTIMES = frozenset({"heuristic", HEURISTIC_LITE_RUNTIME_KEY})


# Soft ceiling on stored rationale. The on-screen log + audit table both
# read this column, and a 60KB chain-of-thought makes the log unreadable.
# 2KB is enough for ~6 lines of reasoning + the structured tail; if the
# response has a "**PLAN:**" / "PLAN:" marker we always keep that part
# verbatim so the post-mortem stays intact.
RATIONALE_CAP_CHARS = 2_000


def _smart_truncate_rationale(text: str, cap: int = RATIONALE_CAP_CHARS) -> str:
    """Cap reasoning while preserving the structured PLAN/MOVES/RATIONALE tail.

    Strategy:
    1. If ``text`` fits under the cap, return it unchanged.
    2. Otherwise look for the LAST occurrence of a ``PLAN:`` marker
       (``**PLAN:**`` is the markdown form from the agent spec; bare
       ``PLAN:`` also matches). Everything from that marker onward is
       the "structured summary" — the rest of the budget is split
       between a head excerpt of the reasoning and that tail.
    3. If no marker is present, just hard-cut at ``cap`` and add an
       ellipsis suffix so the truncation is visible in the log.

    The cap is intentionally generous (~2KB ≈ a paragraph or two of
    reasoning + the 3-line PLAN/MOVES/RATIONALE block) — the goal is
    "cap, don't kill", per the player's request.
    """
    if not text or len(text) <= cap:
        return text or ""
    # Find the last PLAN: marker (case-insensitive). Markdown bold form
    # wins over bare form because the agent spec asks for it.
    lower = text.lower()
    plan_idx = lower.rfind("**plan:**")
    if plan_idx < 0:
        plan_idx = lower.rfind("plan:")
    if plan_idx < 0:
        return text[: cap - 16].rstrip() + " …[truncated]"
    tail = text[plan_idx:]
    if len(tail) >= cap:
        # The structured block alone exceeds the cap (rare); take it whole
        # so the player at least sees the final committed plan.
        return tail
    # Budget the head: cap − (tail length + separator)
    sep = "\n…[reasoning truncated]…\n"
    head_budget = cap - len(tail) - len(sep)
    if head_budget < 80:
        # Not enough room for a meaningful preamble — just return tail.
        return tail
    head = text[:head_budget].rstrip()
    return f"{head}{sep}{tail}"


def run_agent_turn(
    store: SocStore,
    session_id: str,
    player: str,
    runtime_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one heuristic agent turn end-to-end.

    ``runtime_override`` selects which deterministic playbook plays:

    * ``None`` / ``"heuristic"`` → :data:`HEURISTIC_AGENT_NAME`
      (``RED_HARVEST``).
    * ``"red_harvest_lite"``     → :data:`HEURISTIC_LITE_AGENT_NAME`
      (``RED_HARVEST_LITE``) — same playbook with chaff/EMP disabled;
      the hackathon's easy first opponent.

    Returns the envelope FastAPI surfaces to the front-end, including
    ``agent_id`` so the UI / audit log can render the actual name of
    whoever played this turn.

    :raises ValueError: for any other runtime — most importantly
        ``"cortex"``. That path was removed with the Agents-API specs;
        LLM seats are dispatched by
        :func:`sea_of_colours.orchestrator_2.runtime.run_agent_turn`
        with ``agent_label="tabula_v12"``. Failing loudly here beats
        silently handing an LLM seat to the heuristic, which is exactly
        the misattribution that hid past regressions.
    """
    requested = (runtime_override or "heuristic").strip().lower()
    if requested not in _SUPPORTED_RUNTIMES:
        raise ValueError(
            f"runtime_override={requested!r} is not supported by the "
            f"heuristic runtime (accepts: "
            f"{', '.join(sorted(_SUPPORTED_RUNTIMES))}). For the V12 LLM "
            f"agent use orchestrator_2.runtime.run_agent_turn(..., "
            f"agent_label='tabula_v12')."
        )

    started = time.time()
    view = soc_engine.get_view(store, session_id, player)
    agent_view = view.get("agent_view") or {}

    # RED_HARVEST_LITE (hackathon "no weapons" tutorial opponent) is
    # selected the same way "heuristic" is — via runtime_override — and
    # never builds/fires chaff or EMP, in orbit or at night.
    is_lite = requested == HEURISTIC_LITE_RUNTIME_KEY
    weapons_enabled = not is_lite
    heuristic_agent_name = HEURISTIC_LITE_AGENT_NAME if is_lite else HEURISTIC_AGENT_NAME

    # v0.9.5 — Orbit phase is driven by RED_HARVEST's deterministic
    # playbook (repair → 2 probes → harvester, gated on the 3-slot
    # action cap and the seat's credit balance).
    if str(view.get("phase") or "") == "orbit":
        from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

        orbit_actions, rationale = plan_orbit_actions(
            agent_view, weapons_enabled=weapons_enabled,
        )
        submit_result = soc_engine.submit_orbit_actions(
            store, session_id, player, orbit_actions,
        )
        orbit_resolved = bool(submit_result.get("orbit_resolved"))
        ms_elapsed = int((time.time() - started) * 1000)
        tool_calls_log = [
            {"name": "soc_get_view", "args": {"echo": "in-process"}},
            {
                "name": "soc_submit_orbit_actions",
                "args": {"action_count": len(orbit_actions)},
            },
        ]
        soc_engine.save_agent_rationale(
            store,
            session_id,
            view.get("day", agent_view.get("hud", {}).get("day", 0)),
            heuristic_agent_name,
            player,
            rationale,
            runtime="heuristic",
            prompt_excerpt=None,
            tool_calls=tool_calls_log,
            response_text=None,
            ms_elapsed=ms_elapsed,
        )
        return {
            "ok": True,
            "agent_id": heuristic_agent_name,
            "player": player,
            "runtime": "heuristic",
            "rationale": rationale,
            "moves": [],
            "orbit_actions": orbit_actions,
            "tool_calls": tool_calls_log,
            "submitted": True,
            "night_resolved": False,
            "orbit_resolved": orbit_resolved,
            "submit_result": submit_result,
            "ms_elapsed": ms_elapsed,
        }

    agent = HeuristicAgent(
        name=heuristic_agent_name, weapons_enabled=weapons_enabled,
    )
    plan = agent.play(agent_view)
    moves = plan["moves"]
    tool_calls = list(plan["tool_calls"])

    # The heuristic ALWAYS submits — an empty queue is how it says "I
    # have nothing to do tonight, lock my seat". The night can't resolve
    # until every seat is in, so skipping this would deadlock the game.
    submit_result = soc_engine.submit_policy(store, session_id, player, moves)
    night_resolved = bool(submit_result.get("night_resolved"))

    ms_elapsed = int((time.time() - started) * 1000)
    capped_rationale = _smart_truncate_rationale(plan["rationale"])

    soc_engine.save_agent_rationale(
        store,
        session_id,
        view.get("day", agent_view.get("hud", {}).get("day", 0)),
        heuristic_agent_name,
        player,
        capped_rationale,
        runtime="heuristic",
        prompt_excerpt=None,
        tool_calls=tool_calls,
        response_text=None,
        ms_elapsed=ms_elapsed,
    )

    return {
        "ok": True,
        "agent_id": heuristic_agent_name,
        "player": player,
        "runtime": "heuristic",
        "rationale": capped_rationale,
        "moves": moves,
        "tool_calls": tool_calls,
        "submitted": bool(submit_result.get("ok")),
        "night_resolved": night_resolved,
        "submit_result": submit_result,
        "ms_elapsed": ms_elapsed,
    }
