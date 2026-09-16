"""SNAP — the fast weapon that resolves above the vision snapshot (§4.9.4).

v1.36. SNAP is one missile at one cell for 100 blue, and everything
interesting about it is a question of ORDER rather than of damage. It
lands before the hour's vision is recorded, which is what lets it deny a
smash-and-grab: kill the beacon and the landing it was lighting is no
longer legal *this* hour, not merely next.

That is the exact opposite of the ruling the EMP got in v1.28
(``docs/OUTSTANDING_ISSUES.md`` #24), where a same-hour salvo must NOT
void a rival's landing. Both rulings are deliberate and they are the
difference between the two weapons, so the pair is tested together
below: if a refactor ever collapses them onto the same side of the
snapshot, one of these two tests fails and says which way it slid.

The rest is the damage window. A harvester is maimed if it is standing
on the cell when the SNAP lands OR if it arrives during that hour — the
second half is a hot-cell stamp read by the step/drop paths, because an
arrival that has not happened yet cannot be hit by a function that has
already run.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.game.weapons import (
    SNAP_CLOUD_HOURS,
    SNAP_COST_BLUE_PURITY,
    SNAP_COST_CREDITS,
    SNAP_RADIUS,
    WEAPONISED_BLUE_CAP,
)


# ── fixtures ────────────────────────────────────────────────────────


def _board(**stock: int) -> GameSession:
    """A quiet 20x14 board with both harvesters parked in orbit.

    Orbit is the useful start state for these tests: a harvester in
    orbit has to DROP to reach the surface, and the drop is the move
    SNAP is meant to be able to refuse.
    """
    sess = GameSession.new(20, 14, seed=31)
    sess.phase = Phase.PLANNING
    for seat in sess.players:
        sess.credits[seat] = 10_000
        for kind, n in stock.items():
            sess.weapon_stock[seat][kind] = int(n)
    for seat in sess.players:
        ent = sess.entities[f"harvester_{seat}"]
        ent.x = None
        ent.y = None
    return sess


def _resolve(sess: GameSession, p1: List[dict], p2: List[dict]) -> List[dict]:
    sess.stash_policy("p1", p1)
    sess.stash_policy("p2", p2)
    sess.maybe_resolve_if_ready()
    return [f for f in (sess.last_night_replay or []) if isinstance(f, dict)]


def _snap(x: int, y: int) -> dict:
    return {"a": "snap", "at": [x, y]}


def _emp(x: int, y: int) -> dict:
    return {"a": "emp", "at": [[x, y]]}


def _drop(unit: str, x: int, y: int) -> dict:
    return {"a": "drop", "unit": unit, "at": [x, y]}


def _step(unit: str, x: int, y: int) -> dict:
    return {"a": "step", "unit": unit, "to": [x, y]}


def _wait() -> dict:
    return {"a": "wait"}


def _watch(sess: GameSession) -> Dict[str, Any]:
    """Hold the harvester objects before Aurora destroys them (§3.11.2).

    The entity is gone from ``sess.entities`` by the time a test asserts,
    but the object still carries the ``damaged`` flag the night set.
    """
    return {s: sess.entities[f"harvester_{s}"] for s in ("p1", "p2")}


def _landed(sess: GameSession, watched: Dict[str, Any], seat: str) -> bool:
    """Did this seat's harvester ever reach the surface tonight?"""
    ent = watched[seat]
    return ent.x is not None or bool(
        sess.track_paths.get(seat)
    )


def _texts(sess: GameSession) -> str:
    return "\n".join(
        str(e.get("text", "")) for e in sess.log if isinstance(e, dict)
    )


def _probes(sess: GameSession) -> List[Any]:
    return [e for e in sess.entities.values() if e.entity_type == "probe"]


# ── the headline: SNAP denies the landing, the EMP does not ─────────


