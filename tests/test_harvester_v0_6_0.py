"""v0.6.0 harvester rule suite — vision plus, all-colour harvest, lineage, vault tier.

Pins the four big rule changes that landed in v0.6.0 so a future
refactor cannot silently regress them:

1. Harvester vision is a PLUS — Euclidean radius 1, 5-tile disk
   (self + 4 cardinals). Diagonals are FOG.
2. Harvest covers every colour — RED→GREEN, GREEN→EMPTY, BLUE→EMPTY.
   No per-color cap; the natural limit is the 6-parcel hold.
3. RED→GREEN mints a fresh synthetic-green identity at (x, y) with
   full provenance; the parcel carries the RED's original square_id
   while the ledger now reports the synthetic as the active row at
   that cell.
4. Vault tier-priority cascade (§3.14) — GREEN top, RED mid, BLUE
   bottom. Six scenarios pinned below.
"""

from __future__ import annotations

from conftest import banked_parcels

import pytest

from sea_of_colours.game.session import (
    HARVESTER_LOS_RADIUS,
    HOARD_CAPACITY,
    GameSession,
    _harvester_vision_disk,
)
from sea_of_colours.generator import Cell, Tile


@pytest.fixture(autouse=True)
def _use_echo_drop_mode_for_legacy_tests(monkeypatch):
    """test_harvester_v0_6_0.py was written before v0.9.17 canonical rules.
    
    These tests assume live_or_echo drop mode (no probe requirement for
    drops). Rather than rewriting every test to add probe coverage, we
    restore the old default for this entire module."""
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")


# ── 1. Harvester vision = plus ────────────────────────────────────────


def test_harvester_los_radius_is_one() -> None:
    """The constant is documented as 1 in the rulebook (RULEBOOK §3.11)."""
    assert HARVESTER_LOS_RADIUS == 1


def test_harvester_vision_disk_is_plus_shape() -> None:
    """The Euclidean disk at radius 1 resolves to a 5-tile plus."""
    cells = _harvester_vision_disk(5, 5, w=20, h=20)
    # The plus: self + 4 cardinals.
    expected = {(5, 5), (5, 4), (5, 6), (4, 5), (6, 5)}
    assert cells == expected, (
        f"plus mismatch — got {sorted(cells)}, want {sorted(expected)}"
    )
    # Diagonals must NOT be in the disk (d² = 2 > 1).
    for diag in [(4, 4), (4, 6), (6, 4), (6, 6)]:
        assert diag not in cells, f"diagonal {diag} leaked into plus disk"


def test_harvester_plus_clips_at_grid_edge() -> None:
    """Top-left corner harvester sees only the legal portion of the plus."""
    cells = _harvester_vision_disk(0, 0, w=10, h=10)
    assert cells == {(0, 0), (1, 0), (0, 1)}


# ── 2. Harvest covers every colour ────────────────────────────────────


def test_drop_on_red_converts_to_green_and_banks_parcel() -> None:
    sess = GameSession.new(20, 14, seed=4242)
    y = sess.height // 2
    sess.grid[y][3] = Cell(Tile.RED, 200)

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    # Tile became GREEN(255).
    assert sess.grid[y][3].tile == Tile.GREEN
    assert sess.grid[y][3].purity == 255
    # Hoard has the RED parcel.
    assert len(banked_parcels(sess, "p1")) == 1
    parcel = banked_parcels(sess, "p1")[0]
    assert parcel["tile_at_harvest"] == int(Tile.RED)
    assert parcel["purity_at_harvest"] == 200


