"""v1.13 — automatic RED shipping + GREEN disposal (RULEBOOK §4.4/§4.5).

The Orbit phase used to end in two competitive drafts: a per-parcel
credit-bid catapult for RED and a round-robin flush for GREEN paid in
forfeit RED fuel. Both are gone. Settlement now happens *to* a seat:
every RED parcel ships at full value, every GREEN parcel is disposed of
at a flat penalty, and neither costs an action or a bid.

These tests pin the properties that make that safe, rather than the
arithmetic of any one settlement:

* RED ships whole, scored by tier multiplier, with no transit charge.
* GREEN always clears the vault — load-bearing, because GREEN sits at
  the top of the §3.14 displacement ladder and can never be evicted, so
  a seat that couldn't dispose of it would brick its own vault.
* The live score and the bulk scoreboard agree. ``compute_player_score``
  is shared by ``GameSession.score_for`` and ``bulk_session_scores``
  (which reads the SHIPPED/HOARD tables without hydrating a session), so
  the GREEN penalty has to survive in the persisted parcel rows.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

from sea_of_colours.game.orbit_resolver import OrbitResolver
from sea_of_colours.game.session import (
    GREEN_ENDGAME_PENALTY,
    GameSession,
    Phase,
    RED_QUALITY_MULTIPLIER,
    compute_player_score,
)
from sea_of_colours.generator import Tile


def _orbit_session(seed: int = 7) -> GameSession:
    sess = GameSession.new(20, 14, seed=seed)
    sess.phase = Phase.ORBIT
    sess.pending_orbit_actions = {p: None for p in sess.players}
    sess.credits = {p: 0 for p in sess.players}
    sess.hoard_squares = {p: [] for p in sess.players}
    sess.orbit_credits_awarded_day = sess.day
    return sess


def _stamp(
    sess: GameSession, owner: str, tile: Tile, rows: List[Tuple[str, int]],
) -> None:
    sess.hoard_squares.setdefault(owner, []).extend(
        {
            "square_id": sid,
            "site_id": sid,
            "tile_at_harvest": int(tile),
            "origin_tile": int(tile),
            "purity_at_harvest": int(purity),
            "origin_purity": int(purity),
            "lineage": "natural",
        }
        for sid, purity in rows
    )


def _settle(sess: GameSession) -> None:
    OrbitResolver().run(sess, {p: [] for p in sess.players})


# ── RED ────────────────────────────────────────────────────────────


def test_every_red_parcel_ships_without_being_asked() -> None:
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.RED, [("a", 40), ("b", 120), ("c", 255)])
    _settle(sess)

    assert sess.hoard_squares["p1"] == []
    assert len(sess.shipped_squares["p1"]) == 3


def test_red_scores_purity_times_tier_with_no_transit_charge() -> None:
    """A pure-255 parcel is worth its full 765 — the old row transit
    charge (10-100 RED off the top, by bid rank) is gone."""
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.RED, [("pure", 255)])
    _settle(sess)

    row = sess.shipped_squares["p1"][0]
    assert row["effective_purity"] == 255
    assert row["score_tier"] == "pure"
    assert sess.score_for("p1") == int(255 * RED_QUALITY_MULTIPLIER["pure"])


def test_trace_still_scores_less_than_pure_per_unit_purity() -> None:
    """Tier multipliers survived the simplification: auto-shipping
    changed *whether you choose*, not what a parcel is worth. Where you
    send a harvester at night has to keep mattering."""
    trace = _orbit_session()
    _stamp(trace, "p1", Tile.RED, [("t", 40)])
    _settle(trace)

    pure = _orbit_session()
    _stamp(pure, "p1", Tile.RED, [("p", 255)])
    _settle(pure)

    per_unit_trace = trace.score_for("p1") / 40
    per_unit_pure = pure.score_for("p1") / 255
    assert per_unit_pure > per_unit_trace


# ── GREEN ──────────────────────────────────────────────────────────


def test_green_is_disposed_of_and_charged_the_flat_penalty() -> None:
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.GREEN, [("g1", 200), ("g2", 90)])
    _settle(sess)

    assert sess.hoard_squares["p1"] == []
    assert sess.score_for("p1") == -2 * GREEN_ENDGAME_PENALTY


def test_green_penalty_is_flat_regardless_of_purity() -> None:
    """GREEN is a tax on mining blind, not a commodity — a 255-purity
    green costs exactly what a 1-purity green costs."""
    dirty = _orbit_session()
    _stamp(dirty, "p1", Tile.GREEN, [("g", 255)])
    _settle(dirty)

    clean = _orbit_session()
    _stamp(clean, "p1", Tile.GREEN, [("g", 1)])
    _settle(clean)

    assert dirty.score_for("p1") == clean.score_for("p1") == -GREEN_ENDGAME_PENALTY


def test_green_can_never_brick_a_vault() -> None:
    """The regression this whole mechanic exists to prevent.

    GREEN is the top tier of the §3.14 vault ladder and is never
    displaced — not by RED, not by another GREEN. Before v1.13 the flush
    was the only way out. If disposal were ever made optional again, a
    full vault of green would end the seat's game silently.
    """
    from sea_of_colours.game.session import HOARD_CAPACITY

    sess = _orbit_session()
    _stamp(
        sess, "p1", Tile.GREEN,
        [(f"g{i}", 100) for i in range(HOARD_CAPACITY)],
    )
    assert len(sess.hoard_squares["p1"]) == HOARD_CAPACITY
    _settle(sess)
    assert sess.hoard_squares["p1"] == [], "a full green vault must clear"


# ── The two scorers must agree ─────────────────────────────────────


def test_live_score_and_bulk_scoreboard_agree_after_settlement() -> None:
    """``compute_player_score`` is called both by ``score_for`` (live,
    from a hydrated session) and by the watcher's bulk scorer (straight
    off the persisted SHIPPED/HOARD parcel tables). Auto-disposed GREEN
    lands in SHIPPED, so the penalty has to be readable from those rows
    alone or the standings and the HUD disagree."""
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.RED, [("r", 120)])
    _stamp(sess, "p1", Tile.GREEN, [("g", 60)])
    _settle(sess)

    from_tables = compute_player_score(
        sess.shipped_squares["p1"],
        sess.hoard_squares["p1"],
        is_complete=False,
    )
    assert from_tables == sess.score_for("p1")
    # 120 vein at x1.0, minus one green
    assert from_tables == 120 - GREEN_ENDGAME_PENALTY


def test_the_hud_counter_tracks_the_canonical_score() -> None:
    """``cumulative_shipped_score`` is an O(1) cache of the SHIPPED bay's
    contribution, and the HUD scoreboard reads it instead of re-walking
    the parcels. Nothing in the engine consumes it, so a resolver that
    forgot to bump it would still produce correct final standings while
    showing the player a scoreboard frozen at zero all game.
    """
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.RED, [("a", 255), ("b", 100)])
    _stamp(sess, "p1", Tile.GREEN, [("g", 200)])
    _settle(sess)

    # 255 x 3.0 + 100 x 1.0 - 100 = 765 + 100 - 100
    assert sess.cumulative_shipped_score["p1"] == pytest.approx(765.0)
    assert sess.cumulative_shipped_score["p1"] == pytest.approx(
        sess.score_for("p1")
    )


def test_the_hud_counter_accumulates_across_days() -> None:
    """It is a running total, not a per-day figure — settling twice must
    add, and must still agree with the canonical score."""
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.RED, [("a", 100)])
    _settle(sess)
    first = sess.cumulative_shipped_score["p1"]

    sess.phase = Phase.ORBIT
    _stamp(sess, "p1", Tile.RED, [("b", 200)])
    _settle(sess)

    assert sess.cumulative_shipped_score["p1"] > first
    assert sess.cumulative_shipped_score["p1"] == pytest.approx(
        sess.score_for("p1")
    )


def test_green_penalty_survives_a_store_round_trip() -> None:
    """The stores persist parcels with their harvest keys and drop the
    extras settlement stamps on (``score_tier``, ``effective_purity``).
    The bulk scoreboard then scores straight off those rows, so the
    GREEN penalty has to be recoverable from ``tile_at_harvest`` alone —
    if it were only readable from ``score_tier``, standings computed
    from the tables would quietly disagree with the live HUD.
    """
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.RED, [("r", 120)])
    _stamp(sess, "p1", Tile.GREEN, [("g", 60)])
    _settle(sess)

    keep = {
        "square_id", "site_id", "tile_at_harvest", "purity_at_harvest",
        "lineage",
    }
    stripped = [
        {k: v for k, v in row.items() if k in keep}
        for row in sess.shipped_squares["p1"]
    ]
    assert not any("score_tier" in r for r in stripped)

    assert compute_player_score(
        stripped, [], is_complete=False,
    ) == sess.score_for("p1")


def test_blue_is_never_settled() -> None:
    """BLUE is fuel, not cargo — it neither ships nor is dumped, so it
    is the one thing that legitimately stays in the vault across an
    orbit. A settlement that swept the whole vault would silently
    disarm the seat."""
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.BLUE, [("b1", 200), ("b2", 40)])
    _stamp(sess, "p1", Tile.RED, [("r", 100)])
    _settle(sess)

    left = sess.hoard_squares["p1"]
    assert len(left) == 2
    assert all(
        int(p["tile_at_harvest"]) == int(Tile.BLUE) for p in left
    )
    assert len(sess.shipped_squares["p1"]) == 1


def test_green_is_charged_once_not_twice() -> None:
    """Disposed green moves from HOARD to SHIPPED. If the scorer counted
    it in both places the penalty would silently double."""
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.GREEN, [("g", 100)])
    _settle(sess)

    assert sess.score_for("p1") == -GREEN_ENDGAME_PENALTY


def test_settlement_is_idempotent_across_days() -> None:
    """A second settlement with an empty vault must not re-charge the
    green already disposed of on day 1."""
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.GREEN, [("g", 100)])
    _stamp(sess, "p1", Tile.RED, [("r", 100)])
    _settle(sess)
    first = sess.score_for("p1")

    sess.phase = Phase.ORBIT
    sess.day += 1
    _settle(sess)

    assert sess.score_for("p1") == first


# ── The blob the replay/station UI reads ───────────────────────────


def test_settlement_blob_still_feeds_the_station_animation() -> None:
    """``catapult_history`` is the only channel between settlement and
    the orbital-station visuals, and nothing in the engine consumes it,
    so a dropped key breaks the UI silently and only at replay time.

    ``slot_assignments`` used to be a fixed 20-entry lattice because
    slots were what seats bid over. It is now just the manifest — one
    entry per parcel, however many that is — but the per-entry shape is
    unchanged so the launch animation and briefing grid still read it.
    """
    sess = _orbit_session()
    _stamp(sess, "p1", Tile.RED, [("r1", 200), ("r2", 30)])
    _stamp(sess, "p2", Tile.GREEN, [("g1", 50)])
    _settle(sess)

    blob = sess.catapult_history[-1]
    assert blob["day"] == sess.day

    cat = blob["catapult"]
    assert cat["auto"] is True
    assert len(cat["slot_assignments"]) == 2
    slot = cat["slot_assignments"][0]
    assert {"seat", "shipped", "tier", "effective_purity", "score",
            "tier_multiplier", "parcel"} <= set(slot)
    assert cat["seats"]["p1"]["score_shipped"] > 0

    jett = blob["jettison"]
    assert len(jett["slot_assignments"]) == 1
    assert jett["slot_assignments"][0]["seat"] == "p2"
    assert jett["seats"]["p2"]["penalty"] == GREEN_ENDGAME_PENALTY


def test_settlement_manifest_is_not_capped_at_twenty() -> None:
    """The old lattice held 20 parcels across all seats. Two full vaults
    now exceed that, and every one of them must appear — the UI sizes
    itself to the manifest rather than the other way round."""
    from sea_of_colours.game.session import HOARD_CAPACITY

    sess = _orbit_session()
    for seat in ("p1", "p2"):
        _stamp(
            sess, seat, Tile.RED,
            [(f"{seat}-{i}", 100) for i in range(HOARD_CAPACITY)],
        )
    _settle(sess)

    total = 2 * HOARD_CAPACITY
    assert total > 20
    assert len(sess.catapult_history[-1]["catapult"]["slot_assignments"]) == total


# ── No action cap ──────────────────────────────────────────────────


def test_orbit_accepts_more_than_three_actions() -> None:
    """v1.13 — MAX_ORBIT_ACTIONS is gone. The old cap limited how many
    *kinds* of thing a seat did per orbit, never the volume of any one
    of them (every build action carries a ``count``), so it only ever
    produced a "one thing too many" refusal."""
    from sea_of_colours.game.policy import OrbitWasteAction

    sess = _orbit_session()
    sess.credits = {p: 10_000 for p in sess.players}
    # v1.31 — the fifth action used to be ``build_mine``. The caltrop is
    # retired, so that now parses to a waste marker: the count would still
    # have read 5 while only four real actions got through.
    ok, errs = sess.stash_orbit_actions("p1", [
        {"a": "build_probe", "count": 2},
        {"a": "build_harvester"},
        {"a": "build_emp"},
        {"a": "build_chaff"},
        {"a": "repair", "unit": "harvester_p1"},
    ])
    assert ok and errs == []
    stashed = sess.pending_orbit_actions["p1"]
    assert len(stashed) == 5
    assert not [a for a in stashed if isinstance(a, OrbitWasteAction)]
