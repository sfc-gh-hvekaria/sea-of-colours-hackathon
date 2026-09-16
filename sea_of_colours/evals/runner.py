"""Scenario execution + assertion evaluation.

The runner is intentionally thin — a scenario builds a fixture, the
agent runtime fires (heuristic by default; Cortex if SOC_BACKEND is
already wired), the policy gets pulled off the store, and each
assertion is evaluated. All results are bundled into a
:class:`ScenarioResult` for printing or aggregation.

The agent's other seat (``p2`` by convention; configurable per
scenario) is left unsubmitted so the night never resolves — that lets
us inspect the policy unaffected by post-night cleanup. If a scenario
needs the opponent to act we'd model that as part of the *fixture*
(positioning + trails + echoes already in place), not as a real
turn from the other seat.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

from sea_of_colours.agent.runtime import run_agent_turn
from sea_of_colours.evals.assertions import (
    Assertion,
    AssertionContext,
    AssertionResult,
)
from sea_of_colours.evals.configs import EvalConfig
from sea_of_colours.snowpark import engine as soc_engine
from sea_of_colours.snowpark.backend import SOC_BACKEND, get_store
from sea_of_colours.snowpark.store import InMemorySocStore


def _mirror_fixture_to_backend(
    in_memory_store: InMemorySocStore, session_id: str,
):
    """Mirror an in-memory fixture into the configured backend store.

    :class:`~sea_of_colours.evals.builder.WorldBuilder` always lands its
    session in an :class:`InMemorySocStore` for speed + determinism.
    When the eval is targeting the deployed Cortex agent
    (``SOC_BACKEND=snowflake``), that's a problem — the warehouse
    proc ``SOC_SUBMIT_POLICY`` reads from
    :class:`SnowparkSocStore`-backed tables, so its lookup against the
    in-memory fixture's session_id returns "session not found" and
    the seat locks empty.

    The fix is to copy the constructed session into whatever store
    :func:`sea_of_colours.snowpark.backend.get_store` resolves to, BEFORE
    invoking the agent runtime. We use :func:`engine.save_session_full`
    so every SOC_* table (entity_state, hoard, grid_cell, etc.) lands
    consistently — the agent's view-resolution path on a Snowflake
    backend hits multiple tables, not just SOC_GAME_SESSION.

    Returns the store the rest of the run should use (the backend
    store when SOC_BACKEND=snowflake, otherwise the original in-memory
    store — no mirror needed for the heuristic-only path).
    """
    if SOC_BACKEND != "snowflake":
        return in_memory_store
    backend_store = get_store()
    if backend_store is in_memory_store:
        # Already the configured backend (memory mode forced into the
        # singleton); nothing to mirror.
        return backend_store
    sess = soc_engine._hydrate_session(in_memory_store, session_id)
    soc_engine.save_session_full(backend_store, sess)
    return backend_store


@contextlib.contextmanager
def _patched_env(overrides: Mapping[str, str]) -> Iterator[None]:
    """Temporarily set env vars for the body of a ``with`` block.

    Used by :func:`run_scenario` to scope :class:`EvalConfig` settings
    (``SOC_AGENT_WORLD_VIEW``, ``SOC_CORTEX_AGENT``) to a single
    scenario run. Restores the previous values (or removes keys that
    were unset before) on exit, so back-to-back runs of different
    configs don't bleed into each other.
    """
    previous: Dict[str, Optional[str]] = {
        k: os.environ.get(k) for k in overrides
    }
    try:
        for k, v in overrides.items():
            os.environ[k] = v
        yield
    finally:
        for k, prev in previous.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


@dataclass
class ScenarioResult:
    """Aggregated outcome of one scenario evaluation pass.

    A scenario can pass/fail per-assertion; ``passed`` is the AND of
    all assertion outcomes. ``policy`` is the wire-format move queue
    we actually submitted (so a markdown report can render it).
    ``rationale`` is whatever the agent runtime returned in its
    envelope (Cortex transcript or heuristic one-liner).
    """
    scenario_name: str
    summary: str
    policy: List[Dict[str, Any]]
    rationale: str
    agent_id: str
    runtime: str
    results: List[AssertionResult] = field(default_factory=list)
    sample_index: int = 0
    config_label: Optional[str] = None
    session_id: Optional[str] = None
    watch_url: Optional[str] = None

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results) if self.results else False

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def render_markdown(self) -> str:
        """Render this result as a single markdown section."""
        lines: List[str] = []
        verdict = "PASS" if self.passed else "FAIL"
        lines.append(
            f"## {self.scenario_name} — {verdict} ({self.pass_count}/{len(self.results)})"
        )
        if self.summary:
            lines.append(f"_{self.summary}_")
            lines.append("")
        lines.append(f"**Agent:** {self.agent_id} (runtime={self.runtime})")
        lines.append("")
        lines.append("**Rationale:**")
        body = self.rationale.strip() or "(no rationale captured)"
        lines.append("> " + body.replace("\n", "\n> "))
        lines.append("")
        lines.append("**Policy submitted:**")
        if not self.policy:
            lines.append("> _(empty queue)_")
        else:
            for move in self.policy:
                lines.append(f"> - `{move}`")
        lines.append("")
        lines.append("**Assertions:**")
        for r in self.results:
            mark = "✓" if r.passed else "✗"
            lines.append(f"- {mark} **{r.name}** — {r.detail}")
            if not r.passed:
                lines.append(f"    - expected: {r.expected}")
                lines.append(f"    - observed: {r.observed}")
        if self.watch_url:
            lines.append("")
            lines.append(
                f"**Replay:** [`{self.session_id}`]({self.watch_url})"
            )
        return "\n".join(lines)


def _extract_policy_from_store(
    store, session_id: str, day: int, player: str
) -> List[Dict[str, Any]]:
    """Pull the wire-format move queue the agent submitted.

    Phase-aware: when the session is in the Orbit phase, the agent's
    submission lives on ``sess.pending_orbit_actions[player]`` (an
    ``OrbitAction`` list), NOT in ``store.list_policies``. We
    serialise via :func:`_orbit_actions_to_wire` so the assertion
    layer sees the same dict shape (``{"a": "build_probe", ...}``)
    regardless of which phase produced the queue.

    Falls back to :meth:`SocStore.list_policies` for planning-phase
    turns, defaulting to the empty list rather than raising so a
    scenario that records "agent submitted nothing" still produces a
    result.
    """
    # Peek at phase without re-computing the whole view — the session
    # dict is cheaper to hydrate than a full agent view.
    try:
        sess = soc_engine._hydrate_session(store, session_id)
        phase_name = str(getattr(sess.phase, "value", sess.phase) or "").lower()
    except Exception:
        sess = None
        phase_name = ""

    if sess is not None and phase_name == "orbit":
        from sea_of_colours.game.session import _orbit_actions_to_wire

        pending = sess.pending_orbit_actions.get(player) or []
        return _orbit_actions_to_wire(pending)

    policies = store.list_policies(session_id, day) or {}
    queue = policies.get(player) or []
    # Normalise to list-of-dicts. The store can hold either pure-Python
    # dicts or Move dataclasses; we want dicts for assertion checks.
    out: List[Dict[str, Any]] = []
    for entry in queue:
        if isinstance(entry, dict):
            out.append(entry)
        elif hasattr(entry, "to_dict"):
            out.append(entry.to_dict())
        else:
            # Best effort — surface whatever we got so the report
            # tells the developer their fixture wrote a weird shape.
            out.append({"_raw": repr(entry)})
    return out


_OPPONENT: Dict[str, str] = {"p1": "p2", "p2": "p1"}


def _agent_context_extras(env: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract the pilot-comprehension telemetry from an agent envelope.

    Two-phase harnesses (pilot_v3, pilot_v4) stash their planning
    telemetry on ``env["extras"]``:

    * ``plan_label``          — the strategist's compact plan token.
    * ``decision.selections`` — the tactician's raw selection list.
    * ``materialized_count``  — how many moves actually landed in the
                                policy queue.

    Legacy single-phase agents don't populate any of these, so we
    default everything to zero/empty and let assertions that require
    them fail explicitly rather than silently pass.
    """
    rationale = str(env.get("rationale") or "")
    extras = env.get("extras") or {}
    if not isinstance(extras, Mapping):
        extras = {}
    plan_label = str(extras.get("plan_label") or "")
    decision = extras.get("decision") or {}
    if isinstance(decision, Mapping):
        selections = decision.get("selections") or []
        selection_count = (
            len(selections) if isinstance(selections, (list, tuple)) else 0
        )
    else:
        selection_count = 0
    # ``materialized_count`` is the authoritative count from the harness
    # itself; if a harness doesn't provide one, fall back to zero (the
    # assertion treats "both zero" as untracked and vacuously passes).
    materialized_count = int(extras.get("materialized_count") or 0)
    return {
        "rationale": rationale,
        "plan_label": plan_label,
        "selection_count": selection_count,
        "materialized_count": materialized_count,
    }

