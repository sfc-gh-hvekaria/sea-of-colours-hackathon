"""The map payload states what a square IS, not just how to paint it.

v1.22 — ``player_dense_view`` cells carry ``tile`` / ``purity`` / ``tier``.
Before this the only terrain signal was the render colour, so the UI
reverse-engineered the tier from the dither glyph: that recovers the band
("51–150") but never the number, while agents have always read exact purity
off ``agent_view``. These pin the new fields, the tier cutoffs they must
agree with (RULEBOOK §2.2), and — most importantly — that the extra detail
never crosses the fog line.
"""

from __future__ import annotations

import pytest

from sea_of_colours.game.session import (
    Entity,
    GameSession,
    cell_facts,
)
from sea_of_colours.generator import Cell, Tile
from sea_of_colours.render import BLUE_LEVEL_NAMES, RED_LEVEL_NAMES


def _idx(sess: GameSession, x: int, y: int) -> int:
    return y * sess.width + x


def _seeing_session(cx: int = 8, cy: int = 6) -> GameSession:
    """A session where p1 has a probe, so some cells are genuinely live."""
    sess = GameSession.new(20, 14, seed=1212)
    sess.entities["probe_p1_test"] = Entity("probe_p1_test", "probe", "p1", cx, cy)
    return sess


# ── the tier ladder, straight off the engine's own cutoffs ──────────────


@pytest.mark.parametrize(
    "purity,tier",
    [(1, "trace"), (50, "trace"), (51, "vein"), (150, "vein"),
     (151, "mass"), (254, "mass"), (255, "pure")],
)
def test_red_tiers_sit_on_the_rulebook_boundaries(purity: int, tier: str) -> None:
    assert cell_facts(Tile.RED, purity)["tier"] == tier


@pytest.mark.parametrize(
    "purity,tier",
    [(1, "shallow"), (50, "shallow"), (51, "mid"), (150, "mid"),
     (151, "sink"), (254, "sink"), (255, "deep")],
)
def test_blue_tiers_sit_on_the_rulebook_boundaries(purity: int, tier: str) -> None:
    assert cell_facts(Tile.BLUE, purity)["tier"] == tier


def test_tier_names_come_from_render_not_a_second_copy() -> None:
    """Guards against someone re-typing the ladder into session.py."""
    reds = {cell_facts(Tile.RED, p)["tier"] for p in (10, 100, 200, 255)}
    blues = {cell_facts(Tile.BLUE, p)["tier"] for p in (10, 100, 200, 255)}
    assert reds == set(RED_LEVEL_NAMES)
    assert blues == set(BLUE_LEVEL_NAMES)


def test_colours_without_a_ladder_get_no_tier() -> None:
    # GREEN is uniform and EMPTY is nothing; inventing a tier for either
    # would put a word in the readout that means nothing in the rules.
    assert "tier" not in cell_facts(Tile.GREEN, 255)
    assert "tier" not in cell_facts(Tile.EMPTY, 0)
    assert cell_facts(Tile.GREEN, 255)["tile"] == "GREEN"


def test_purity_is_clamped_to_the_legal_band() -> None:
    assert cell_facts(Tile.RED, -5)["purity"] == 0
    assert cell_facts(Tile.RED, 9001)["purity"] == 255


# ── what actually reaches the client ────────────────────────────────────


def test_a_live_cell_reports_the_square_the_engine_holds() -> None:
    sess = _seeing_session()
    sess.grid[6][8] = Cell(Tile.RED, 191)
    cell = sess.player_dense_view("p1")[_idx(sess, 8, 6)]
    assert cell["kind"] == "terrain" and cell["stale"] is False
    assert (cell["tile"], cell["purity"], cell["tier"]) == ("RED", 191, "mass")


def test_fog_never_carries_terrain_facts() -> None:
    """The whole point of fog. A leak here would hand away the board."""
    sess = _seeing_session()
    dense = sess.player_dense_view("p1")
    fogged = [c for c in dense if c.get("kind") == "fog"]
    assert fogged, "expected some fog on a board with one probe"
    for c in fogged:
        assert "tile" not in c
        assert "purity" not in c
        assert "tier" not in c


def test_an_echo_quotes_what_the_seat_remembers_not_what_is_there_now() -> None:
    """Stale intel must age with the paint beside it, or the readout would
    contradict the tile it is describing — and would silently leak the
    current value of ground the seat can no longer see."""
    sess = _seeing_session()
    sess.grid[6][8] = Cell(Tile.RED, 200)
    sess.player_dense_view("p1")          # observe it once
    sess.entities.pop("probe_p1_test")    # probe dies, cell falls to memory
    sess.grid[6][8] = Cell(Tile.GREEN, 255)  # someone harvests it

    cell = sess.player_dense_view("p1")[_idx(sess, 8, 6)]
    assert cell["stale"] is True
    assert (cell["tile"], cell["purity"]) == ("RED", 200)


def test_facts_and_paint_describe_the_same_square() -> None:
    """A mismatch would be worse than no facts at all: the number would
    disagree with the colour the player is looking at."""
    sess = _seeing_session()
    dense = sess.player_dense_view("p1")
    checked = 0
    for y in range(sess.height):
        for x in range(sess.width):
            c = dense[_idx(sess, x, y)]
            if c.get("kind") != "terrain" or c.get("stale"):
                continue
            truth = sess.grid[y][x]
            assert c["tile"] == truth.tile.name
            assert c["purity"] == truth.purity
            checked += 1
    assert checked, "expected at least one live cell"


def test_a_snapshot_from_before_this_change_still_renders() -> None:
    """Seasons already in flight hold echo snapshots with paint and no tile.
    They must degrade to 'no facts', not to an exception."""
    from sea_of_colours.game.session import _snapshot_facts

    assert _snapshot_facts({"paint": {"ch": "  "}, "stale": True}) == {}
    assert _snapshot_facts({"tile": "not-a-tile", "purity": 4}) == {}
    assert _snapshot_facts({"tile": int(Tile.RED), "purity": 255})["tier"] == "pure"


def test_vedge_agrees_with_the_live_stale_split() -> None:
    """The vision border is built client-side from the live/stale split,
    because that is the only signal available for EVERY seat's array in a
    replay frame — `vedge` is stamped for the viewing seat alone. The two
    are statements of the same fact and must never disagree."""
    sess = _seeing_session()
    dense = sess.player_dense_view("p1")
    live = {
        (x, y)
        for y in range(sess.height)
        for x in range(sess.width)
        if dense[_idx(sess, x, y)].get("kind") == "terrain"
        and not dense[_idx(sess, x, y)].get("stale")
    }
    for (x, y) in live:
        vedge = dense[_idx(sess, x, y)].get("vedge", "")
        assert ("n" in vedge) == ((x, y - 1) not in live)
        assert ("e" in vedge) == ((x + 1, y) not in live)
        assert ("s" in vedge) == ((x, y + 1) not in live)
        assert ("w" in vedge) == ((x - 1, y) not in live)
    assert live, "expected the probe to light something up"
