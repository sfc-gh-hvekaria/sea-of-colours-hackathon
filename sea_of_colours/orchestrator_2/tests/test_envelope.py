"""Tests for the universal STATE envelope — fairness contract.

These tests pin the most important invariant of orchestrator_2: every
agent gets the same envelope. If a developer accidentally adds
agent-aware code to ``envelope.build_universal_envelope``, these tests
catch it.
"""

from __future__ import annotations

import json

import pytest

from sea_of_colours.orchestrator_2.envelope import build_universal_envelope


def _view(*, day=2, width=14, height=9):
    grid = [[None for _ in range(width)] for _ in range(height)]
    return {
        "agent_view": {
            "meta": {
                "session_id": "T",
                "season": "S",
                "player": "p1",
                "day": day,
                "policy_actions_left": 21,
                "policy_actions_max": 21,
                "rules": {"probe_radius": 4},
            },
            "hud": {
                "score": 0,
                "scores": {"p1": 0, "p2": 0},
                "season_day_cap": 7,
                "hoard": {"used": 0, "max": 15, "free": 15,
                          "by_tier": {"GREEN": {}, "RED": {}, "BLUE": {}},
                          "warning": None},
                "shipped": {"used": 0, "max": None,
                            "by_tier": {"GREEN": {}, "RED": {}, "BLUE": {}}},
            },
            "last_night": {"day_ended": 1, "my_orders": [],
                           "my_assets_destroyed": [], "my_parcels_banked": []},
            "competitor_intel": {"new_this_day": [], "persistent_echoes": []},
            "world": {"width": width, "height": height, "grid": grid},
            "navigation": {"best_red_visible": [], "best_red_echo": [],
                           "fog_clusters": []},
            "my_assets": [],
        }
    }


def test_envelope_signature_takes_no_agent_name():
    """If someone adds an agent_name param, this fails immediately."""
    import inspect
    sig = inspect.signature(build_universal_envelope)
    params = list(sig.parameters.keys())
    assert params == ["session_id", "view"], (
        f"build_universal_envelope must be agent-agnostic; got params={params}"
    )


def test_envelope_has_all_universal_keys():
    body = build_universal_envelope("SESS", _view())
    for key in ("meta", "hud", "last_night", "competitor_intel",
                "world", "navigation", "my_assets"):
        assert f'"{key}":' in body, f"missing universal key {key!r}"


def test_envelope_has_no_agent_specific_keys():
    """No candidates / threat / memory_summary in the universal payload."""
    body = build_universal_envelope("SESS", _view())
    for forbidden in ("candidates", "threat", "memory_summary"):
        assert f'"{forbidden}":' not in body, (
            f"orchestrator leaked agent-specific key {forbidden!r}"
        )


def test_envelope_is_deterministic_for_same_input():
    """Two calls with the same view produce byte-identical output."""
    v = _view()
    a = build_universal_envelope("SESS", v)
    b = build_universal_envelope("SESS", v)
    assert a == b
