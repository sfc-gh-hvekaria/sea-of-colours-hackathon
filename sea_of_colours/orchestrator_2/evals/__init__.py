"""Eval runner for orchestrator_2.

Thin wrapper around the existing :mod:`sea_of_colours.evals` runtime
that swaps the agent-turn callable to point at
:func:`sea_of_colours.orchestrator_2.runtime.run_agent_turn` instead of
the v1 orchestrator's. Scenarios, builders, and assertions are reused
verbatim from the v1 evals package.
"""