def test_drop_on_green_converts_to_empty_and_banks_parcel() -> None:
    """GREEN harvest leaves bare ground — no new ledger row."""
    sess = GameSession.new(20, 14, seed=4242)
    y = sess.height // 2
    sess.grid[y][3] = Cell(Tile.GREEN, 255)

    # Capture the natural GREEN's square_id BEFORE harvest.
    assert sess.ledger is not None
    green_sid_before = sess.ledger.lookup(3, y)
    assert green_sid_before

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    # Tile became EMPTY.
    assert sess.grid[y][3].tile == Tile.EMPTY
    assert sess.grid[y][3].purity == 0
    # Hoard has the GREEN parcel carrying the original square_id.
    assert len(banked_parcels(sess, "p1")) == 1
    parcel = banked_parcels(sess, "p1")[0]
    assert parcel["tile_at_harvest"] == int(Tile.GREEN)
    assert parcel["square_id"] == green_sid_before
    # The natural GREEN row was stamped harvested — no new row.
    row = sess.ledger.row_by_sid(green_sid_before)
    assert row is not None
    assert row["harvested_on_day"] is not None
    assert row["harvested_by"] == "harvester_p1"
    # No synthetic-green was minted (only RED→GREEN mints one).
    assert sess.ledger.synthetic_rows == {}


def test_drop_on_blue_converts_to_empty_and_banks_parcel() -> None:
    """BLUE harvest is symmetric to GREEN harvest."""
    sess = GameSession.new(20, 14, seed=4242)
    y = sess.height // 2
    sess.grid[y][3] = Cell(Tile.BLUE, 180)

    assert sess.ledger is not None
    blue_sid_before = sess.ledger.lookup(3, y)
    assert blue_sid_before

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    assert sess.grid[y][3].tile == Tile.EMPTY
    parcel = sess.hoard_squares["p1"][0]
    assert parcel["tile_at_harvest"] == int(Tile.BLUE)
    assert parcel["square_id"] == blue_sid_before
    assert sess.ledger.synthetic_rows == {}


def test_no_per_color_harvest_cap() -> None:
    """v0.6.0 dropped the 5-RED-per-night cap; the only limit is 6-parcel hold."""
    sess = GameSession.new(40, 14, seed=99)
    y = sess.height // 2
    # 6 contiguous RED tiles (drop + 5 steps fills the hold).
    sess.grid[y][3] = Cell(Tile.EMPTY, 0)  # drop on empty so the chain is 5 RED steps
    for x in range(4, 10):
        sess.grid[y][x] = Cell(Tile.RED, 200)

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        *[
            {"a": "step", "unit": "harvester_p1", "to": [x, y]}
            for x in range(4, 10)
        ],
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    # All 6 RED steps converted; nothing left over the historical cap.
    converted = sum(1 for x in range(4, 10) if sess.grid[y][x].tile == Tile.GREEN)
    assert converted == 6
    assert len(banked_parcels(sess, "p1")) == 6


def test_outing_hold_cap_cancels_seventh_parcel() -> None:
    """RULEBOOK §3: a single outing banks at most 6 parcels (hold capacity).

    Dropping on RED (auto-harvest = parcel 1) then walking 6 RED steps
    would be 7 parcels — the engine must cancel the 6th step (hold full at
    6) so only 6 bank. Regression for the day-3 audit where a harvester
    banked 7 in one outing because the cap was enforced nowhere.
    """
    sess = GameSession.new(40, 14, seed=99)
    y = sess.height // 2
    # Drop cell is RED (banks parcel 1), then 6 further RED tiles.
    for x in range(3, 10):  # x = 3..9 → 7 contiguous RED
        sess.grid[y][x] = Cell(Tile.RED, 200)

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        *[
            {"a": "step", "unit": "harvester_p1", "to": [x, y]}
            for x in range(4, 10)  # 6 steps requested
        ],
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    # Hold caps at 6 — the 7th parcel (last step) is cancelled.
    assert len(banked_parcels(sess, "p1")) == 6
    # The final RED tile was never reached → still RED (not converted).
    assert sess.grid[y][9].tile == Tile.RED


# ── 3. Synthetic-green lineage ─────────────────────────────────────────