# Default watcher base — overridable via SOC_WATCH_BASE_URL so users running
# the FastAPI server on a non-default host/port get correct deep links.
_DEFAULT_WATCH_BASE = "http://127.0.0.1:8000"


def _build_watch_url(session_id: str) -> str:
    """Compose the deep-link the watcher UI reads as ``?session=<id>``."""
    base = os.environ.get("SOC_WATCH_BASE_URL", _DEFAULT_WATCH_BASE).rstrip("/")
    return f"{base}/?session={session_id}"


def _resolve_night_for_replay(
    store, session_id: str, agent_player: str,
) -> None:
    """Tick the night so the engine emits replay frames.

    The eval harness deliberately stops after the agent's seat
    submits — assertions run against the policy queue + pre-night
    session so per-move checks (HarvesterChainHits, MustAvoid, …)
    aren't disturbed by post-night mutation. That's the right
    contract for scoring runs but it leaves
    :attr:`GameSession.last_night_replay` empty, which means the
    watcher UI sees an empty ``days`` payload and renders no frames.

    For the ``--replay`` path we submit an empty queue for the
    opponent seat AFTER assertions have already been evaluated.
    ``submit_policy`` then trips ``both_ready()`` → night resolves →
    :func:`engine.submit_policy` calls :meth:`store.append_replay_frames`
    via the embedded post-resolve hook. The resulting frames are
    visible at ``/?session=<id>`` for whatever watcher backend the
    user is pointing at.
    """
    opponent = _OPPONENT.get(agent_player)
    if opponent is None:
        return
    soc_engine.submit_policy(store, session_id, opponent, [])


