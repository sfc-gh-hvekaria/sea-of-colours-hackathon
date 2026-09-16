"""OBS-43 (reach) and OBS-57 — the seam must value ground honestly.

Both faults come off `SNAP_ac1c55bf_d6_p4`, the one suite turn that never
passed: the second harvester was offered a gamble on fog and a walk over ground
a rival had already stripped, so it declined the seam altogether and took a
chain twenty cells away. The agent's refusal was correct — the menu was lying.
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control as sc

BEACON = (16, 6)
PROBE = (14, 6)


def _view(*, red, smear, trail=()):
    """A minimal view: `red` is what we can see, `smear` the broadcast footprint."""
    return {
        "grid": {"width": 48, "height": 32},
        "red_tiles": [
            {"x": x, "y": y, "purity": p} for (x, y), p in red.items()
        ],
        "redsign": [{
            "mine": True,
            "cells": [[x, y, 1] for x, y in smear],
        }],
        "competitor_intel": {
            "new_this_day": [
                {"kind": "enemy_harvester_trail", "at": [x, y]} for x, y in trail
            ],
        },
    }


def _ats(ring):
    return [tuple(r["at"]) for r in ring]


def test_a_visible_pure_retires_the_smears_promise_on_the_rest():
    # The smear advertises ONE pure. We can see it at the beacon, so the fogged
    # cells around it are ordinary fog and must not outrank visible mass.
    view = _view(
        red={BEACON: 255, (16, 7): 240, (16, 8): 105},
        smear=[BEACON, (17, 4), (14, 8), (15, 3)],
    )
    ats = _ats(sc.enumerate_value_ring(view, BEACON, PROBE))
    assert ats[0] == BEACON
    assert ats[1] == (16, 7), f"visible 240 mass must rank second, got {ats}"
    for fogged in ((17, 4), (14, 8), (15, 3)):
        assert fogged not in ats, f"{fogged} is fog, not a second pure"


def test_an_unlocated_pure_keeps_the_promise():
    # A rival's fogged seam: nothing visible inside the smear, so the broadcast
    # is the only evidence there is and the smear cells stay pure candidates.
    view = _view(red={(16, 7): 240}, smear=[BEACON, (17, 4), (15, 3)])
    ats = _ats(sc.enumerate_value_ring(view, BEACON, PROBE))
    assert BEACON in ats and (17, 4) in ats
    assert ats[0] != (16, 7), "an unlocated pure still outranks known mass"


def test_ground_a_rival_stripped_is_not_offered_as_value():
    # OBS-43's reach. The trail says these were harvested last night; the echo
    # still says 158/142. The trail wins.
    view = _view(
        red={BEACON: 255, (16, 7): 240, (17, 7): 158, (17, 8): 142},
        smear=[BEACON],
        trail=[(17, 7), (17, 8)],
    )
    ats = _ats(sc.enumerate_value_ring(view, BEACON, PROBE))
    assert (17, 7) not in ats and (17, 8) not in ats
    assert (16, 7) in ats, "the surviving mass must still be offered"


def test_seam_geometry_sees_the_trail_as_green():
    # The union is what makes the above reach drop/comb selection, not just the
    # ring: v12 seam control must not import the live-vision-only reader.
    view = _view(red={BEACON: 255}, smear=[BEACON], trail=[(17, 7)])
    assert (17, 7) in sc._known_green_cells(view)
    assert (17, 7) not in sc._live_green_cells(view), (
        "fixture is not discriminating — the v7 reader already saw this cell"
    )
