"""v1.24 — the results screen must AGREE with the scorer.

The season Nubes_Fissure ended with three separate ways for the endgame
payload to contradict ``compute_player_score``, and between them they
produced the user-visible symptom "the end score is different from the
running score":

1. **Disposed GREEN priced at zero.** Settlement moves GREEN out of the
   vault and appends it to ``shipped_squares`` carrying its GREEN origin
   tile. ``compute_player_score`` charges ``-GREEN_ENDGAME_PENALTY`` for
   such a row; the endgame's own per-parcel helper had no GREEN branch
   and valued it at ``effective_purity 0 x mult = 0``. Piotr's curve
   therefore ran 400 above his true score.
2. **The settlement day fell off the axis.** The chart's day axis was
   ``1..season_day_cap`` but the terminal orbit stamps
   ``shipped_day = cap + 1``, so everything banked in the final
   settlement was charted nowhere. Lucas lost 429 points off the end of
   his curve.
3. **Rounding per parcel instead of once.** The scorer sums floats and
   rounds the total; the chart rounded every parcel. A seat holding many
   ``trace`` rows (x0.75, rarely an integer) drifted — Florian's
   manifest summed to 624 against a true 626.

Each test below pins one of those, plus the decomposition invariant that
makes the card readable at all.
"""

from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "memory")

from sea_of_colours.generator import Tile
from sea_of_colours.game.session import (
    GREEN_ENDGAME_PENALTY,
    GameSession,
    Phase,
    compute_player_score,
    compute_score_breakdown,
)
from sea_of_colours.snowpark import engine as soc_engine
from sea_of_colours.snowpark.store import InMemorySocStore


def _persist(store: InMemorySocStore, sess: GameSession) -> None:
    store.save_session({
        "session_id": sess.session_id,
        "season_name": sess.season_name,
        "width": sess.width,
        "height": sess.height,
        "seed": sess.seed,
        "day": sess.day,
        "phase": sess.phase.value,
        "json_state": sess.to_dict(),
    })


def _red(purity: int, tier: str, day: int) -> dict:
    return {
        "tile_at_harvest": int(Tile.RED),
        "purity_at_harvest": purity,
        "effective_purity": purity,
        "score_tier": tier,
        "shipped_day": day,
    }


def _disposed_green(day: int) -> dict:
    """Exactly what ``OrbitResolver._dispose_green_auto`` appends."""
    return {
        "tile_at_harvest": int(Tile.GREEN),
        "purity_at_harvest": 255,
        "effective_purity": 0,
        "score_tier": "green",
        "shipped_day": day,
    }


def _season(cap: int = 3) -> GameSession:
    sess = GameSession.new(
        20, 14, seed=91,
        players=["p1", "p2"],
        agents={"p1": "red_harvest", "p2": "red_harvest"},
        season_day_cap=cap,
    )
    sess.phase = Phase.SEASON_COMPLETE
    sess.final_orbit = True
    return sess


def _summary(sess: GameSession) -> dict:
    store = InMemorySocStore()
    _persist(store, sess)
    return soc_engine.get_endgame_summary(store, sess.session_id)


# ── 1. GREEN is a charge, not a zero ─────────────────────────────────
def test_disposed_green_is_charged_in_the_shipped_curve() -> None:
    """A GREEN row in SHIPPED must pull the curve DOWN by 100.

    This is the defect that made a losing seat look like a winning one on
    the chart: the parcel is in ``shipped_squares``, so it was charted,
    but it was charted at its ``effective_purity`` of 0.
    """
    sess = _season(cap=3)
    sess.shipped_squares["p1"] = [
        _red(255, "pure", 2),          # +765
        _disposed_green(2),            # -100
        _disposed_green(3),            # -100
    ]
    summary = _summary(sess)

    p1 = next(p for p in summary["players"] if p["seat"] == "p1")
    assert p1["score"] == 765 - 200

    series = summary["shipped_by_day"]["p1"]
    # Day 2 banks the pure AND pays one green; day 3 pays the second.
    assert series == [0, 665, 565]
    assert series[-1] == p1["score"], (
        "the last point of the cumulative curve IS the final score"
    )