def _smash_and_grab(weapon: dict, *, seat_stock: str) -> Tuple[GameSession, Dict[str, Any]]:
    """p2 lights a cell with a probe and drops onto it; p1 shoots the probe.

    Both weapons are fired at the SAME hour as the drop, at the cell the
    beacon is standing on, so the only thing separating the outcomes is
    where in the hour the weapon resolves.
    """
    sess = _board(**{seat_stock: 1})
    # p2's beacon lights (10,7); (10,7) is the landing site too, so the
    # probe's death is the landing's only source of light.
    ok, _ = sess.spawn_probe("p2", 10, 7)
    assert ok
    assert (10, 7) in sess.tiles_visible_now("p2")

    watched = _watch(sess)
    _resolve(
        sess,
        [weapon],
        [_drop("harvester_p2", 10, 7)],
    )
    return sess, watched


def test_a_snap_on_the_beacon_kills_the_landing_it_was_lighting():
    """The whole weapon. §4.9.4, and the answer to smash-and-grab."""
    sess, watched = _smash_and_grab(_snap(10, 7), seat_stock="snap")

    assert not _probes(sess), (
        "the SNAP should have destroyed the beacon"
    )
    assert watched["p2"].x is None and watched["p2"].y is None, (
        "the harvester should still be in orbit — its landing light went "
        "out BEFORE the hour's vision was recorded, so the drop is not "
        "legal this hour"
    )
    assert "live" in _texts(sess).lower()


def test_an_emp_on_the_same_beacon_in_the_same_hour_does_not(monkeypatch):
    """The sibling ruling (#24, v1.28) — and the contrast that defines SNAP.

    An EMP resolves BELOW the hour-start snapshot, so the beacon it kills
    was already recorded as lit and the landing stands. Nothing here is
    new; it is pinned beside the SNAP case so that a change which moves
    either weapon across the snapshot cannot pass unnoticed.
    """
    sess, watched = _smash_and_grab(_emp(10, 7), seat_stock="emp")

    assert not _probes(sess), (
        "the EMP should still have destroyed the beacon"
    )
    assert watched["p2"].x == 10 and watched["p2"].y == 7, (
        "the landing was judged against the hour-start snapshot, which "
        "still had the beacon in it — see OUTSTANDING_ISSUES #24"
    )


# ── the damage window ───────────────────────────────────────────────


def test_a_harvester_standing_on_the_cell_is_maimed_where_it_stands():
    sess = _board(snap=1)
    sess.entities["harvester_p2"].x = 9
    sess.entities["harvester_p2"].y = 6
    watched = _watch(sess)

    _resolve(sess, [_snap(9, 6)], [_wait()])

    assert watched["p2"].damaged is True


def test_a_harvester_that_walks_in_later_that_hour_is_maimed_on_arrival():
    """The hot-cell half. p2 steps ONTO the cell after the SNAP has landed.

    This is the timing the weapon is actually bought for: you cannot see
    the rival's harvester, but you can guess the square it wants.
    """
    sess = _board(snap=1)
    sess.entities["harvester_p2"].x = 8
    sess.entities["harvester_p2"].y = 6
    watched = _watch(sess)

    _resolve(sess, [_snap(9, 6)], [_step("harvester_p2", 9, 6)])

    assert watched["p2"].damaged is True, (
        "arriving on a cell a SNAP hit this hour must maim the arrival"
    )


