"""Binding registry — how orchestrator_2 decides where to send a turn.

An :class:`AgentBinding` names a delivery target for one ``(session,
player)`` pair. Four kinds today:

* ``cortex_agent``        — bare Snowflake Cortex Agent. Orchestrator
                            builds the universal envelope and POSTs to
                            ``agents/<locator>:run``. ``locator`` is the
                            agent's Snowflake object name.
* ``harness_in_process``  — Python module living in this repo. Locator
                            is ``"<module>:<callable>"``. The
                            dispatcher imports it and calls the entry
                            point with the universal envelope. The
                            harness handles its own prompt + tool calls.
* ``harness_proc``        — Snowflake stored procedure (future). Locator
                            is the proc identifier. Dispatcher invokes
                            ``CALL <proc>(p_session_id, p_player, p_state_json)``.
                            Stub today; raises NotImplementedError.
* ``harness_spcs``        — Snowpark Container Service (future). Locator
                            is the HTTPS URL. Dispatcher POSTs the
                            envelope. Stub today.
* ``heuristic``           — in-process RED_HARVEST. Locator is the
                            strategy name (``"RED_HARVEST"``).

Resolution order (most explicit first):

0. ``agent_label`` — the per-seat value from the New Game menu, looked up
   in :data:`AGENT_LABEL_BINDINGS`. This is the normal path.
1. ``SOC_BINDING_<PLAYER>`` env var — e.g.
   ``SOC_BINDING_P1=harness_in_process:my_team.harness:run``.
2. ``SOC_CORTEX_AGENT`` env var + the :data:`KNOWN_AGENT_BINDINGS` map.
3. ``SOC_AGENT_RUNTIME=heuristic`` → kind=heuristic.
4. Default: heuristic.

**Registering your own agent (the hackathon path).** You do not edit
this file. Any directory under ``harnesses/`` containing an
``agent.json`` is discovered and registered at import (v1.39) — the seat
routes, and the New Game dropdown lists it because the web UI builds its
roster from :func:`selectable_agents` rather than a hardcoded list.
``scripts/new_agent.py`` creates the directory and its manifest for you.
See :mod:`sea_of_colours.orchestrator_2.agent_manifest` for the schema
and why registration works this way, and ``harnesses/tabula_v12/README.md``
for what to change inside your fork once it exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


# ── Public dataclass ────────────────────────────────────────────────
@dataclass(frozen=True)
class AgentBinding:
    """One delivery target for a turn."""

    kind: str
    locator: str
    agent_label: Optional[str] = None  # display name (e.g. "TABULA_V12")
    # UI metadata. ``menu_label`` is what a player reads in the New Game
    # dropdown; ``None`` keeps the binding routable but unlisted (useful
    # for a half-finished fork you don't want opponents picking yet).
    # ``needs_llm`` drives the credential preflight — a seat bound to an
    # LLM harness fails loudly at game creation without a PAT rather than
    # silently submitting zero moves all game.
    menu_label: Optional[str] = None
    needs_llm: bool = False


# ── Known-agent map (orchestrator_2's view of who's who) ───────────
#
# Maps a Cortex agent name (the historical ``SOC_CORTEX_AGENT`` env var
# value) to the binding orchestrator_2 should use for it. Keeps the
# existing eval CLI ergonomics: set ``SOC_CORTEX_AGENT=...`` and
# orchestrator_2 figures out whether that name needs a harness wrap.
#
# IMPORTANT: orchestrator_2 ONLY claims agents in this map. Anything
# else falls through to the heuristic.
KNOWN_AGENT_BINDINGS = {
    # v12 — the WORLD-VIEW release, and the only LLM agent this
    # distribution ships. Deterministic packager, redsign seam-control,
    # continuous journal, plus two persistent "beyond tonight" prompt
    # components: an agent-authored WORLD VIEW (cross-day rivals+map
    # model) and a static OUT-OF-GRID KNOWLEDGE reference (the fixed
    # scoring/weapon/redsign physics).
    #
    # Every earlier harness (the pilot_* and tabula_v2..v11 lineage) was
    # deleted for the hackathon distribution — it was R&D history, and
    # leaving a dozen near-identical agents in the tree is the fastest
    # way to confuse someone asking "which one do I fork?". The answer
    # is: this one. Fork it with ``scripts/new_agent.py``; don't edit it
    # in place, or you lose the baseline you are trying to beat.
    "SOC_RED_REAPER_TABULA_V12": AgentBinding(
        kind="harness_in_process",
        locator="sea_of_colours.orchestrator_2.harnesses.tabula_v12.harness:run",
        agent_label="TABULA_V12",
        menu_label="V12 — LLM agent (needs a Snowflake PAT · slow)",
        needs_llm=True,
    ),
    # SOC_NEW_AGENT_ANCHOR — scripts/new_agent.py inserts forks above this
    # line. Hand-written entries are fine too; the anchor only exists so
    # the script never has to guess where the dict ends.
}

# Heuristic fallback — used when no other binding resolves.
HEURISTIC_BINDING = AgentBinding(
    kind="heuristic",
    locator="RED_HARVEST",
    agent_label="RED_HARVEST",
    menu_label="RED_HARVEST — heuristic bot, weapons on",
)

# Hackathon "training wheels" opponent — same deterministic playbook,
# weapons (chaff/EMP) never built or fired. ``_dispatch_heuristic``
# branches on ``locator`` to pick this apart from the competitive
# ``HEURISTIC_BINDING`` above.
HEURISTIC_LITE_BINDING = AgentBinding(
    kind="heuristic",
    locator="RED_HARVEST_LITE",
    agent_label="RED_HARVEST_LITE",
    menu_label="RED_HARVEST_LITE — heuristic bot, no weapons (start here)",
)

# A human at the keyboard. Listed here so the New Game dropdown can be
# built from one roster instead of a hardcoded list that drifts.
HUMAN_BINDING = AgentBinding(
    kind="human",
    locator="HUMAN",
    agent_label="HUMAN",
    menu_label="HUMAN — pilot from this browser",
)

# ── The roster ──────────────────────────────────────────────────────
#
# Game-config agent labels — the values stored in ``GameSession.agents``
# (chosen in the New Game menu) mapped to a binding. Registering here is
# all it takes to make an agent both routable and selectable: the seat
# dispatches with no env plumbing, and the dropdown lists it because the
# UI reads ``/api/meta/agents``, which is built from this dict.
#
# **Add your fork here.** Convention is ``<team>_<agent>`` — e.g.
# ``"redwatch_reaper"`` — so a room full of forks stays legible and two
# teams can't collide on a name. ``scripts/new_agent.py`` appends the
# entry for you.
AGENT_LABEL_BINDINGS = {
    "human": HUMAN_BINDING,
    "red_harvest_lite": HEURISTIC_LITE_BINDING,
    "red_harvest": HEURISTIC_BINDING,
    "tabula_v12": KNOWN_AGENT_BINDINGS["SOC_RED_REAPER_TABULA_V12"],
    # SOC_NEW_AGENT_LABEL_ANCHOR — kept for hand-written entries. Forks
    # no longer land here; see the discovery pass below.
}


# ── Discovered forks ────────────────────────────────────────────────
#
# v1.39. Every directory under ``harnesses/`` with an ``agent.json`` is
# registered automatically, so a fork is one self-contained folder and
# nothing shared has to be edited to add one.
#
# This replaced two inserted lines per fork in this very file. With one
# fork that was fine; with a room of forty it meant every team's push
# conflicted with every other team's, and collecting the agents at the
# end of the day meant forty manual merges — each an opportunity to drop
# somebody's entry. See ``agent_manifest.py`` for the reasoning in full.
#
# Two deliberate properties:
#
# * **Built-ins win.** A discovered manifest never overwrites a label
#   already in the dict above, so no fork can shadow ``tabula_v12`` and
#   quietly become the thing everyone is benchmarked against.
# * **A bad manifest is skipped, not fatal.** Import-time discovery runs
#   on every server start and every test collection; one team's trailing
#   comma must not stop the room. ``soc doctor`` surfaces the problems.
def _register_discovered() -> list[str]:
    """Fold ``agent.json`` forks into the roster. Returns any problems."""
    try:
        from . import agent_manifest
    except Exception as exc:  # pragma: no cover - import guard
        return [f"agent discovery unavailable: {exc}"]

    manifests, problems = agent_manifest.discover()
    for man in manifests:
        if man.label in AGENT_LABEL_BINDINGS:
            problems.append(
                f"{man.directory / agent_manifest.MANIFEST_NAME}: "
                f"{man.label!r} is already a built-in agent — pick another "
                f"--team/--name so yours is scored separately."
            )
            continue
        binding = AgentBinding(
            kind="harness_in_process",
            locator=man.locator,
            agent_label=man.label.upper(),
            menu_label=man.menu_label
            or f"{man.label.upper()} — {man.team}'s agent",
            needs_llm=man.needs_llm,
        )
        KNOWN_AGENT_BINDINGS.setdefault(man.const, binding)
        AGENT_LABEL_BINDINGS[man.label] = binding
    return problems


#: The roster as shipped, captured before discovery folds any forks in.
#: v1.42 — there used to be no way to ask this, so the test guarding the
#: roster had to hardcode four names and consequently went red the moment
#: anyone ran ``scripts/new_agent.py``. On a day when every attendee mints
#: a fork in the first ten minutes, a test that fails for doing the thing
#: the guide told you to do is not a guard, it is noise — and it trained
#: people to ignore a red suite, which is the one habit the day cannot
#: afford. The guarantee worth keeping is narrower: nothing joins the
#: *shipped* roster by accident. Forks are supposed to join.
SHIPPED_AGENT_LABELS: frozenset[str] = frozenset(AGENT_LABEL_BINDINGS)

# Recorded rather than printed: importing a module should not write to
# anyone's console, and the one caller that genuinely wants to nag about
# a broken fork (``soc doctor``) can read this.
DISCOVERY_PROBLEMS: list[str] = _register_discovered()

# Labels that mean "just play the in-process heuristic".
_HEURISTIC_LABELS = {"human", "red_harvest", "heuristic"}


def selectable_agents() -> list[dict]:
    """The New Game roster, in menu order.

    The dropdown used to be a hardcoded list in ``app.js``, which meant
    registering a fork took a frontend edit as well — easy to miss, and
    the failure mode (your agent works but you cannot pick it) wastes
    hackathon time. Serving the roster from the registry makes one edit
    enough.
    """
    out = []
    for label, binding in AGENT_LABEL_BINDINGS.items():
        if not binding.menu_label:
            continue
        out.append(
            {
                "value": label,
                "label": binding.menu_label,
                "kind": binding.kind,
                "needs_llm": binding.needs_llm,
            }
        )
    return out


def _parse_env_binding(spec: str) -> AgentBinding:
    """Parse ``"<kind>:<locator>"``, with an optional ``"#<label>"`` suffix.

    The label separator is ``#`` rather than a third colon because
    harness locators contain a colon themselves
    (``module.path:callable``). Splitting on colons made
    ``harness_in_process:pkg.harness:run`` parse as locator ``pkg.harness``
    with the label ``run``, so the import failed and the seat silently
    fell back to the heuristic.
    """
    head, _, label = spec.partition("#")
    kind, sep, locator = head.partition(":")
    if not sep or not locator.strip():
        raise ValueError(
            f"env binding {spec!r} must be 'kind:locator' "
            f"(optionally 'kind:locator#label')"
        )
    return AgentBinding(
        kind=kind.strip(),
        locator=locator.strip(),
        agent_label=label.strip() or None,
    )


def resolve_binding(
    store,
    session_id: str,
    player: str,
    *,
    runtime_override: Optional[str] = None,
    agent_label: Optional[str] = None,
) -> AgentBinding:
    """Find the binding for ``(session_id, player)`` using the resolution order.

    ``runtime_override`` short-circuits everything:
    - ``"heuristic"`` → :data:`HEURISTIC_BINDING`
    - ``"cortex"``    → continue resolution (falls through to env vars + KNOWN_AGENT_BINDINGS)

    ``agent_label`` is the per-seat game-config label (from
    ``GameSession.agents`` / the New Game menu). When it names a known
    agent (:data:`AGENT_LABEL_BINDINGS`) it wins over env vars — this is
    how the live server routes a menu-selected ``"tabula_v12"`` seat to the
    harness without any env plumbing. A ``runtime_override="heuristic"``
    still forces the heuristic (used by the safety-net fallback).

    Order otherwise: seat label → env override → SOC_CORTEX_AGENT →
    SOC_AGENT_RUNTIME → heuristic. The Snowflake ``SOC_AGENT_BINDING``
    table is not consulted yet — that's a Phase 5 deliverable.
    """
    if runtime_override == "heuristic":
        return HEURISTIC_BINDING

    # 0. Explicit per-seat game-config label (New Game / multiplayer menu).
    if agent_label:
        lbl = agent_label.strip().lower()
        # ``human`` is in the roster so the menu can be built from one
        # list, but it is not a dispatch target. If something asks us to
        # take a human's turn anyway (a bot-driver bug, a seat that was
        # reassigned mid-game), fall back to the heuristic rather than
        # handing the dispatcher a kind it cannot route.
        if lbl in _HEURISTIC_LABELS:
            return HEURISTIC_BINDING
        if lbl in AGENT_LABEL_BINDINGS:
            return AGENT_LABEL_BINDINGS[lbl]

    # 1. Per-player env var (most explicit).
    env_key = f"SOC_BINDING_{player.upper()}"
    spec = os.environ.get(env_key)
    if spec:
        return _parse_env_binding(spec)

    # 2. SOC_CORTEX_AGENT + known-agent map.
    agent_name = (os.environ.get("SOC_CORTEX_AGENT") or "").strip()
    if agent_name and agent_name in KNOWN_AGENT_BINDINGS:
        return KNOWN_AGENT_BINDINGS[agent_name]

    # 3. Bare Cortex agent — anyone setting SOC_CORTEX_AGENT to a name
    #    we don't know about wants to talk to it raw. We don't claim
    #    those (the v1 orchestrator does); fall through to heuristic so
    #    nothing accidentally double-dispatches.
    if runtime_override == "cortex":
        # Caller insisted on cortex but we don't recognise the agent.
        # Treat as a bare-Cortex binding; the dispatcher will POST the
        # universal envelope to that agent name.
        if agent_name:
            return AgentBinding(
                kind="cortex_agent",
                locator=agent_name,
                agent_label=agent_name,
            )

    # 4. SOC_AGENT_RUNTIME (legacy switch).
    mode = (os.environ.get("SOC_AGENT_RUNTIME") or "heuristic").strip().lower()
    if mode == "heuristic":
        return HEURISTIC_BINDING

    # 5. Default safety net.
    return HEURISTIC_BINDING