def test_red_harvest_mints_synthetic_green_with_provenance() -> None:
    """RED→GREEN conversion mints a synthetic-green row with full provenance."""
    sess = GameSession.new(20, 14, seed=4242)
    y = sess.height // 2
    sess.grid[y][3] = Cell(Tile.RED, 215)

    assert sess.ledger is not None
    natural_red_sid = sess.ledger.lookup(3, y)
    assert natural_red_sid

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    # Active row at (3, y) is now the synthetic-green; the natural RED
    # has been stamped harvested.
    synth_sid = sess.ledger.lookup(3, y)
    assert synth_sid != natural_red_sid
    synth_row = sess.ledger.row_by_sid(synth_sid)
    assert synth_row is not None
    assert synth_row["lineage"] == "synthetic"
    assert synth_row["parent_square_id"] == natural_red_sid
    assert synth_row["generated_by_owner"] == "p1"
    assert synth_row["generated_by_harvester_id"] == "harvester_p1"
    assert synth_row["generated_on_day"] is not None
    assert synth_row["tile_at_generation"] == int(Tile.GREEN)
    assert synth_row["purity_at_generation"] == 255
    # The closed-out RED row is stamped harvested.
    red_row = sess.ledger.row_by_sid(natural_red_sid)
    assert red_row is not None
    assert red_row["harvested_on_day"] is not None
    assert red_row["harvested_by"] == "harvester_p1"
    # The parcel carries the RED's id (certificate of origin).
    parcel = banked_parcels(sess, "p1")[0]
    assert parcel["square_id"] == natural_red_sid
    assert parcel.get("lineage") == "natural"
    assert parcel.get("minted_synthetic_id") == synth_sid


def test_natural_world_build_rows_are_lineage_natural() -> None:
    """Every cell minted at world-build carries lineage='natural'."""
    sess = GameSession.new(12, 8, seed=51)
    assert sess.ledger is not None
    for row in sess.ledger.entries.values():
        assert row["lineage"] == "natural"
        assert row["parent_square_id"] is None
        assert row["harvested_on_day"] is None


# ── 4. Vault tier-priority replacement ─────────────────────────────────


def _fill_hoard(sess: GameSession, owner: str, *, tile: Tile, purity: int) -> None:
    """Helper: stuff the hoard with HOARD_CAPACITY parcels of a single colour."""
    sess.hoard_squares[owner] = [
        {
            "square_id": f"fake-{tile.name}-{i:03d}",
            "site_id": f"fake-{tile.name}-{i:03d}",
            "x": i, "y": 0,
            "tile_at_harvest": int(tile),
            "purity_at_harvest": purity + (i % 3),  # spread so min/max differ
            "harvested_on_planning_day": 1,
            "harvester_id": "harvester_p1",
            "paint": {"bg": "rgb(0,0,0)", "ch": "  "},
        }
        for i in range(HOARD_CAPACITY)
    ]


def _make_incoming_parcel(tile: Tile, purity: int) -> dict:
    return {
        "square_id": f"incoming-{tile.name}-{purity}",
        "site_id": f"incoming-{tile.name}-{purity}",
        "x": 0, "y": 0,
        "tile_at_harvest": int(tile),
        "purity_at_harvest": purity,
        "harvested_on_planning_day": 2,
        "harvester_id": "harvester_p1",
        "paint": {"bg": "rgb(0,0,0)", "ch": "  "},
    }


def test_vault_green_displaces_lowest_blue_when_full() -> None:
    sess = GameSession.new(20, 14, seed=1)
    _fill_hoard(sess, "p1", tile=Tile.BLUE, purity=10)
    incoming = _make_incoming_parcel(Tile.GREEN, 255)
    ops: list = []
    sess.deposit_haul_to_hoard("p1", "harvester_p1", [incoming], ops)
    # One BLUE was displaced and the GREEN took its slot.
    colours = [p["tile_at_harvest"] for p in sess.hoard_squares["p1"]]
    assert colours.count(int(Tile.GREEN)) == 1
    assert colours.count(int(Tile.BLUE)) == HOARD_CAPACITY - 1
    assert any("displaced" in line for line in ops)


