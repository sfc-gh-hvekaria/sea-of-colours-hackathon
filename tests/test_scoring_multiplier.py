"""v0.9.6 tier-multiplier scoring formula tests (RULEBOOK §3.1).

The scoring rule is::

    score(parcel) = effective_purity × MULT[tier]

with the multiplier table at session scope:

    MULT = {"trace": 0.75, "vein": 1.0, "mass": 1.5, "pure": 3.0}

These tests pin the math for each tier in isolation, the per-parcel
``effective_purity`` cannibalisation hook, and the cross-tier sum so
the scoring policy in :meth:`GameSession.score_for` doesn't drift
silently.
"""

from __future__ import annotations

from typing import Any, Dict

from sea_of_colours.game.session import (
    GameSession,
    RED_QUALITY_MULTIPLIER,
)


def _ship_parcel(
    sess: GameSession,
    player: str,
    purity: int,
    *,
    effective_purity: int | None = None,
) -> None:
    """Helper: append a synthetic shipped parcel directly so we test
    :meth:`score_for` without dragging in the resolver."""
    row: Dict[str, Any] = {
        "square_id": f"sq-{player}-{purity}-{len(sess.shipped_squares[player])}",
        "origin_purity": int(purity),
        "purity_at_harvest": int(purity),
        "origin_tile": 2,  # Tile.RED
        "tile_at_harvest": 2,
    }
    if effective_purity is not None:
        row["effective_purity"] = int(effective_purity)
    sess.shipped_squares[player].append(row)


def _new_session() -> GameSession:
    return GameSession.new(20, 14, seed=11)


def test_multiplier_table_default_values() -> None:
    """The default tunables match the design table."""
    assert RED_QUALITY_MULTIPLIER["trace"] == 0.75
    assert RED_QUALITY_MULTIPLIER["vein"] == 1.0
    assert RED_QUALITY_MULTIPLIER["mass"] == 1.5
    assert RED_QUALITY_MULTIPLIER["pure"] == 3.0


def test_score_trace_purity_50_applies_0p75_multiplier() -> None:
    """Trace tier (purity 0..50) → ×0.75."""
    sess = _new_session()
    _ship_parcel(sess, "p1", purity=50)
    # 50 × 0.75 = 37.5 → round to 38.
    assert sess.score_for("p1") == round(50 * 0.75)


def test_score_vein_purity_100_applies_1p0_multiplier() -> None:
    """Vein tier (51..150) → ×1.0 (unchanged)."""
    sess = _new_session()
    _ship_parcel(sess, "p1", purity=100)
    assert sess.score_for("p1") == 100


def test_score_mass_purity_200_applies_1p5_multiplier() -> None:
    """Mass tier (151..254) → ×1.5."""
    sess = _new_session()
    _ship_parcel(sess, "p1", purity=200)
    # 200 × 1.5 = 300.
    assert sess.score_for("p1") == 300


def test_score_pure_purity_255_applies_3x_multiplier() -> None:
    """Pure tier (255 only) → ×3.0."""
    sess = _new_session()
    _ship_parcel(sess, "p1", purity=255)
    assert sess.score_for("p1") == 765


def test_score_sums_across_mixed_tiers() -> None:
    """Multi-parcel score sums each parcel's tier-weighted contribution."""
    sess = _new_session()
    _ship_parcel(sess, "p1", purity=40)   # trace: 40 × 0.75 = 30
    _ship_parcel(sess, "p1", purity=100)  # vein:  100
    _ship_parcel(sess, "p1", purity=200)  # mass:  300
    _ship_parcel(sess, "p1", purity=255)  # pure:  765
    expected = round(40 * 0.75) + 100 + round(200 * 1.5) + 765
    assert sess.score_for("p1") == expected == 30 + 100 + 300 + 765


def test_effective_purity_stamp_overrides_raw() -> None:
    """``effective_purity`` (catapult cannibalisation stamp) wins over
    ``origin_purity`` when computing score."""
    sess = _new_session()
    # Raw 100 (vein), cannibalised down to 40 (trace).
    _ship_parcel(sess, "p1", purity=100, effective_purity=40)
    # Score reads the EFFECTIVE purity → trace tier × 0.75.
    assert sess.score_for("p1") == round(40 * 0.75) == 30


def test_pure_single_launch_beats_ten_trace_dust() -> None:
    """The headline-design assertion: one PURE-255 ship beats ten
    TRACE-50 ships on score, even though both shipped piles are
    nominally 'lots of RED'."""
    sess_a = _new_session()
    _ship_parcel(sess_a, "p1", purity=255)  # 765
    sess_b = _new_session()
    for _ in range(10):
        _ship_parcel(sess_b, "p1", purity=50)  # 50 × 0.75 × 10 = 375
    assert sess_a.score_for("p1") > sess_b.score_for("p1")
    assert sess_a.score_for("p1") == 765
    # ``score_for`` sums floats THEN rounds: 10 × (50 × 0.75) = 375.0 → 375.
    assert sess_b.score_for("p1") == round(10 * 50 * 0.75) == 375


def test_score_zero_for_empty_shipped_bay() -> None:
    """An empty shipped bay always scores 0."""
    sess = _new_session()
    assert sess.score_for("p1") == 0