def test_a_landing_into_the_cell_is_turned_back_rather_than_wrecked_on_it():
    """The one place SNAP treats a landing differently from a walk-in.

    A hull that steps onto a hot cell is already on the surface, so it
    is crippled where it stands. A hull that tries to LAND has an orbit
    to be sent back to, and is — the same shape as dropping onto a rival
    harvester (§3.6), which is the engine's existing answer to "your
    landing arrived into something".

    p2 can see the square from a probe that is NOT on it, so the vision
    route is closed and this is the hot cell doing the work on its own.
    """
    sess = _board(snap=1)
    ok, _ = sess.spawn_probe("p2", 11, 6)   # radius-4 disk, covers (9,6)
    assert ok
    assert (9, 6) in sess.tiles_visible_now("p2")
    watched = _watch(sess)

    _resolve(sess, [_snap(9, 6)], [_drop("harvester_p2", 9, 6)])

    assert watched["p2"].damaged is True
    assert not _landed(sess, watched, "p2"), (
        "a landing into a SNAP must be refused, not completed — the hull "
        "stays in orbit as wreckage"
    )
    assert "ABORTED" in _texts(sess), (
        "the log has to say the landing was refused; 'crippled' alone "
        "reads as a hull lying on the board that is not there"
    )


def test_a_landing_it_turned_back_never_spent_its_outing():
    """§3.9.2 gives a harvester one outing a night, and this was not one.

    The collision path is careful about this — a hull that stays orbital
    did not make an outing — and a SNAP that refuses a landing has to be
    careful in the same way, or the weapon quietly costs its victim the
    rest of the night as well as the hull.
    """
    sess = _board(snap=1)
    ok, _ = sess.spawn_probe("p2", 11, 6)
    assert ok

    _resolve(sess, [_snap(9, 6)], [_drop("harvester_p2", 9, 6)])

    spent = sess.deployed_harvesters_by_day.get(int(sess.day), set())
    assert "harvester_p2" not in spent


def test_neither_kind_of_hit_lets_the_square_be_harvested():
    """Walk in or land, the ore stays in the ground.

    Asserted for both paths together because it is the same promise —
    "it can protect a square that turn" — and the two paths reach it by
    different routes: the landing never happens, and the walk-in happens
    but returns before the harvest.
    """
    for verb in ("step", "drop"):
        sess = _board(snap=1)
        ok, _ = sess.spawn_probe("p2", 11, 6)
        assert ok
        if verb == "step":
            sess.entities["harvester_p2"].x = 8
            sess.entities["harvester_p2"].y = 6
            move = _step("harvester_p2", 9, 6)
        else:
            move = _drop("harvester_p2", 9, 6)
        before = sess.grid[6][9].tile

        _resolve(sess, [_snap(9, 6)], [move])

        assert sess.grid[6][9].tile == before, (
            f"{verb} into a SNAP must leave the square unharvested"
        )
        assert not sess.hoard_squares.get("p2")


def test_the_cell_cools_off_by_the_next_hour():
    """A SNAP denies a beat, not a stretch of night.

    p2 waits out the hour the SNAP lands in and walks on afterwards.
    """
    sess = _board(snap=1)
    sess.entities["harvester_p2"].x = 8
    sess.entities["harvester_p2"].y = 6
    watched = _watch(sess)

    _resolve(
        sess,
        [_snap(9, 6)],
        [_wait(), _step("harvester_p2", 9, 6)],
    )

    assert watched["p2"].damaged is False, (
        "the hot cell must not outlive its hour — otherwise SNAP is a "
        "cheap minefield rather than a tempo weapon"
    )


def test_a_maimed_harvester_takes_nothing_home_that_turn():
    """§3.6.1 — the damage is the ordinary one, so the ordinary rule applies.

    SNAP deliberately reuses the engine's existing ``damaged`` flag
    rather than inventing a second kind of broken, which is what makes
    this assertion a statement about SNAP at all.
    """
    sess = _board(snap=1)
    ent = sess.entities["harvester_p2"]
    ent.x, ent.y = 9, 6
    watched = _watch(sess)

    _resolve(sess, [_snap(9, 6)], [_wait()])

    assert watched["p2"].damaged is True
    assert not sess.hoard_squares.get("p2"), (
        "a harvester maimed this hour banks nothing this turn"
    )


