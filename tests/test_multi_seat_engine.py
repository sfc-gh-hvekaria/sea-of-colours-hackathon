"""v0.9.6 — N-seat engine smoke tests.

These tests pin the contract of the new per-session ``players``
field on :class:`GameSession`:

* up to 4 seats spawn with their own harvesters / orblift / seat
  state dictionaries,
* iterating ``sess.players`` covers everyone (no orphan p2 entries
  when the seat list is e.g. ``("p1", "p3")``),
* the engine view and the observer payload both surface the
  new ``players`` / ``agents`` / ``visibility_mode`` fields, and
* legacy 2-seat snapshots survive the round-trip through
  ``to_dict`` / ``from_dict``.
"""

from __future__ import annotations

import json

from sea_of_colours.game.session import GameSession
from sea_of_colours.snowpark import engine as soc_engine
from sea_of_colours.snowpark.store import InMemorySocStore


def _fresh_store() -> InMemorySocStore:
    return InMemorySocStore()


def test_session_new_accepts_three_seats() -> None:
    store = _fresh_store()
    res = soc_engine.init_session(
        store,
        seed=42,
        width=30,
        height=20,
        players=("p1", "p2", "p3"),
        agents={"p1": "human", "p2": "red_harvest", "p3": "red_harvest"},
        visibility_mode="hidden",
    )
    assert res["players"] == ["p1", "p2", "p3"]
    assert res["agents"]["p3"] == "red_harvest"
    assert res["visibility_mode"] == "hidden"


def test_view_carries_seat_list_and_scores_for_all_seats() -> None:
    store = _fresh_store()
    res = soc_engine.init_session(
        store, seed=7, width=24, height=16,
        players=("p1", "p2", "p3", "p4"),
        agents={"p1": "human", "p2": "red_harvest",
                "p3": "red_harvest", "p4": "red_harvest"},
    )
    sid = res["session_id"]
    view = soc_engine.get_view(store, sid, "p1")
    assert view["players"] == ["p1", "p2", "p3", "p4"]
    # Scores must include every seat in the session.
    assert set(view["scores"].keys()) == {"p1", "p2", "p3", "p4"}


def test_clamped_to_four_seats() -> None:
    """More than 4 seats is clamped — the engine refuses to spawn 5."""
    sess = GameSession.new(
        24, 16, 1,
        players=("p1", "p2", "p3", "p4", "p5"),
    )
    assert len(sess.players) == 4
    assert "p5" not in sess.players


def test_observer_packs_per_seat_units_and_inventories() -> None:
    store = _fresh_store()
    res = soc_engine.init_session(
        store, seed=11, width=24, height=16,
        players=("p1", "p2", "p3"),
    )
    sid = res["session_id"]
    obs = soc_engine.get_observer(store, sid)
    assert obs["players"] == ["p1", "p2", "p3"]
    assert "p3" in obs["units_by_seat"]
    assert "p3" in obs["inventory_by_seat"]


def test_round_trip_serialisation_preserves_seat_list() -> None:
    sess = GameSession.new(
        20, 14, 99,
        players=("p1", "p2", "p3"),
        agents={"p1": "human", "p2": "red_harvest", "p3": "red_harvest"},
        visibility_mode="open",
    )
    raw = sess.to_dict()
    raw_json = json.loads(json.dumps(raw))
    restored = GameSession.from_dict(raw_json)
    assert restored.players == ("p1", "p2", "p3")
    assert restored.agents["p3"] == "red_harvest"
    assert restored.visibility_mode == "open"
    # Per-seat dicts must be populated for every seat — a missing
    # entry would crash policy submission with a KeyError.
    for seat in restored.players:
        assert seat in restored.probe_seq
        assert seat in restored.credits
        assert seat in restored.weapon_stock


def test_legacy_two_seat_snapshot_loads() -> None:
    """A pre-v0.9.6 snapshot (no ``players`` field) must hydrate as
    the legacy two-seat shape — required for back-compat with games
    persisted to Snowflake before this refactor landed."""
    sess = GameSession.new(20, 14, 7)
    raw = sess.to_dict()
    # Simulate a legacy snapshot by dropping the new fields.
    legacy = dict(raw)
    legacy.pop("players", None)
    legacy.pop("agents", None)
    legacy.pop("visibility_mode", None)
    restored = GameSession.from_dict(legacy)
    assert restored.players == ("p1", "p2")
    assert restored.agents == {"p1": "human", "p2": "human"}
    assert restored.visibility_mode == "hidden"
