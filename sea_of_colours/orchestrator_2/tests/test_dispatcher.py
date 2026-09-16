"""Tests for the dispatcher — pin the 4-way switch behaviour.

We mock each backend (cortex invoker, harness import, heuristic) so
these run in <50ms with no Snowflake / Cortex dependency.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from sea_of_colours.orchestrator_2 import dispatcher
from sea_of_colours.orchestrator_2.binding_registry import AgentBinding
from sea_of_colours.orchestrator_2.dispatcher import (
    DispatchResult,
    dispatch_turn,
)


def _basic_view():
    return {
        "agent_view": {
            "meta": {"session_id": "T", "player": "p1", "day": 1,
                     "policy_actions_left": 21, "policy_actions_max": 21,
                     "rules": {}},
            "hud": {"score": 0, "season_day_cap": 7,
                    "hoard": {"used": 0, "max": 15, "free": 15,
                              "by_tier": {"GREEN": {}, "RED": {}, "BLUE": {}},
                              "warning": None},
                    "shipped": {"used": 0, "max": None,
                                "by_tier": {"GREEN": {}, "RED": {}, "BLUE": {}}}},
            "last_night": {}, "competitor_intel": {"new_this_day": [],
                                                    "persistent_echoes": []},
            "world": {"width": 14, "height": 9,
                      "grid": [[None]*14 for _ in range(9)]},
            "navigation": {}, "my_assets": [],
        }
    }


# ── cortex_agent kind ────────────────────────────────────────────────
def test_dispatch_cortex_agent_invokes_via_invoker():
    binding = AgentBinding(kind="cortex_agent",
                            locator="SOC_FAKE_AGENT",
                            agent_label="FAKE")
    with patch.object(dispatcher, "_dispatch_cortex_agent") as m:
        m.return_value = DispatchResult(
            ok=True, elapsed_ms=1234, submitted_policy=True,
            response="ok", tool_calls=[{"name": "soc_submit_policy"}],
        )
        result = dispatch_turn(
            store=None, session_id="S", player="p1",
            view=_basic_view(), binding=binding,
        )
    assert result.ok is True
    assert result.submitted_policy is True
    assert result.elapsed_ms == 1234


# ── harness_in_process kind ──────────────────────────────────────────
def test_dispatch_harness_in_process_imports_and_calls():
    """Stand up a fake harness module on sys.modules and dispatch to it."""
    mod_name = "_orch2_test_fake_harness"
    fake = types.ModuleType(mod_name)

    def fake_run(*, store, session_id, player, view):
        return {
            "ok": True, "elapsed_ms": 42, "submitted_policy": True,
            "tool_calls": [{"name": "soc_submit_policy"}],
            "response": "fake harness ran",
            "rationale": "fake",
            "extras": {"fake_marker": True},
        }
    fake.run = fake_run
    sys.modules[mod_name] = fake
    try:
        binding = AgentBinding(
            kind="harness_in_process",
            locator=f"{mod_name}:run",
            agent_label="FAKE_HARNESS",
        )
        result = dispatch_turn(
            store=None, session_id="S", player="p1",
            view=_basic_view(), binding=binding,
        )
        assert result.ok is True
        assert result.submitted_policy is True
        assert result.rationale == "fake"
        assert result.extras["fake_marker"] is True
    finally:
        sys.modules.pop(mod_name, None)


def test_dispatch_harness_in_process_bad_locator():
    binding = AgentBinding(kind="harness_in_process",
                            locator="invalid_no_colon",
                            agent_label="BAD")
    result = dispatch_turn(
        store=None, session_id="S", player="p1",
        view=_basic_view(), binding=binding,
    )
    assert result.ok is False
    assert "harness import failed" in (result.error or "")


# ── harness_proc / harness_spcs are stubs ────────────────────────────
def test_dispatch_harness_proc_raises_notimplemented():
    binding = AgentBinding(kind="harness_proc",
                            locator="DB.SCHEMA.SOC_FOO_HARNESS_RUN")
    with pytest.raises(NotImplementedError):
        dispatch_turn(
            store=None, session_id="S", player="p1",
            view=_basic_view(), binding=binding,
        )


def test_dispatch_harness_spcs_raises_notimplemented():
    binding = AgentBinding(kind="harness_spcs",
                            locator="https://example.com/run")
    with pytest.raises(NotImplementedError):
        dispatch_turn(
            store=None, session_id="S", player="p1",
            view=_basic_view(), binding=binding,
        )


# ── heuristic kind ───────────────────────────────────────────────────
def test_dispatch_heuristic_calls_legacy_plan_moves():
    binding = AgentBinding(kind="heuristic", locator="RED_HARVEST")
    with patch("sea_of_colours.agent.heuristic_agent.plan_moves") as mock_plan, \
         patch("sea_of_colours.snowpark.engine.submit_policy") as mock_submit:
        mock_plan.return_value = ([{"a": "probe", "at": [5, 5]}], "test rationale")
        result = dispatch_turn(
            store=MagicMock(), session_id="S", player="p1",
            view=_basic_view(), binding=binding,
        )
    assert result.ok is True
    assert result.submitted_policy is True
    assert result.rationale == "test rationale"
    mock_submit.assert_called_once()


# ── unknown kind ─────────────────────────────────────────────────────
def test_dispatch_unknown_kind_returns_error():
    binding = AgentBinding(kind="future_alien_kind", locator="x")
    result = dispatch_turn(
        store=None, session_id="S", player="p1",
        view=_basic_view(), binding=binding,
    )
    assert result.ok is False
    assert "unknown binding kind" in (result.error or "")