def test_friendly_fire_is_on_exactly_as_it_is_for_the_salvo():
    sess = _board(snap=1)
    ok, _ = sess.spawn_probe("p1", 4, 4)
    assert ok

    _resolve(sess, [_snap(4, 4)], [_wait()])

    assert not _probes(sess), (
        "a SNAP put down on your own beacon kills your own beacon"
    )


# ── shape of the weapon ─────────────────────────────────────────────


def test_a_snap_is_one_cell_and_one_hour():
    sess = _board(snap=1)
    ok, _ = sess.apply_snap_launch("p1", 5, 5, hour=1)
    assert ok
    (cloud,) = sess.snap_clouds
    assert cloud["radius"] == SNAP_RADIUS == 0
    assert cloud["hours_remaining"] == SNAP_CLOUD_HOURS == 1
    assert sess.cells_in_any_snap_cloud() == {(5, 5)}


def test_firing_a_snap_costs_the_whole_hour():
    """§3.10 / §4.9.5 — a weapon hour is a spent hour, same as an EMP.

    Pinned because SNAP resolves in its own pre-empt phase, which is
    precisely where bug #15 leaked a second action per hour.
    """
    sess = _board(snap=1)
    sess.entities["harvester_p1"].x = 3
    sess.entities["harvester_p1"].y = 3

    _resolve(
        sess,
        [_snap(9, 6), _step("harvester_p1", 4, 3)],
        [_wait(), _wait()],
    )

    # The step is the seat's SECOND queued move, so it may only have run
    # in the hour after the launch — never alongside it.
    visits = sess.track_paths.get("p1") or {}
    assert "4:3" in visits or not visits, (
        "the step should resolve in a LATER hour, not the launch hour"
    )
    assert sess.weapon_stock["p1"]["snap"] == 0


def test_a_snap_with_nothing_in_the_rack_is_a_waste_not_a_free_shot():
    sess = _board(snap=0)
    _resolve(sess, [_snap(9, 6)], [_wait()])
    assert "no SNAP in stockpile" in _texts(sess)


# ── economy ─────────────────────────────────────────────────────────


def test_a_snap_is_one_of_the_six_pips_the_cap_is_cut_into():
    sess = GameSession.new(12, 8, seed=5)
    sess.credits["p1"] = 10_000
    sess.blue_bank["p1"] = 10_000

    assert sess.weapon_prices()["snap"] == SNAP_COST_BLUE_PURITY == 100
    assert WEAPONISED_BLUE_CAP % SNAP_COST_BLUE_PURITY == 0

    ok, _ = sess.apply_build_snap("p1", 6)
    assert ok
    assert sess.arsenal_blue("p1") == WEAPONISED_BLUE_CAP

    refused, msg = sess.apply_build_snap("p1", 1)
    assert refused is False
    assert sess.weapon_stock["p1"]["snap"] == 6, "a refused build spends nothing"


# ── what the night TELLS you a SNAP did ─────────────────────────────


def _events(sess: GameSession, seat: str) -> List[Dict[str, Any]]:
    from sea_of_colours.snowpark.view import build_agent_view

    return list(
        (build_agent_view(sess, seat).get("last_night") or {})
        .get("combat_events") or []
    )


def _snapped_landing() -> GameSession:
    """p1 turns p2's landing back with a SNAP, p2 seeing from off-cell."""
    sess = _board(snap=1)
    ok, _ = sess.spawn_probe("p2", 11, 6)
    assert ok
    _resolve(sess, [_snap(9, 6)], [_drop("harvester_p2", 9, 6)])
    return sess


