"""OBS-59 — a second walk-in may share the pure, never the road to it.

`WALKIN_SECURE` re-hits the pure on purpose: it is an EMP hedge, and paying one
green penalty to be certain of a 765 jackpot is a good trade. The defect was
that only the destination was ever reasoned about. Asked for a route to the pure
with wave 1's trail unblocked, the pathfinder returned that trail — so the hedge
cost five green cells rather than one, a losing bet at any plausible jam rate.

These drive `_walkin_mine_patterns` directly rather than `build_seam_menu`: the
first cut of this file went through the menu, produced no patterns at all on its
fixtures, and passed three tests vacuously. Every test below therefore asserts
that the wave it is judging actually exists.
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control as sc

PURE = (31, 18)
BEACON = PURE
# Wave 1: drop on the live frontier, walk the corridor onto the pure.
WAVE1 = ((29, 18), [(29, 19), (30, 19), (31, 19), PURE])
WAKE = {WAVE1[0], *WAVE1[1]}


def _view(red, live):
    return {
        "grid": {"width": 40, "height": 28},
        "red_tiles": [{"x": x, "y": y, "purity": p} for (x, y), p in red.items()],
        "world": {"live": [{"x": x, "y": y, "tile": "RED"} for x, y in live]},
        "redsign": [{"mine": True, "cells": [[PURE[0], PURE[1], 1]]}],
    }


def _build(view, green=frozenset(), *, weapons=True):
    pats = sc._walkin_mine_patterns(
        view, BEACON, PURE, WAVE1, {},
        width=40, height=28, green=set(green), emp=None,
        spacing=0, weapons=weapons,
    )
    return {
        p.pattern_id: [tuple(w.drop_at)] + [tuple(c) for c in w.comb_path]
        for p in pats for w in p.waves
    }


def _open_board():
    """The pure is approachable from a second bearing as well as wave 1's."""
    red = {PURE: 255}
    for c in [(29, 18), (29, 19), (30, 19), (31, 19),
              (31, 17), (30, 17), (31, 16), (30, 16)]:
        red[c] = 120
    return _view(red, list(red))


def test_the_hedge_shares_the_pure_and_nothing_else():
    routes = _build(_open_board())
    assert "WALKIN_SECURE" in routes, "fixture must produce the hedge to judge it"
    shared = set(routes["WALKIN_SECURE"]) & WAKE
    assert shared <= {PURE}, (
        f"wave 2 re-walks {sorted(shared - {PURE})} of wave 1's route; "
        "the hedge only pays for the pure"
    )


def test_no_later_wave_re_walks_an_earlier_one():
    routes = _build(_open_board())
    order = [r for r in ("WALKIN_SECURE", "WALKIN_LATE") if r in routes]
    assert order, "fixture must produce at least one follow-up wave"
    seen = set(WAKE)
    for pid in order:
        shared = set(routes[pid]) & seen
        assert shared <= {PURE}, f"{pid} retraces {sorted(shared - {PURE})}"
        seen |= set(routes[pid])


def test_the_hedge_is_withdrawn_when_the_only_approach_is_the_wake():
    # `own_seam_d4` in miniature, and the geometry has to be exact to bite. The
    # whole east side of the pure is already stripped, so the only clean drop
    # cells sit WEST — behind wave 1 — and their shortest route to the pure is
    # wave 1's own corridor. That is the trap: a legal drop exists, so the old
    # code happily returned the retracing path rather than finding nothing.
    red = {PURE: 255, (29, 18): 120, (29, 19): 120, (30, 19): 120, (31, 19): 120}
    view = _view(
        red,
        [(29, 17), (28, 18), (28, 17), (29, 18), (29, 19), (30, 19),
         (31, 17), (30, 18)],
    )
    green = {(31, 17), (30, 18), (30, 17), (32, 18), (31, 16), (32, 17)}
    routes = _build(view, green)
    assert "WALKIN_GRAB" in routes, "wave 1 must still be offered"
    for pid in ("WALKIN_SECURE", "WALKIN_LATE"):
        shared = set(routes.get(pid, [])) & WAKE
        assert shared <= {PURE}, (
            f"{pid} was offered and retraces {sorted(shared - {PURE})}"
        )


def test_without_weapons_the_follow_up_goes_to_the_halo_not_the_pure():
    # Nothing can jam a landing, so the hedge premium buys nothing at all and
    # the second unit belongs on unstripped mass.
    routes = _build(_open_board(), weapons=False)
    if "WALKIN_SECURE" in routes:
        assert routes["WALKIN_SECURE"][-1] != PURE, "re-hitting a safe pure is pure loss"
