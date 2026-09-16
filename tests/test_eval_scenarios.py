"""Smoke + regression tests for the eval-scenario harness.

This file's job is NOT to assert every scenario passes — most are
designed to surface gaps in the heuristic (and, later, in Cortex). The
suite would be permanently red if we naively gated CI on every
scenario.

What it DOES enforce:

1. **Fixtures build cleanly.** Every scenario can construct its
   :class:`~sea_of_colours.snowpark.store.InMemorySocStore` and
   persist the resulting :class:`~sea_of_colours.game.session.GameSession`
   without raising. This catches schema drift between the builder and
   the engine.
2. **The runner returns a well-formed result.** ``policy`` is a list,
   ``results`` contains one entry per declared assertion, every
   assertion result carries the expected/observed fields. This locks
   in the contract the markdown report relies on.
3. **A small "must-always-pass" subset stays green** against the
   heuristic so any regression in the heuristic itself trips a
   failure. The subset is the scenarios the current heuristic does
   handle competently — picked by inspection, NOT by tagging the
   easy ones.

To run only the eval tests::

    python3 -m pytest tests/test_eval_scenarios.py -v

Full-suite agent quality is tracked separately by
``scripts/run_evals.py``.
"""

from __future__ import annotations

import os
import pytest

# Force the in-memory backend BEFORE the engine module is imported,
# matching the rest of the test suite.
os.environ.setdefault("SOC_BACKEND", "memory")

from sea_of_colours.evals import SCENARIOS, run_scenario  # noqa: E402


@pytest.fixture(autouse=True)
def _use_echo_drop_mode_for_legacy_tests(monkeypatch):
    """test_eval_scenarios.py was written before v0.9.17 canonical rules.
    
    These tests assume live_or_echo drop mode (no probe requirement for
    drops). Rather than rewriting every test to add probe coverage, we
    restore the old default for this entire module."""
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")


# Scenarios the heuristic should ALWAYS pass. If the heuristic
# regresses (e.g. someone breaks its RED-targeting logic) one of these
# will go red. Keep this list small and only add scenarios where the
# heuristic genuinely demonstrates the target capability.
#
# The three Phase 1 spatial scenarios (multi_hop_seam, enemy_intercept,
# two_seams_choose_one) live here because the closest-first / value-DESC
# heuristic already handles them — we want any future heuristic
# regression to surface clearly before we evaluate Cortex.
HEURISTIC_GREEN_SCENARIOS = {
    "tier_choice",
    "enemy_trail_in_seam",
    "vault_pressure",
    "probe_collision_risk",
    "multi_hop_seam",
    "enemy_intercept",
    "two_seams_choose_one",
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario_runs_cleanly(scenario):
    """Every scenario must build + execute + return a well-formed result."""
    result = run_scenario(scenario, runtime_override="heuristic")

    assert result.scenario_name == scenario.name, \
        "ScenarioResult.scenario_name should match scenario.name"
    assert isinstance(result.policy, list), \
        "policy must be a list of move dicts"
    assert len(result.results) == len(scenario.assertions), \
        f"got {len(result.results)} assertion results for "\
        f"{len(scenario.assertions)} declared assertions"

    for ar in result.results:
        assert isinstance(ar.name, str)
        assert isinstance(ar.passed, bool)
        assert isinstance(ar.detail, str)


@pytest.mark.parametrize(
    "scenario_name",
    sorted(HEURISTIC_GREEN_SCENARIOS),
)
def test_heuristic_passes_known_scenarios(scenario_name):
    """The heuristic must keep passing the scenarios it CAN handle.

    Regression sentinel: if a refactor breaks the heuristic's RED-
    targeting or pickup-on-pressure behaviour, the failing scenario
    name pinpoints the gap.
    """
    from sea_of_colours.evals import get_scenario

    scenario = get_scenario(scenario_name)
    result = run_scenario(scenario, runtime_override="heuristic")

    if not result.passed:
        failing = [r for r in result.results if not r.passed]
        fail_lines = "\n".join(
            f"  - {r.name}: {r.detail}" for r in failing
        )
        pytest.fail(
            f"heuristic regressed on '{scenario_name}':\n{fail_lines}\n"
            f"policy was: {result.policy}\n"
            f"rationale: {result.rationale}"
        )


def test_all_scenarios_are_registered():
    """Every scenario factory is reachable via the SCENARIOS registry."""
    names = [s.name for s in SCENARIOS]
    assert len(names) == len(set(names)), \
        "duplicate scenario names found in SCENARIOS registry"
    assert "probes_only" in names
    assert "collision_avoidance" in names
    assert "tier_choice" in names