def test_the_strike_reaches_every_seat_and_the_casualty_only_reaches_its_owner():
    """SNAP's feed is split the way the EMP's is, and for the reason.

    The round leaves a scorch mark on a square everybody can see, so
    hiding the strike would only cost agents an inference humans get
    free off the map. WHO it hit is a different question — that is the
    victim's own damage report, exactly as ``emp`` is public and
    ``emp_hit`` is not.

    Worth pinning hard because SNAP shipped writing NOTHING to this feed
    while `ENGINE_INTERFACE.md` promised it appeared there, so every
    fork was told to read a channel that was always empty.
    """
    sess = _snapped_landing()

    for seat in ("p1", "p2"):
        strikes = [e for e in _events(sess, seat) if e.get("type") == "snap"]
        assert len(strikes) == 1, f"{seat} cannot see the strike"
        assert strikes[0]["at"] == [9, 6]
        assert strikes[0]["owner"] == "p1"

    assert [e for e in _events(sess, "p2") if e.get("type") == "snap_hit"]
    assert not [e for e in _events(sess, "p1") if e.get("type") == "snap_hit"], (
        "the shooter does not get told what it hit — that is the "
        "victim's private detail"
    )


def test_a_refused_landing_and_a_wrecked_walk_in_do_not_report_the_same():
    """Both leave a damaged hull; only one leaves it on the board.

    An agent that cannot tell them apart goes looking for wreckage at
    (9,6) that is sitting in orbit, so the outcome is carried rather
    than left to be inferred from a position it cannot see anyway.
    """
    aborted = _snapped_landing()
    hit = [e for e in _events(aborted, "p2") if e.get("type") == "snap_hit"][0]
    assert hit["outcome"] == "landing_aborted"
    assert hit["by"] == ["p1"]

    sess = _board(snap=1)
    sess.entities["harvester_p2"].x = 8
    sess.entities["harvester_p2"].y = 6
    _resolve(sess, [_snap(9, 6)], [_step("harvester_p2", 9, 6)])
    walked = [e for e in _events(sess, "p2") if e.get("type") == "snap_hit"][0]
    assert walked["outcome"] == "crippled"


def test_a_shot_hull_is_not_filed_as_a_rammed_one():
    """``harv_damaged`` is documented as harvester-on-harvester damage.

    SNAP reused it at first, which made the kill feed say the two houses
    had collided when in fact one had shot the other — the one number
    in the scoreboard that is supposed to say who did what to whom.
    """
    sess = _snapped_landing()

    snapped = (sess.combat_attrib.get("snap_harvesters") or {})
    assert int(snapped.get("p1", {}).get("p2", 0)) == 1

    rammed = (sess.combat_attrib.get("harv_damaged") or {})
    assert int(rammed.get("p1", {}).get("p2", 0)) == 0


def test_a_snap_that_hits_nothing_still_says_it_was_fired():
    """Spending a SNAP on an empty square is intelligence too.

    The scorch mark announces it to anyone looking, so the feed has to
    as well — otherwise an agent reads a quiet night while its rival's
    published arsenal silently drops 100 blue.
    """
    sess = _board(snap=1)
    _resolve(sess, [_snap(3, 3)], [_wait()])

    strikes = [e for e in _events(sess, "p2") if e.get("type") == "snap"]
    assert len(strikes) == 1
    assert not [e for e in _events(sess, "p2") if e.get("type") == "snap_hit"]


def test_a_snap_costs_credits_too_and_the_view_says_which():
    """The published price must be the price the resolver charges.

    SNAP shipped briefly with 250c in the engine and 0c on the view,
    because the credit half was a literal in ``view.py`` rather than a
    table in ``weapons.py``. A buy panel quoting a price the orbit then
    refuses is the failure this pins.
    """
    from sea_of_colours.snowpark.view import build_agent_view

    sess = GameSession.new(12, 8, seed=5)
    sess.credits["p1"] = 10_000
    sess.blue_bank["p1"] = 10_000
    before = int(sess.credits["p1"])

    ok, _ = sess.apply_build_snap("p1", 1)
    assert ok
    charged = before - int(sess.credits["p1"])
    assert charged == SNAP_COST_CREDITS == 250

    published = build_agent_view(sess, "p1")["orbit"]["weapon_prices"]["snap"]
    assert published == {"blue": SNAP_COST_BLUE_PURITY, "credits": charged}