def test_green_still_reads_as_a_penalty_line_after_settlement_moved_it()\
        -> None:
    """The card must NAME the green charge, not bury it in ``shipped``.

    ``vault_green_count`` is zero once settlement has flushed the vault,
    so the old breakdown showed ``green_penalty: 0`` while the cost sat
    inside the shipped figure — leaving the player with a score they
    could not account for.
    """
    sess = _season(cap=3)
    sess.shipped_squares["p1"] = [_red(255, "pure", 2), _disposed_green(2)]
    assert sess.vault_green_count("p1") == 0, "fixture: vault is flushed"

    summary = _summary(sess)
    p1 = next(p for p in summary["players"] if p["seat"] == "p1")
    bd = p1["breakdown"]

    assert bd["green_penalty"] == GREEN_ENDGAME_PENALTY
    assert bd["shipped"] == 765, "shipped is RED only"
    assert bd["shipped"] - bd["green_penalty"] + bd["vault_red_loss"] \
        == p1["score"]


# ── 2. the settlement day is on the axis ─────────────────────────────
def test_the_final_settlement_day_is_charted() -> None:
    """Settlement stamps ``cap + 1``; the axis has to reach it.

    Otherwise the biggest single banking event of the season — the
    terminal orbit, which ships the entire remaining vault — is charted
    nowhere and the curve ends below the score by exactly that amount.
    """
    sess = _season(cap=3)
    sess.shipped_squares["p1"] = [
        _red(100, "vein", 2),      # +100 during play
        _red(255, "pure", 4),      # +765 at settlement, day cap + 1
    ]
    summary = _summary(sess)

    assert summary["days"] == [1, 2, 3, 4], "axis must reach the settlement"
    series = summary["shipped_by_day"]["p1"]
    assert series == [0, 100, 100, 865]
    p1 = next(p for p in summary["players"] if p["seat"] == "p1")
    assert series[-1] == p1["score"] == 865


def test_a_season_that_never_settles_late_keeps_its_axis() -> None:
    """The axis is derived, not blindly extended.

    A season whose parcels all shipped within the cap must still chart
    exactly ``cap`` points, or every existing chart gains a dead column.
    """
    sess = _season(cap=3)
    sess.shipped_squares["p1"] = [_red(100, "vein", 2)]
    summary = _summary(sess)
    assert summary["days"] == [1, 2, 3]
    assert summary["shipped_by_day"]["p1"] == [0, 100, 100]


# ── 3. round once, not per parcel ────────────────────────────────────
def test_many_trace_parcels_do_not_drift_the_curve() -> None:
    """``trace`` is x0.75, so per-parcel rounding accumulates error.

    Twelve purity-5 traces are 3.75 each: rounded per parcel that is
    4 x 12 = 48, but the scorer's single rounding of 45.0 is 45.
    """
    sess = _season(cap=3)
    sess.shipped_squares["p1"] = [_red(5, "trace", 2) for _ in range(12)]
    summary = _summary(sess)

    p1 = next(p for p in summary["players"] if p["seat"] == "p1")
    assert p1["score"] == 45
    assert summary["shipped_by_day"]["p1"][-1] == 45, (
        "per-parcel rounding would read 48 here"
    )


# ── 4. the decomposition invariant ───────────────────────────────────
def test_breakdown_always_reconstructs_the_score() -> None:
    """``shipped - green_penalty + vault_red_loss == final``, always.

    Swept over the shapes that actually occur, including the mixed one
    that broke in play: RED banked, GREEN disposed into SHIPPED, and RED
    left in the vault for the fire-sale.
    """
    cases = [
        ([], []),
        ([_red(255, "pure", 2)], []),
        ([_disposed_green(2)], []),
        ([_red(255, "pure", 2), _disposed_green(2)], []),
        ([_red(40, "trace", 1)] * 7, [{"tile_at_harvest": int(Tile.GREEN),
                                       "purity_at_harvest": 255}]),
        (
            [_red(200, "mass", 3), _disposed_green(3), _disposed_green(3)],
            [{"tile_at_harvest": int(Tile.RED), "purity_at_harvest": 180}],
        ),
    ]
    for shipped, hoard in cases:
        for is_complete in (False, True):
            bd = compute_score_breakdown(
                shipped, hoard, is_complete=is_complete
            )
            final = compute_player_score(
                shipped, hoard, is_complete=is_complete
            )
            assert bd["final"] == final
            assert (
                bd["shipped"] - bd["green_penalty"] + bd["vault_red_loss"]
                == final
            ), f"breakdown does not add up for {shipped!r}/{hoard!r}"


