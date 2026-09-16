"""Agent evaluation harness for Sea of Colours.

Builds deterministic, pre-seeded game states ("scenarios") that exercise
specific reasoning capabilities of the active agent (heuristic or
Cortex). Each scenario:

* Constructs an :class:`~sea_of_colours.game.session.GameSession` at a
  chosen day with hand-placed entities, visibility, scores, and an
  optional opponent footprint.
* Persists it through the standard
  :class:`~sea_of_colours.snowpark.store.SocStore` so the live agent
  runtime (:func:`sea_of_colours.agent.runtime.run_agent_turn`) sees
  the same fixture an in-game turn would.
* Runs one (or N) agent turns against the fixture, captures the
  submitted policy, and evaluates it against a small set of
  declarative :class:`~sea_of_colours.evals.assertions.Assertion`
  objects (``MustTouch``, ``MustAvoid``, ``MinExpectedValue``,
  ``ProbeCount``, etc.).

The intent is to give us a fast, reproducible feedback loop on
prompt / spec changes. Run the suite, see exactly which capabilities
regressed, A/B prompts without spinning up a full season.

Public surface:

* :class:`~sea_of_colours.evals.builder.WorldBuilder` — fluent DSL
  for constructing fixtures.
* :func:`~sea_of_colours.evals.runner.run_scenario` — execute one
  scenario, return :class:`~sea_of_colours.evals.runner.ScenarioResult`.
* :data:`~sea_of_colours.evals.scenarios.SCENARIOS` — the registry
  of built-in scenarios.
"""

from sea_of_colours.evals.builder import WorldBuilder
from sea_of_colours.evals.assertions import (
    Assertion,
    AssertionResult,
    EndsWithPickup,
    HarvesterChainHits,
    MinExpectedValue,
    MustAvoid,
    MustTouch,
    NoHarvesterDeployment,
    NoSyntheticGreenSteps,
    ProbeCount,
    ProbesInDistinctQuadrants,
    ProbesInRegion,
)
from sea_of_colours.evals.configs import CONFIGS, EvalConfig, get_config
from sea_of_colours.evals.runner import ScenarioResult, run_scenario
from sea_of_colours.evals.scenarios import SCENARIOS, Scenario, get_scenario

__all__ = [
    "Assertion",
    "AssertionResult",
    "CONFIGS",
    "EndsWithPickup",
    "EvalConfig",
    "HarvesterChainHits",
    "MinExpectedValue",
    "MustAvoid",
    "MustTouch",
    "NoHarvesterDeployment",
    "NoSyntheticGreenSteps",
    "ProbeCount",
    "ProbesInDistinctQuadrants",
    "ProbesInRegion",
    "SCENARIOS",
    "Scenario",
    "ScenarioResult",
    "WorldBuilder",
    "get_config",
    "get_scenario",
    "run_scenario",
]
