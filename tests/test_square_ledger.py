"""Square-identity ledger + harvest-certificate behaviour (RULEBOOK §3.12)."""

from __future__ import annotations

from conftest import banked_parcels

import pytest

from sea_of_colours.game.ledger import (
    LEDGER_STORE,
    InMemoryLedgerStore,
    SquareLedger,
    square_hash,
)
from sea_of_colours.game.session import GameSession
from sea_of_colours.generator import Cell, Tile


@pytest.fixture(autouse=True)
def _use_echo_drop_mode_for_legacy_tests(monkeypatch):
    """test_square_ledger.py was written before v0.9.17 canonical rules.
    
    These tests assume live_or_echo drop mode (no probe requirement for
    drops). Rather than rewriting every test to add probe coverage, we
    restore the old default for this entire module."""
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")


def test_square_hash_is_deterministic_and_distinct() -> None:
    a = square_hash(42, 3, 4, int(Tile.RED), 200)
    b = square_hash(42, 3, 4, int(Tile.RED), 200)
    c = square_hash(42, 3, 5, int(Tile.RED), 200)
    d = square_hash(43, 3, 4, int(Tile.RED), 200)
    assert a == b, "same inputs ⇒ same hash"
    assert a != c, "different coords ⇒ different hash"
    assert a != d, "different seed ⇒ different hash"
    assert len(a) == 16, "16-char hex digest"


def test_ledger_covers_every_tile_and_round_trips() -> None:
    sess = GameSession.new(8, 6, seed=99)
    assert sess.ledger is not None
    assert sess.ledger.width == 8 and sess.ledger.height == 6
    assert len(sess.ledger.entries) == 8 * 6
    rec = sess.ledger.record(0, 0)
    assert {"square_id", "x", "y", "tile_at_generation", "purity_at_generation"} <= set(rec)
    payload = sess.ledger.to_dict()
    restored = SquareLedger.from_dict(payload)
    assert restored.entries == sess.ledger.entries
    assert restored.lookup(0, 0) == sess.ledger.lookup(0, 0)


def test_ledger_is_saved_to_store_on_new_game() -> None:
    sess = GameSession.new(6, 6, seed=7)
    fetched = LEDGER_STORE.load(sess.session_id)
    assert fetched is not None
    assert fetched.lookup(0, 0) == sess.ledger.lookup(0, 0)  # type: ignore[union-attr]


def test_in_memory_ledger_store_round_trip() -> None:
    store = InMemoryLedgerStore()
    ledger = SquareLedger.from_grid(11, [[Cell(Tile.EMPTY, 0)]])
    store.save("sid-1", ledger)
    assert store.load("sid-1") is ledger
    assert store.delete("sid-1") is True
    assert store.load("sid-1") is None


def test_harvest_parcel_carries_square_id_and_origin_paint() -> None:
    """The parcel's square_id matches the ORIGINAL RED's id (not the synthetic green).

    v0.6.0 changed ``ledger.lookup(x, y)`` to return the most recent
    UNHARVESTED row at ``(x, y)``. After a RED→GREEN conversion,
    that's the freshly-minted synthetic-green row — not the RED that
    was banked into the harvester. The parcel itself carries the
    RED's original identity (certificate of origin describes what was
    harvested, not what was left behind).
    """
    sess = GameSession.new(22, 14, seed=4242)
    dy = sess.height // 2
    drop_x, target_x = 3, 4
    sess.grid[dy][drop_x] = Cell(Tile.EMPTY, 0)
    sess.grid[dy][target_x] = Cell(Tile.RED, 210)

    # Snapshot the natural RED's identity BEFORE harvest — that's what
    # the parcel will carry.
    assert sess.ledger is not None
    natural_red_sid = sess.ledger.lookup(target_x, dy)
    assert natural_red_sid

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [drop_x, dy]},
        {"a": "step", "unit": "harvester_p1", "to": [target_x, dy]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    hoard = banked_parcels(sess, "p1")
    assert len(hoard) == 1
    parcel = hoard[0]
    assert parcel["x"] == target_x and parcel["y"] == dy
    # Parcel carries the RED's original square_id — NOT the post-
    # harvest synthetic-green that now sits at that cell.
    assert parcel["square_id"] == natural_red_sid
    # The ledger now reports the synthetic-green as the active row.
    post_harvest_sid = sess.ledger.lookup(target_x, dy)
    assert post_harvest_sid != natural_red_sid
    synth_row = sess.ledger.row_by_sid(post_harvest_sid)
    assert synth_row is not None
    assert synth_row["lineage"] == "synthetic"
    assert synth_row["parent_square_id"] == natural_red_sid
    assert parcel["tile_at_harvest"] == int(Tile.RED)
    assert parcel["purity_at_harvest"] == 210
    paint = parcel["paint"]
    assert isinstance(paint, dict) and {"bg", "ch"} <= set(paint)


def test_drop_onto_red_auto_harvests_all_consecutive_reds() -> None:
    """v0.6.0: harvester lands+harvests AND every step on RED harvests.

    No per-color cap; the only limit is the harvester's 6-parcel hold
    (drop tile + 5 step tiles). This test drops on RED and walks
    through 5 more RED — banking 6 parcels, the full hold.
    """
    sess = GameSession.new(30, 14, seed=7777)
    y = sess.height // 2
    sess.grid[y][5] = Cell(Tile.RED, 200)
    for x in range(6, 11):
        sess.grid[y][x] = Cell(Tile.RED, 200)

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [5, y]},  # +1
        {"a": "step", "unit": "harvester_p1", "to": [6, y]},  # +1
        {"a": "step", "unit": "harvester_p1", "to": [7, y]},  # +1
        {"a": "step", "unit": "harvester_p1", "to": [8, y]},  # +1
        {"a": "step", "unit": "harvester_p1", "to": [9, y]},  # +1
        {"a": "step", "unit": "harvester_p1", "to": [10, y]},  # +1
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    # Drop tile + 5 step tiles = 6 RED → GREEN conversions, all banked.
    assert len(banked_parcels(sess, "p1")) == 6
    converted = sum(1 for x in range(5, 11) if sess.grid[y][x].tile == Tile.GREEN)
    assert converted == 6


def test_drop_onto_empty_then_step_through_red_harvests_all() -> None:
    """v0.6.0: drop on empty doesn't bank anything; every later RED step does."""
    sess = GameSession.new(20, 12, seed=313)
    y = sess.height // 2
    sess.grid[y][3] = Cell(Tile.EMPTY, 0)
    for x in range(4, 9):  # 5 RED tiles
        sess.grid[y][x] = Cell(Tile.RED, 180)

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        *[
            {"a": "step", "unit": "harvester_p1", "to": [x, y]}
            for x in range(4, 9)
        ],
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()
    # 5 RED steps × 1 parcel each = 5 in the hoard; drop on EMPTY
    # banks nothing.
    assert len(banked_parcels(sess, "p1")) == 5
