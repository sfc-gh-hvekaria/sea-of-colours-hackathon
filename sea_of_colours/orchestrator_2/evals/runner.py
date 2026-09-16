"""Eval runner for orchestrator_2.

Thin adapter over :mod:`sea_of_colours.evals.runner` that swaps the
agent-turn callable to point at
:func:`sea_of_colours.orchestrator_2.runtime.run_agent_turn`. Scenarios,
assertions, fixture builders, and the markdown report renderer come
from the v1 evals package unchanged.

This module also registers a ``tabula_v12`` :class:`EvalConfig`, and
resolves any registered agent label as a config, so the eval CLI works
through the new orchestrator path.
"""

from __future__ import annotations

from typing import Optional

from sea_of_colours.evals.configs import CONFIGS as _LEGACY_CONFIGS
from sea_of_colours.evals.configs import EvalConfig
from sea_of_colours.evals.runner import (  # re-exported convenience
    ScenarioResult,
    _agent_context_extras,
    _build_watch_url,
    _extract_policy_from_store,
    _mirror_fixture_to_backend,
    _patched_env,
    _resolve_night_for_replay,
)
from sea_of_colours.evals.assertions import AssertionContext
from sea_of_colours.snowpark import engine as soc_engine

# Use orchestrator_2's run_agent_turn — this is the ONLY behavioural
# divergence from the v1 runner.
from sea_of_colours.orchestrator_2.runtime import run_agent_turn


# orchestrator_2-specific eval configs, on top of the v1 runner's.
#
# v1.12 — the `pilot_v2` / `pilot_v4` configs were removed. They set
# `SOC_CORTEX_AGENT` to agent names no longer in `KNOWN_AGENT_BINDINGS`,
# so resolution fell through to the heuristic: you asked to benchmark an
# LLM, the run completed, and the numbers you got back were RED_HARVEST's.
# A silently-wrong eval is worse than a missing one, hence deletion
# rather than a rename.
CONFIGS = {
    **{k: v for k, v in _LEGACY_CONFIGS.items()},  # inherit all v1 configs
    "tabula_v12": EvalConfig(
        label="tabula_v12",
        world_view="grid",
        cortex_agent="SOC_RED_REAPER_TABULA_V12",
        description=(
            "V12 — the shipped LLM agent, and the baseline a hackathon "
            "fork has to beat. Routes through orchestrator_2's dispatcher "
            "to the in-process harness "
            "`harnesses/tabula_v12/harness:run`, which precomputes a "
            "deterministic option menu, runs a THINK/PLAN split over "
            "Cortex inference, then compiles the selected option ids into "
            "a move queue. Needs a Snowflake PAT."
        ),
    ),
}


def config_for_agent_label(label: str) -> EvalConfig:
    """Build a config for any registered harness, including your fork.

    Forks don't need a hand-written entry in :data:`CONFIGS`: registering
    in ``binding_registry.AGENT_LABEL_BINDINGS`` is enough, and the eval
    CLI resolves the label through here. Keeps "it plays in the browser"
    and "it can be benchmarked" from being two separate registrations.
    """
    from sea_of_colours.orchestrator_2 import binding_registry as _reg

    binding = _reg.AGENT_LABEL_BINDINGS.get(label.strip().lower())
    if binding is None:
        known = ", ".join(sorted(_reg.AGENT_LABEL_BINDINGS))
        raise KeyError(f"unknown agent label {label!r}. Registered: {known}")
    cortex_agent = next(
        (k for k, v in _reg.KNOWN_AGENT_BINDINGS.items() if v is binding),
        binding.agent_label or label,
    )
    return EvalConfig(
        label=label,
        world_view="grid",
        cortex_agent=cortex_agent,
        description=f"Registered agent {binding.agent_label or label} "
                    f"({binding.kind}: {binding.locator}).",
    )


def get_config(label: str) -> EvalConfig:
    """Same contract as the v1 ``get_config`` — KeyError on miss.

    Falls back to :func:`config_for_agent_label` so a registered fork can
    be evaluated by its menu label without writing an eval config.
    """
    if label in CONFIGS:
        return CONFIGS[label]
    try:
        return config_for_agent_label(label)
    except KeyError:
        pass
    known = ", ".join(sorted(CONFIGS.keys()))
    raise KeyError(f"unknown eval config {label!r}. Known configs: {known}")


def run_scenario(
    scenario,
    *,
    runtime_override: Optional[str] = None,
    sample_index: int = 0,
    config: Optional[EvalConfig] = None,
    record_replay: bool = False,
) -> ScenarioResult:
    """Execute one scenario through orchestrator_2.

    Identical contract to :func:`sea_of_colours.evals.runner.run_scenario`,
    just routed through orchestrator_2's runtime. We don't subclass /
    monkey-patch the v1 runner because it imports the v1 ``run_agent_turn``
    at module level — duplicating the small dispatch loop here is
    cleaner than a runtime patch.
    """
    overrides = config.env_overrides() if config is not None else {}
    with _patched_env(overrides):
        store, session_id = scenario.build()
        if runtime_override == "cortex":
            store = _mirror_fixture_to_backend(store, session_id)
        sess = soc_engine._hydrate_session(store, session_id)
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
            a.evaluate(policy, context=context)
            for a in scenario.assertions
        ]

        watch_url: Optional[str] = None
        if record_replay:
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