def test_vault_red_fire_sale_only_lands_once_the_season_is_over() -> None:
    """The one term that legitimately differs mid-season vs at the end.

    Worth pinning explicitly: it is the honest half of "the end score
    changed", and a future refactor that leaks it into the running score
    would silently inflate every live scoreboard.
    """
    hoard = [{"tile_at_harvest": int(Tile.RED), "purity_at_harvest": 200}]
    running = compute_score_breakdown([], hoard, is_complete=False)
    final = compute_score_breakdown([], hoard, is_complete=True)
    assert running["vault_red_loss"] == 0
    assert final["vault_red_loss"] == 100  # 50% of raw purity, no multiplier
    assert final["final"] - running["final"] == 100


def test_the_live_status_payload_carries_the_canonical_score() -> None:
    """`/status` must ship the scorer's number, not just the bay cache.

    Checked as part of the v1.24 fan-out, because the whole defect was
    two routes to one number drifting apart and the live HUD is a THIRD
    route. Note the deliberate asymmetry that is NOT a bug: the running
    figure excludes the vault RED fire sale, which only exists once the
    season completes, so a hoarder is understated on purpose (§4).
    """
    sess = _season(cap=3)
    sess.phase = Phase.PLANNING  # a LIVE season, mid-flight
    sess.final_orbit = False
    sess.shipped_squares["p1"] = [_red(255, "pure", 2), _disposed_green(2)]
    sess.hoard_squares["p1"] = [
        {"tile_at_harvest": int(Tile.RED), "purity_at_harvest": 200},
    ]
    sess.cumulative_shipped_score["p1"] = 765.0 - GREEN_ENDGAME_PENALTY

    store = InMemorySocStore()
    _persist(store, sess)
    status = soc_engine.get_session_status(store, sess.session_id)

    assert status["scores"]["p1"] == compute_player_score(
        sess.shipped_squares["p1"],
        sess.hoard_squares["p1"],
        is_complete=False,
    )
    assert status["scores"]["p1"] == 665  # 765 pure, less one disposed GREEN
    # With nothing held but RED, the bay cache happens to agree.
    assert status["cumulative_shipped_score"]["p1"] == 665


def test_green_awaiting_disposal_is_charged_on_the_live_scoreboard() -> None:
    """The ORBIT-phase gap that made the running score look wrong.

    GREEN harvested tonight sits in the vault until settlement runs, and
    settlement runs at EVERY orbit, not just the last one — so this is a
    phase the player sits in on every single night of the season.
    ``compute_player_score`` charges that GREEN immediately (the debit is
    outside its ``is_complete`` guard, because disposal is mandatory
    under §4.5 and no play can avoid it); ``cumulative_shipped_score``,
    being a SHIPPED-bay cache, does not.

    Measured before the fix: HUD 765 against a true 465, then a visible
    300-point drop the instant the player submitted their orbit. And
    because ``/view`` and the agent percept already used the scorer, a
    human and a bot holding the same position were shown different
    running scores.

    So the two fields must DISAGREE here — that is what makes the cache
    the wrong thing to headline — and ``scores`` must be the honest one.
    """
    sess = _season(cap=3)
    sess.phase = Phase.ORBIT  # settlement has NOT run yet
    sess.final_orbit = False
    sess.shipped_squares["p1"] = [_red(255, "pure", 1)]
    sess.cumulative_shipped_score["p1"] = 765.0
    sess.hoard_squares["p1"] = [
        {"tile_at_harvest": int(Tile.GREEN), "purity_at_harvest": 255}
        for _ in range(3)
    ]

    store = InMemorySocStore()
    _persist(store, sess)
    status = soc_engine.get_session_status(store, sess.session_id)

    assert status["scores"]["p1"] == 765 - 3 * GREEN_ENDGAME_PENALTY == 465
    assert status["scores"]["p1"] == sess.score_for("p1")
    # The cache is untouched and still shipped-only — the SHIPPED tab
    # is captioned as that bay and wants exactly this number.
    assert status["cumulative_shipped_score"]["p1"] == 765

    # And the agent percept, which always used the scorer, agrees with
    # what the human is now shown. This is the parity that was missing.
    view = soc_engine.get_view(store, sess.session_id, "p1")
    assert int(view["scores"]["p1"]) == status["scores"]["p1"]
