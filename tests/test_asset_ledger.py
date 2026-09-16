"""Lifecycle tests for the per-asset ledger.

The ledger lives at :mod:`sea_of_colours.game.asset_ledger` and is the
Snowflake-bound source of truth for every harvester / orblift / probe.
These tests pin the lifecycle hooks wired through ``GameSession`` so a
future refactor can't accidentally drop a column or stop incrementing a
counter.
"""

from __future__ import annotations

import pytest

from sea_of_colours.game import ASSET_LEDGER_STORE, AssetRecord, GameSession
from sea_of_colours.game.session import Entity
from sea_of_colours.generator import Cell, Tile


@pytest.fixture(autouse=True)
def _use_echo_drop_mode_for_legacy_tests(monkeypatch):
    """test_asset_ledger.py was written before v0.9.17 canonical rules.
    
    These tests assume live_or_echo drop mode (no probe requirement for
    drops). Rather than rewriting every test to add probe coverage, we
    restore the old default for this entire module."""
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")


def _records_for(sess: GameSession, owner: str) -> list[AssetRecord]:
    return [r for r in sess.asset_records.values() if r.owner == owner]


def test_starting_roster_seeds_asset_records_for_both_players() -> None:
    """Every default unit gets a ledger row at session birth."""
    sess = GameSession.new(20, 14, seed=42)
    for owner in ("p1", "p2"):
        ids = {r.asset_id for r in _records_for(sess, owner)}
        assert f"harvester_{owner}" in ids
        assert f"orblift_{owner}" in ids

    # Default units start in orbit → first_deployed_day is null.
    h = sess.asset_records["harvester_p1"]
    assert h.first_deployed_day is None
    assert h.created_on_day == sess.day
    assert h.is_alive()

    # And the global store agrees (Snowflake seam is wired).
    ledger = ASSET_LEDGER_STORE.load(sess.session_id)
    assert ledger is not None
    assert "harvester_p1" in ledger.records
    assert "orblift_p2" in ledger.records


def test_probe_spawn_creates_record_marked_deployed_today() -> None:
    sess = GameSession.new(22, 14, seed=11)
    sess.day = 4
    ok, msg = sess.spawn_probe("p1", 10, 5)
    assert ok, msg
    # The new entity's id is in the message: probe_p1_<n>.
    pid = next(iter(eid for eid in sess.entities if eid.startswith("probe_p1_")))
    rec = sess.asset_records[pid]
    assert rec.asset_type == "probe"
    assert rec.owner == "p1"
    assert rec.created_on_day == 4
    assert rec.first_deployed_day == 4
    assert rec.destroyed_on_day is None
    assert rec.last_seen_x == 10 and rec.last_seen_y == 5


def test_harvester_drop_marks_first_deployed_day_once() -> None:
    sess = GameSession.new(22, 14, seed=22)
    y = sess.height // 2
    sess.grid[y][3] = Cell(Tile.EMPTY, 0)

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    day_when_dropped = sess.day
    sess.maybe_resolve_if_ready()

    rec = sess.asset_records["harvester_p1"]
    assert rec.first_deployed_day == day_when_dropped


def test_red_step_bumps_total_red_harvested_lifetime_counter() -> None:
    sess = GameSession.new(22, 14, seed=33)
    y = sess.height // 2
    sess.grid[y][3] = Cell(Tile.EMPTY, 0)
    sess.grid[y][4] = Cell(Tile.RED, 220)
    sess.grid[y][5] = Cell(Tile.RED, 220)

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        {"a": "step", "unit": "harvester_p1", "to": [4, y]},
        {"a": "step", "unit": "harvester_p1", "to": [5, y]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    rec = sess.asset_records["harvester_p1"]
    assert rec.total_red_harvested == 2, (
        f"expected 2 lifetime conversions, got {rec.total_red_harvested}"
    )


def test_probe_crushed_under_harvester_is_marked_destroyed() -> None:
    sess = GameSession.new(22, 14, seed=44)
    sess.day = 3
    sess.entities["probe_p1_1"] = Entity("probe_p1_1", "probe", "p1", 5, 5)
    sess.asset_records["probe_p1_1"] = AssetRecord(
        asset_id="probe_p1_1",
        asset_type="probe",
        owner="p1",
        session_id=sess.session_id,
        created_on_day=2,
        first_deployed_day=2,
        last_seen_x=5,
        last_seen_y=5,
    )
    moves = [{"a": "drop", "unit": "harvester_p1", "at": [5, 5]}]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    rec = sess.asset_records["probe_p1_1"]
    assert rec.destroyed_on_day == 3
    assert rec.destroyed_by and rec.destroyed_by.startswith("crushed_by_harvester")
    assert not rec.is_alive()


def test_inventory_pack_buckets_assets_by_status() -> None:
    sess = GameSession.new(22, 14, seed=55)
    sess.day = 2
    # Plant a probe that will be crushed and another that stays alive.
    sess.entities["probe_p1_5"] = Entity("probe_p1_5", "probe", "p1", 7, 7)
    sess.asset_records["probe_p1_5"] = AssetRecord(
        asset_id="probe_p1_5",
        asset_type="probe",
        owner="p1",
        session_id=sess.session_id,
        created_on_day=1,
        first_deployed_day=1,
        last_seen_x=7,
        last_seen_y=7,
    )

    inv = sess.inventory_pack("p1")
    buckets = inv["assets_by_status"]
    in_orbit_ids = {row["asset_id"] for row in buckets["in_orbit"]}
    on_surface_ids = {row["asset_id"] for row in buckets["on_surface"]}
    assert in_orbit_ids == {"harvester_p1", "orblift_p1"}
    assert "probe_p1_5" in on_surface_ids
    assert buckets["destroyed"] == []

    # Crush the probe → it migrates to ``destroyed`` next pack.
    sess._note_asset_destroyed("probe_p1_5", reason="testing")
    del sess.entities["probe_p1_5"]
    inv = sess.inventory_pack("p1")
    destroyed_ids = {row["asset_id"] for row in inv["assets_by_status"]["destroyed"]}
    assert "probe_p1_5" in destroyed_ids


def test_asset_records_round_trip_through_to_dict_and_from_dict() -> None:
    sess = GameSession.new(18, 12, seed=66)
    sess.day = 5
    sess.spawn_probe("p1", 4, 4)
    rec = next(
        r for r in sess.asset_records.values() if r.asset_type == "probe"
    )
    rec.total_red_harvested = 0
    rec.total_days_on_surface = 7

    payload = sess.to_dict()
    restored = GameSession.from_dict(payload)
    assert restored.asset_records[rec.asset_id].total_days_on_surface == 7
    assert restored.asset_records[rec.asset_id].first_deployed_day == 5


def test_advance_day_counters_increments_only_surface_assets() -> None:
    sess = GameSession.new(20, 14, seed=77)
    sess.day = 9
    sess.entities["probe_p1_2"] = Entity("probe_p1_2", "probe", "p1", 3, 3)
    sess.asset_records["probe_p1_2"] = AssetRecord(
        asset_id="probe_p1_2",
        asset_type="probe",
        owner="p1",
        session_id=sess.session_id,
        created_on_day=8,
        first_deployed_day=8,
    )
    before_probe = sess.asset_records["probe_p1_2"].total_days_on_surface
    before_orblift = sess.asset_records["orblift_p1"].total_days_on_surface
    sess.advance_asset_day_counters()
    assert sess.asset_records["probe_p1_2"].total_days_on_surface == before_probe + 1
    assert sess.asset_records["orblift_p1"].total_days_on_surface == before_orblift
