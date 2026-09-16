"""Blue-sign radiative-overlay tests (RULEBOOK §4.6).

Blue-sign is a STATIC, PERSISTENT, fog-independent overlay computed
once at season birth from the generation-time blue geometry. Every
house sees the identical fuzzy smear; it never fades (even after the
blue is mined out) and never reveals the exact squares or purity.
"""

from __future__ import annotations

from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.generator import Tile
from sea_of_colours.snowpark.view import build_agent_view

# Seed 42 on a 20×14 board yields multiple blue pockets → a non-empty
# blue-sign with more than one region.
_SEED = 42


def _regions(sess: GameSession):
    return sess.blue_sign


def test_blue_sign_is_nonempty_and_well_formed() -> None:
    sess = GameSession.new(20, 14, seed=_SEED)
    regions = _regions(sess)
    assert regions, "seed 42 should produce blue pockets"
    for r in regions:
        assert set(r.keys()) >= {"id", "center", "cells"}
        cx, cy = r["center"]
        # The center is intentionally jittered off the true centroid, so
        # it can sit slightly outside the grid; just sanity-check it's
        # numeric and roughly on-board.
        assert -5 <= cx <= sess.width + 5
        assert -5 <= cy <= sess.height + 5
        assert r["cells"], "every region paints at least one cell"
        for cell in r["cells"]:
            x, y, intensity = cell
            assert 0 <= x < sess.width
            assert 0 <= y < sess.height
            assert 0.0 < float(intensity) <= 1.0


def test_blue_sign_is_deterministic_in_seed() -> None:
    a = GameSession.new(20, 14, seed=_SEED).blue_sign
    b = GameSession.new(20, 14, seed=_SEED).blue_sign
    assert a == b


def test_blue_sign_identical_for_every_seat_and_fog_independent() -> None:
    """The overlay is global: identical in both seats' agent views even
    in hidden (fog) mode."""
    sess = GameSession.new(20, 14, seed=_SEED)
    sess.visibility_mode = "hidden"
    v1 = build_agent_view(sess, "p1")
    v2 = build_agent_view(sess, "p2")
    assert v1["blue_sign"] == v2["blue_sign"] == sess.blue_sign
    assert v1["blue_sign"], "blue-sign must survive fog"


def test_blue_sign_survives_to_dict_roundtrip() -> None:
    sess = GameSession.new(20, 14, seed=_SEED)
    revived = GameSession.from_dict(sess.to_dict())
    assert revived.blue_sign == sess.blue_sign


def test_blue_sign_persists_after_blue_depletion() -> None:
    """Mining out every blue cell does NOT change the stored overlay."""
    sess = GameSession.new(20, 14, seed=_SEED)
    before = [dict(r) for r in sess.blue_sign]
    # Deplete: wipe all BLUE terrain to EMPTY.
    for row in sess.grid:
        for i, cell in enumerate(row):
            if cell.tile == Tile.BLUE:
                from sea_of_colours.generator import Cell
                row[i] = Cell(Tile.EMPTY, 0)
    assert sess.blue_sign == before


def test_blue_sign_recomputed_for_legacy_saves() -> None:
    """A save missing ``blue_sign`` recomputes an identical overlay from
    the (persisted) generation-time ledger on hydrate."""
    sess = GameSession.new(20, 14, seed=_SEED)
    blob = sess.to_dict()
    blob.pop("blue_sign", None)
    revived = GameSession.from_dict(blob)
    assert revived.blue_sign == sess.blue_sign


def test_blue_sign_empty_when_no_blue() -> None:
    """A seed with no blue pockets yields an empty overlay (no crash)."""
    sess = GameSession.new(20, 14, seed=7)
    # If this seed ever starts generating blue, the test still holds the
    # invariant: no blue cells ⇒ empty overlay.
    has_blue = any(
        int(row.get("tile_at_generation", -1)) == int(Tile.BLUE)
        and str(row.get("lineage")) == "natural"
        for row in (sess.ledger.entries or {}).values()
    )
    if not has_blue:
        assert sess.blue_sign == []