def test_vault_green_displaces_lowest_red_when_no_blue() -> None:
    sess = GameSession.new(20, 14, seed=1)
    _fill_hoard(sess, "p1", tile=Tile.RED, purity=200)
    incoming = _make_incoming_parcel(Tile.GREEN, 255)
    ops: list = []
    sess.deposit_haul_to_hoard("p1", "harvester_p1", [incoming], ops)
    colours = [p["tile_at_harvest"] for p in sess.hoard_squares["p1"]]
    assert colours.count(int(Tile.GREEN)) == 1
    assert colours.count(int(Tile.RED)) == HOARD_CAPACITY - 1


def test_vault_all_green_jettisons_incoming_green() -> None:
    sess = GameSession.new(20, 14, seed=1)
    _fill_hoard(sess, "p1", tile=Tile.GREEN, purity=255)
    incoming = _make_incoming_parcel(Tile.GREEN, 255)
    ops: list = []
    sess.deposit_haul_to_hoard("p1", "harvester_p1", [incoming], ops)
    # The vault is unchanged.
    assert all(
        p["tile_at_harvest"] == int(Tile.GREEN)
        for p in sess.hoard_squares["p1"]
    )
    assert len(sess.hoard_squares["p1"]) == HOARD_CAPACITY
    assert any("jettisoned" in line for line in ops)


def test_vault_red_displaces_red_only_if_higher_purity() -> None:
    sess = GameSession.new(20, 14, seed=1)
    # Vault is all RED purity 200..202. Incoming RED 100 must NOT
    # displace; incoming RED 250 MUST.
    _fill_hoard(sess, "p1", tile=Tile.RED, purity=200)
    ops: list = []
    sess.deposit_haul_to_hoard(
        "p1", "harvester_p1", [_make_incoming_parcel(Tile.RED, 100)], ops,
    )
    assert all(
        int(p.get("purity_at_harvest", 0)) >= 200
        for p in sess.hoard_squares["p1"]
    ), "incoming RED 100 should have been jettisoned"
    assert any("jettisoned" in line.lower() for line in ops)

    ops2: list = []
    sess.deposit_haul_to_hoard(
        "p1", "harvester_p1", [_make_incoming_parcel(Tile.RED, 250)], ops2,
    )
    purities = sorted(
        int(p.get("purity_at_harvest", 0)) for p in sess.hoard_squares["p1"]
    )
    assert 250 in purities, "incoming RED 250 should have displaced the lowest RED"
    assert any("displaced" in line.lower() for line in ops2)


def test_vault_blue_never_displaces_red_or_green() -> None:
    sess = GameSession.new(20, 14, seed=1)
    _fill_hoard(sess, "p1", tile=Tile.RED, purity=200)
    ops: list = []
    sess.deposit_haul_to_hoard(
        "p1", "harvester_p1", [_make_incoming_parcel(Tile.BLUE, 250)], ops,
    )
    # Vault stayed all RED.
    assert all(
        p["tile_at_harvest"] == int(Tile.RED)
        for p in sess.hoard_squares["p1"]
    )
    assert any("jettisoned" in line.lower() for line in ops)


def test_vault_blue_displaces_blue_only_if_higher_purity() -> None:
    sess = GameSession.new(20, 14, seed=1)
    _fill_hoard(sess, "p1", tile=Tile.BLUE, purity=100)
    ops: list = []
    sess.deposit_haul_to_hoard(
        "p1", "harvester_p1", [_make_incoming_parcel(Tile.BLUE, 200)], ops,
    )
    purities = sorted(
        int(p.get("purity_at_harvest", 0)) for p in sess.hoard_squares["p1"]
    )
    assert 200 in purities
    assert any("displaced" in line.lower() for line in ops)
