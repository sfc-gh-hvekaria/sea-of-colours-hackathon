"""Geometry contracts for the own-seam (CASE 1) redsign family — tabula_v12.

These are the rules a pattern's SHAPE has to obey regardless of which cells the
board happens to offer, so they can be checked without a live snapshot.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control as sc

_W = _H = 40
_BEACON = (16, 6)


def _seam_view(
    *,
    pure: Tuple[int, int] = _BEACON,
    mass_ring: bool = True,
    probe_at: Tuple[int, int] = _BEACON,
    chaff: bool = False,
) -> Dict[str, Any]:
    """A dense own-seam: one pure at the beacon, mass all around it, and a live
    probe overhead so the pure is drop-legal now (the SMASH_GRAB branch)."""
    red: List[Dict[str, Any]] = [
        {"x": pure[0], "y": pure[1], "purity": 255, "freshness": "fresh"},
    ]
    if mass_ring:
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx == dy == 0:
                    continue
                red.append({
                    "x": pure[0] + dx, "y": pure[1] + dy,
                    "purity": 200, "freshness": "fresh",
                })
    return {
        "day": 6,
        "world": {
            "width": _W, "height": _H,
            "live": [{"x": r["x"], "y": r["y"], "tile": "RED",
                      "purity": r["purity"]} for r in red],
        },
        "red_tiles": red,
        "redsign": [{"center": [_BEACON[0], _BEACON[1]], "mine": True,
                     "found_day": 5, "cells": []}],
        "entities": {"mine": [
            {"type": "probe", "pos": [probe_at[0], probe_at[1]],
             "nights_remaining": 2},
        ]},
        "my_assets": [
            {"kind": "probe", "state": "deployed",
             "x": probe_at[0], "y": probe_at[1], "nights_left": 2},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1"},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1_2"},
        ],
        "probe_stock": 3,
        "last_night": {
            "incoming_attacks": [{"type": "chaff"}] if chaff else [],
            "emp_scars": [],
        },
    }


def _walkin_view(chaff: bool = False) -> Dict[str, Any]:
    """The same seam with NO probe over the pure, so it is known but fogged and
    the menu takes the WALK-IN branch. A strip of live frontier to the west
    gives the walk somewhere legal to open."""
    far = (_BEACON[0] - 6, _BEACON[1])
    view = _seam_view(chaff=chaff, probe_at=far)
    return view


def _mine(view: Dict[str, Any]) -> Dict[str, sc.SeamPattern]:
    pats = sc.build_seam_menu(view, [], probe_stock=3)
    return {p.pattern_id: p for p in pats if p.mine}


def _cells(p: sc.SeamPattern) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for w in p.waves:
        if w.deny_only or w.drop_at is None:
            continue
        out.append((int(w.drop_at[0]), int(w.drop_at[1])))
        out += [(int(c[0]), int(c[1])) for c in (w.comb_path or [])]
    return out


# ── OBS-42 / fix 1.1 — the wake guard ──────────────────────────────────
def test_secure_mass_never_opens_on_the_pure_smash_grab_just_lifted():
    """The whole point of SECURE_MASS is the RING: doctrine pairs it with
    SMASH_GRAB, so wave 1 has already taken the pure by the time it lands. It
    used to pick the richest cell in its probe's disk, which IS that pure, so
    the "mass strip" opened on our own stripped green for -100 before it walked
    (OBS-42, seen on SNAP_ac1c55bf_d6_p4)."""
    mine = _mine(_seam_view())
    assert "SMASH_GRAB" in mine and "SECURE_MASS" in mine, sorted(mine)

    smash, secure = set(_cells(mine["SMASH_GRAB"])), _cells(mine["SECURE_MASS"])
    assert secure, "SECURE_MASS must still ship a route"
    assert secure[0] not in smash, (
        f"SECURE_MASS opens on {secure[0]}, which SMASH_GRAB already stripped"
    )
    assert _BEACON not in secure, (
        "the pure itself must never be in the follow-up wave's route"
    )


def test_the_late_sweep_avoids_both_earlier_waves():
    """Third wave, same rule, now against the wake of BOTH its predecessors."""
    mine = _mine(_seam_view())
    if "LATE_SWEEP" not in mine:
        return  # not offered on this board; the pairing above is the contract
    earlier = set(_cells(mine["SMASH_GRAB"])) | set(_cells(mine["SECURE_MASS"]))
    late = _cells(mine["LATE_SWEEP"])
    assert late and late[0] not in earlier, (
        f"LATE_SWEEP opens on {late[0]}, already stripped by an earlier wave"
    )


# ── the WALK-IN family, where the re-walk is a HEDGE and not a habit ───
def test_the_second_walk_in_skips_the_pure_when_nothing_can_jam_the_first():
    """`WALKIN_SECURE` doubles the pure as an EMP hedge — sound when a walk-in
    can be interdicted, pointless when no weapon is in play. Unconditional, it
    sent H2 back over ground H1 had just stripped for -100 a cell, which is why
    `own_seam_d4`/`d5` failed `no_wake_reentry` all through the baseline."""
    mine = _mine(_walkin_view(chaff=False))
    assert "WALKIN_GRAB" in mine and "WALKIN_SECURE" in mine, sorted(mine)

    first = set(_cells(mine["WALKIN_GRAB"]))
    assert _BEACON in first, "H1 is still the unit that takes the pure"
    second = _cells(mine["WALKIN_SECURE"])
    assert _BEACON not in second, (
        "with no weapon to hedge against, H2 must take the halo, not the pure"
    )
    # The APPROACH counts, not just the landing cell: picking a different
    # frontier is no use if the walk then follows H1's trail in.
    assert not (set(second) & first), (
        f"H2 re-enters H1's wake at {sorted(set(second) & first)}"
    )
    assert "MASS HALO" in (mine["WALKIN_SECURE"].waves[0].note or "")


def test_the_second_walk_in_still_doubles_the_pure_under_chaff():
    """The hedge is right when the thing it hedges is real: chaff can void the
    H1 walk, so paying a green to be SURE of the jackpot is the better trade
    (the owner's rule — repeat-the-pure is gated on chaff)."""
    mine = _mine(_walkin_view(chaff=True))
    assert "WALKIN_SECURE" in mine, sorted(mine)

    second = _cells(mine["WALKIN_SECURE"])
    assert _BEACON in second, "under chaff H2 must re-walk the pure"
    assert "EMP hedge" in mine["WALKIN_SECURE"].title


def test_the_late_walk_in_stays_off_both_earlier_routes_without_weapons():
    """Third unit, widest wake. It goes back for the pure only as the last
    resort of a hedge."""
    mine = _mine(_walkin_view(chaff=False))
    if "WALKIN_LATE" not in mine:
        return
    earlier = set(_cells(mine["WALKIN_GRAB"]))
    if "WALKIN_SECURE" in mine:
        earlier |= set(_cells(mine["WALKIN_SECURE"]))
    late = _cells(mine["WALKIN_LATE"])
    assert _BEACON not in late, "no third pass over the pure without weapons"
    assert late and late[0] not in earlier, (
        f"WALKIN_LATE opens on {late[0]}, already stripped"
    )


def test_the_guard_does_not_starve_a_seam_with_nothing_but_the_pure():
    """A one-cell seam has no ring to fall back to. The follow-up wave must
    still be offered — the agent decides whether re-hitting is worth it under
    weapons — rather than vanishing because every rich cell was excluded."""
    mine = _mine(_seam_view(mass_ring=False))
    assert "SMASH_GRAB" in mine, sorted(mine)
    if "SECURE_MASS" in mine:
        assert _cells(mine["SECURE_MASS"]), (
            "a wave that ships must carry a route"
        )


# ── fix 1.2 — the H1 ladder is three NAMED plays, not one that shifts ──
def test_the_smash_grab_is_the_pure_and_nothing_else_every_time():
    """It used to carry a 0-2 step tail chosen by the threat read, so the agent
    asking for the certain two-hour in-and-out got a walk whenever the board
    looked quiet. The guarantee has to be real to be worth naming."""
    for chaff in (False, True):
        mine = _mine(_seam_view(chaff=chaff))
        assert _cells(mine["SMASH_GRAB"]) == [_BEACON], (
            f"chaff={chaff}: SMASH_GRAB must be the pure alone"
        )
        assert mine["SMASH_GRAB"].waves[0].pickup_after is True


def test_the_value_variant_takes_two_steps_and_only_onto_mass_or_pure():
    mine = _mine(_seam_view())
    assert "SMASH_GRAB_VALUE" in mine, sorted(mine)

    route = _cells(mine["SMASH_GRAB_VALUE"])
    assert route[0] == _BEACON, "it is still the same drop on the pure"
    assert 1 <= len(route) - 1 <= 2, f"tail must be 1-2 steps, got {route[1:]}"

    live = sc._live_red(_seam_view())
    for cell in route[1:]:
        assert sc._tier_name(int(live.get(cell, 0))) in ("mass", "pure"), (
            f"{cell} is not mass/pure — the tail must never spend a step on trace"
        )


def test_the_value_variant_is_withheld_when_nothing_big_touches_the_pure():
    """A pure alone in trace makes +VALUE the same play as SMASH_GRAB, and a
    duplicate under a richer name reads as a choice while being none."""
    assert "SMASH_GRAB_VALUE" not in _mine(_seam_view(mass_ring=False))


def test_the_h1_options_are_a_ladder_of_length_over_the_same_drop():
    """SMASH_GRAB / SMASH_GRAB_VALUE / FULL_SWEEP are the same harvester on the
    same cell, differing only in how far they walk after landing."""
    mine = _mine(_seam_view())
    ladder = ["SMASH_GRAB", "SMASH_GRAB_VALUE", "FULL_SWEEP"]
    assert all(k in mine for k in ladder), sorted(mine)

    routes = [_cells(mine[k]) for k in ladder]
    assert len({r[0] for r in routes}) == 1, "all three open on the pure"
    assert [len(r) for r in routes] == sorted({len(r) for r in routes}), (
        f"lengths must strictly increase, got {[len(r) for r in routes]}"
    )


# ── fix 1.2 — SWEEP_RING, the greedy H2 ────────────────────────────────
def test_the_sweep_ring_appears_only_when_the_pure_has_an_unlit_side():
    """Its tail IS the unlit surround; with the pure fully lit there is nothing
    to sweep into and SECURE_MASS already covers the visible ring."""
    assert "SWEEP_RING" not in _mine(_seam_view())

    fogged = _seam_view(probe_at=(_BEACON[0] + 3, _BEACON[1]))
    # Blind the west side so the pure keeps live cover but loses its surround.
    fogged["world"]["live"] = [
        c for c in fogged["world"]["live"] if c["x"] >= _BEACON[0]
    ]
    assert sc._unseen_surround(fogged, _BEACON, _W, _H), "fixture must fog a side"
    assert "SWEEP_RING" in _mine(fogged), sorted(_mine(fogged))


def test_the_sweep_ring_never_launches_its_probe_onto_wave_ones():
    """With the pure fogged on every side the bearing collapses onto the
    default — the one wave 1 already used — and both probes land on one cell
    and destroy each other."""
    view = _seam_view(probe_at=(_BEACON[0] + 3, _BEACON[1]))
    view["world"]["live"] = []
    mine = _mine(view)
    if "SWEEP_RING" not in mine:
        return
    others = {
        p.waves[0].probe_at for k, p in mine.items()
        if k != "SWEEP_RING" and p.waves[0].probe_at is not None
    }
    assert mine["SWEEP_RING"].waves[0].probe_at not in others


# ── fix 1.3 — the FOGGED (echo pure) variant ───────────────────────────
def _echo_view() -> Dict[str, Any]:
    """The pure is REMEMBERED, not seen: nothing live, no probe overhead, no
    frontier to walk in from — so the seam must be lit before it can be taken."""
    view = _seam_view(probe_at=(0, 0))
    for r in view["red_tiles"]:
        r["freshness"] = "stale"
    view["world"]["live"] = []
    view["entities"]["mine"] = []
    return view


def test_the_echo_variant_says_three_hours_and_names_the_probe_first():
    """1.2 rewrote SMASH_GRAB as "two hours, drop and lift". That is true only
    with the pure already lit; on an echo the shape is probe → drop → lift, and
    calling it two hours understates the exposure by an hour on exactly the
    boards where the seam is least certain."""
    smash = _mine(_echo_view())["SMASH_GRAB"]
    assert smash.waves[0].probe_at is not None, "an echo pure must be lit first"
    assert "three hours" in smash.title.lower()
    assert "THREE-hour" in (smash.waves[0].note or "")

    lit = _mine(_seam_view())["SMASH_GRAB"]
    assert lit.waves[0].probe_at is None
    assert "two hours" in lit.title.lower()


def test_the_support_probe_never_sits_on_the_pure_it_lights():
    """Landing auto-harvests the cell — a probe parked on the pure is crushed by
    the very drop it made legal."""
    wave = _mine(_echo_view())["SMASH_GRAB"].waves[0]
    assert tuple(wave.probe_at) != tuple(wave.drop_at) != (None,)
    assert tuple(wave.probe_at) != _BEACON


def test_no_later_wave_launches_a_probe_onto_wave_ones():
    """SECURE_MASS took the opposite of wave 1's APPROACH bearing, which points
    straight back at wave 1's probe. With the pure on the beacon centre both
    resolved to the same cell and the second probe destroyed the first; jittered
    boards only dodged it by luck.

    Options sharing a unit_ordinal are ALTERNATIVES for one harvester and can
    never run together, so only cross-ordinal clashes are real."""
    by_ordinal: Dict[int, Dict[Tuple[int, int], str]] = {}
    for pid, p in _mine(_echo_view()).items():
        for w in p.waves:
            if w.probe_at is None:
                continue
            by_ordinal.setdefault(w.unit_ordinal, {})[tuple(w.probe_at)] = pid

    for a, cells_a in by_ordinal.items():
        for b, cells_b in by_ordinal.items():
            if a >= b:
                continue
            clash = set(cells_a) & set(cells_b)
            assert not clash, (
                f"{[cells_a[c] for c in clash]} and {[cells_b[c] for c in clash]} "
                f"are different harvesters launching a probe onto {clash}"
            )


# ── fix 1.6 — no drop is ever offered onto ground we know is stripped ──
def test_no_own_seam_wave_opens_on_a_cell_we_know_is_stripped():
    view = _seam_view()
    # Strip the whole ring bar the pure: every rich drop candidate is now green.
    stripped = {
        (_BEACON[0] + dx, _BEACON[1] + dy)
        for dx in range(-2, 3) for dy in range(-2, 3) if (dx, dy) != (0, 0)
    }
    view["world"]["live"] = [
        c if (c["x"], c["y"]) not in stripped
        else {"x": c["x"], "y": c["y"], "tile": "GREEN", "purity": 0}
        for c in view["world"]["live"]
    ]
    view["red_tiles"] = [
        r for r in view["red_tiles"] if (r["x"], r["y"]) not in stripped
    ]
    green = sc._known_green_cells(view)
    assert green, "fixture must actually register green"
    for pid, p in _mine(view).items():
        for w in p.waves:
            if w.deny_only or w.drop_at is None:
                continue
            assert tuple(w.drop_at) not in green, (
                f"{pid} drops on known-green {tuple(w.drop_at)} for -100"
            )