def run_scenario(
    scenario,
    *,
    runtime_override: Optional[str] = None,
    sample_index: int = 0,
    config: Optional[EvalConfig] = None,
    record_replay: bool = False,
) -> ScenarioResult:
    """Execute one scenario and return its :class:`ScenarioResult`.

    ``runtime_override`` is forwarded to
    :func:`sea_of_colours.agent.runtime.run_agent_turn` so the caller
    can pin ``"heuristic"`` (default for fast pytest runs) or
    ``"cortex"`` (for live agent evaluation).

    ``sample_index`` is purely metadata so the CLI's ``--samples N``
    multi-shot mode can label rows in its summary table.

    ``config`` (Phase 1 orchestrator A/B) pins the world-view shape and
    the deployed Cortex agent for this run. We push the config's env
    overrides (``SOC_AGENT_WORLD_VIEW`` / ``SOC_CORTEX_AGENT``) before
    the agent runtime fires and restore them on exit, so a single
    process can sweep multiple configs without bleeding state. When
    ``config`` is ``None`` (the heuristic baseline path the existing
    pytest suite uses), env vars are left untouched and the run is
    identical to pre-Phase-1 behaviour.

    ``record_replay`` triggers a post-assertion night resolution so
    ``SOC_REPLAY_FRAME`` rows materialise and the watcher UI can
    render the resulting playback at ``/?session=<id>``. Assertions
    are unaffected — they evaluate against the pre-night state, as
    before. Only enable this for paths where you actually plan to
    open the watcher; resolving the night writes additional rows
    into the backing store and triggers asset/parcel mutations the
    fast unit-test path doesn't want.
    """
    overrides = config.env_overrides() if config is not None else {}
    with _patched_env(overrides):
        store, session_id = scenario.build()
        # If we're evaluating against the deployed Cortex agent, mirror
        # the in-memory fixture into the Snowflake store so the
        # warehouse-side proc can actually find the session. The
        # heuristic path is unaffected (mirror is a no-op when
        # SOC_BACKEND!=snowflake) — see :func:`_mirror_fixture_to_backend`.
        if runtime_override == "cortex":
            store = _mirror_fixture_to_backend(store, session_id)
        sess = soc_engine._hydrate_session(store, session_id)
        # Tag the session so the eval command center can filter it out
        # of the watcher's general session picker AND map it back to
        # its scenario + config. We use a structured prefix the
        # /api/evals/sessions endpoint parses
        # (``eval:<scenario>:<config>``). Only tagged when the caller
        # wants replay frames — pytest scenarios that exit before
        # touching the store keep their generated season label, so the
        # offline test suite is unaffected.
        if record_replay:
            cfg_label = config.label if config is not None else "default"
            sess.season_name = f"eval:{scenario.name}:{cfg_label}"
            soc_engine.save_session_full(store, sess)
        day_at_run = int(sess.day)
        player = scenario.player

        env = run_agent_turn(
            store, session_id, player, runtime_override=runtime_override,
        )

        policy = _extract_policy_from_store(
            store, session_id, day_at_run, player,
        )

        # Re-hydrate the session AFTER the turn so the assertion
        # context reflects any mutation the agent runtime made. (For
        # heuristic runs the engine doesn't tick the day because p2
        # hasn't submitted; for Cortex runs the same invariant holds.)
        sess_after = soc_engine._hydrate_session(store, session_id)
        ctx_extras = _agent_context_extras(env)
        context = AssertionContext(
            session=sess_after, player=player, day_at_run=day_at_run,
            rationale=ctx_extras["rationale"],
            plan_label=ctx_extras["plan_label"],
            selection_count=ctx_extras["selection_count"],
            materialized_count=ctx_extras["materialized_count"],
        )

        results = [
            a.evaluate(policy, context=context) for a in scenario.assertions
        ]

        watch_url: Optional[str] = None
        if record_replay:
            # Best-effort: a malformed agent run shouldn't tank the
            # scenario result here. We log nothing because callers
            # already render the assertion verdicts; the watcher
            # link is purely a convenience.
            try:
                _resolve_night_for_replay(store, session_id, player)
                watch_url = _build_watch_url(session_id)
            except Exception:
                watch_url = None

    return ScenarioResult(
        scenario_name=scenario.name,
        summary=scenario.summary,
        policy=policy,
        rationale=str(env.get("rationale") or ""),
        agent_id=str(env.get("agent_id") or ""),
        runtime=str(env.get("runtime") or runtime_override or ""),
        results=results,
        sample_index=sample_index,
        config_label=config.label if config is not None else None,
        session_id=session_id,
        watch_url=watch_url,
    )