def test_a_season_played_before_snap_existed_never_hears_of_it():
    """The forward-only promise. An archived save keeps its own economy.

    A pre-v1.36 blob has no stamped price list, so it hydrates on the
    LEGACY table — which has no SNAP in it. The weapon must then be
    absent everywhere at once: not priced, not spec'd, not in the
    counters, and not buyable.
    """
    from sea_of_colours.snowpark.view import build_agent_view

    sess = GameSession.new(12, 8, seed=5)
    sess.credits["p1"] = 10_000
    sess.blue_bank["p1"] = 10_000
    blob = sess.to_dict()
    blob.pop("weapon_blue_costs", None)
    blob.pop("weapon_blue_cap", None)

    old = GameSession.from_dict(blob)
    assert "snap" not in old.weapon_prices()
    assert "snap" not in old.weapon_stock["p1"]
    assert "snap" not in old.weapons_used["p1"]

    orbit = build_agent_view(old, "p1")["orbit"]
    assert "snap" not in orbit["weapon_prices"]
    assert "snap" not in orbit["weapon_specs"]
    assert "snap" not in orbit["weapon_stock"]

    refused, msg = old.apply_build_snap("p1", 1)
    assert refused is False
    assert "does not stock" in msg
    assert "Nothing was spent" in msg


# ── withdrawal ──────────────────────────────────────────────────────


def test_snap_can_be_withdrawn_by_deleting_one_price(monkeypatch):
    """The isolation promise (``docs/ADDING_A_WEAPON.md``).

    SNAP is new and may not survive. Retiring it should be removing its
    entry from the price table, not unpicking the night loop — so this
    test does exactly that and asserts the weapon disappears from every
    published surface at once, the way the caltrop did.
    """
    from sea_of_colours.snowpark.view import build_agent_view
    from sea_of_colours.game import weapons as W

    retired = {k: v for k, v in W.BLUE_COST_BY_KIND.items() if k != "snap"}
    monkeypatch.setattr(W, "BLUE_COST_BY_KIND", retired)

    sess = GameSession.new(12, 8, seed=5)
    sess.credits["p1"] = 10_000
    sess.blue_bank["p1"] = 10_000

    assert set(sess.weapon_prices()) == {"emp", "chaff"}
    assert set(sess.weapon_stock["p1"]) == {"emp", "chaff"}

    orbit = build_agent_view(sess, "p1")["orbit"]
    for block in ("weapon_prices", "weapon_specs", "weapon_stock"):
        assert "snap" not in orbit[block], f"{block} still advertises SNAP"

    refused, _ = sess.apply_build_snap("p1", 1)
    assert refused is False


def test_a_save_holding_snap_still_loads_after_the_weapon_is_gone():
    """Retirement must not crash on the stock somebody already paid for.

    Dropping the kind is a projection, not a confiscation: the counter
    disappears and the refund is owed. This pins the first half — the
    half that decides whether the game opens at all.
    """
    sess = GameSession.new(12, 8, seed=5)
    sess.credits["p1"] = 10_000
    sess.blue_bank["p1"] = 10_000
    sess.apply_build_snap("p1", 2)
    blob = sess.to_dict()
    assert blob["weapon_stock"]["p1"]["snap"] == 2

    # Re-stamp the blob with an economy that has never heard of SNAP,
    # which is what a retirement release looks like to an in-flight game.
    blob["weapon_blue_costs"] = {"emp": 200, "chaff": 300}

    revived = GameSession.from_dict(blob)
    assert "snap" not in revived.weapon_stock["p1"]
    assert revived.weapon_stock["p1"]["emp"] == 0
    # And the board is still playable.
    revived.stash_policy("p1", [_wait()])
    revived.stash_policy("p2", [_wait()])
    revived.maybe_resolve_if_ready()
