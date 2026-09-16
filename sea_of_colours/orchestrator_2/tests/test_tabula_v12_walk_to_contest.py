"""v12 — WALK_TO_CONTEST: reaching a RIVAL seam on foot, with no probe.

A rival's redsign normally needs a fresh probe (blind the finder, light the
fogged pure), so an empty magazine means the seam is correctly ignored. The
exception these tests pin is the rare seam that broadcasts within a few steps of
ground we already light: the DROP needs live coverage but STEPS do not, so we
can land on our own frontier and walk in for free.

Pins: the zero-probe rescue, that a far seam is still (correctly) disregarded,
zero probe cost, stacking with the probe-funded attacks when stock exists, and
the short/long budget split.
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control as sc

BEACON = (10, 10)


def _view(*, live=(), probe_stock=0, mine=False):
    """A rival seam at BEACON, with ``live`` cells as our only lit ground."""
    return {
        "world": {
            "width": 30, "height": 30, "fog_count": 800,
            "live": [{"x": x, "y": y} for (x, y) in live],
        },
        "orbit": {"probe_stock": probe_stock},
        "my_assets": [
            {"id": "harvester_p1_1", "kind": "harvester", "state": "orbit"},
            {"id": "harvester_p1_2", "kind": "harvester", "state": "orbit"},
        ],
        "redsign": [{
            "center": list(BEACON),
            "cells": [[10, 10, 1.0]],
            "hour": 3,
            "mine": mine,
        }],
        "blue_tiles": [], "red_tiles": [], "last_night": {},
    }


def _near_live():
    """Lit ground on TWO bearings, each within a short walk of the beacon."""
    return [(10, 12), (11, 12), (9, 12), (10, 13),   # south
            (12, 10), (12, 9), (13, 10)]             # east


def _far_live():
    """A lit patch far out of walking range of the beacon."""
    return [(28, 28), (27, 28), (28, 27)]


def _ids(patterns):
    return [p.pattern_id for p in patterns]


def _by_id(patterns, pid):
    for p in patterns:
        if p.pattern_id == pid:
            return p
    raise AssertionError(f"{pid} not in {_ids(patterns)}")


# ── the zero-probe rescue ───────────────────────────────────────────────
def test_close_rival_seam_is_walkable_with_no_probes():
    pats = sc.build_seam_menu(_view(live=_near_live()), [], probe_stock=0)
    assert "WALK_TO_CONTEST" in _ids(pats)


def test_far_rival_seam_is_still_disregarded_with_no_probes():
    # The common case: nothing can be done, and offering geometry the packager
    # cannot emit would be worse than staying silent.
    pats = sc.build_seam_menu(_view(live=_far_live()), [], probe_stock=0)
    assert pats == []


def test_walk_in_costs_no_probe_and_survives_the_budget_filter():
    pats = sc.build_seam_menu(_view(live=_near_live()), [], probe_stock=0)
    walk = _by_id(pats, "WALK_TO_CONTEST")
    assert sc._pattern_probe_cost(walk) == 0
    for w in walk.waves:
        assert w.probe_at is None and w.supersede is None


def test_walk_in_drops_on_live_ground_and_blind_walks_the_fog():
    pats = sc.build_seam_menu(_view(live=_near_live()), [], probe_stock=0)
    wave = _by_id(pats, "WALK_TO_CONTEST").waves[0]
    assert tuple(wave.drop_at) in set(_near_live())
    # steps are not vision-gated, so the packager must not truncate them.
    assert wave.blind_walk is True


# ── stacking with the probe attacks ─────────────────────────────────────
def test_with_probes_the_walk_in_is_offered_alongside_blind_grab():
    pats = sc.build_seam_menu(_view(live=_near_live(), probe_stock=3), [],
                              probe_stock=3)
    ids = _ids(pats)
    # Fix 1.2 split the blind attack into a long comb and a short grab; which
    # of the two survives depends on the disk geometry, so assert on the family.
    assert {"BLIND_GRAB", "BLIND_AND_GRAB"} & set(ids), (
        "probe attack must not be displaced"
    )
    assert "WALK_TO_CONTEST" in ids, "free walk-in should stack with it"


def test_with_probes_the_rationale_still_points_at_vision_first():
    pats = sc.build_seam_menu(_view(live=_near_live(), probe_stock=3), [],
                              probe_stock=3)
    why = _by_id(pats, "WALK_TO_CONTEST").rationale
    assert "still have probes" in why
    assert "BLIND_GRAB" in why and "ALONGSIDE" in why


def test_without_probes_the_rationale_says_it_is_the_only_way_in():
    pats = sc.build_seam_menu(_view(live=_near_live()), [], probe_stock=0)
    assert "NO probes" in _by_id(pats, "WALK_TO_CONTEST").rationale


# ── the short / long budget split ───────────────────────────────────────
def test_short_variant_reserves_hold_capacity_for_the_comb():
    # Steps harvest as they go, so arriving with a full hold would leave nothing
    # to comb with — and the comb is what finds a jittered pure.
    pats = sc.build_seam_menu(_view(live=_near_live()), [], probe_stock=0)
    wave = _by_id(pats, "WALK_TO_CONTEST").waves[0]
    path = [tuple(c) for c in wave.comb_path]
    assert BEACON in path, "the walk must actually reach the smear"
    reach = path.index(BEACON) + 1
    assert reach <= sc._WALK_CONTEST_NEAR
    assert len(path) - reach >= 1, "hold capacity must be left to comb with"
    assert len(path) <= sc._HOLD_CAP_STEPS


def test_long_variant_enters_on_a_different_bearing_as_a_second_unit():
    pats = sc.build_seam_menu(_view(live=_near_live()), [], probe_stock=0)
    near = _by_id(pats, "WALK_TO_CONTEST").waves[0]
    far = _by_id(pats, "WALK_TO_CONTEST_FAR").waves[0]
    assert tuple(far.drop_at) != tuple(near.drop_at)
    assert far.direction != near.direction, "must not stack on one track"
    assert far.unit_ordinal != near.unit_ordinal
    assert far.earliest_hour > near.earliest_hour, "entries must be staggered"


def test_no_second_entry_when_all_lit_ground_shares_one_bearing():
    # Two units down the same corridor is a self-collision, not pressure.
    south_only = [(10, 12), (11, 12), (9, 12), (10, 13)]
    pats = sc.build_seam_menu(_view(live=south_only), [], probe_stock=0)
    ids = _ids(pats)
    assert "WALK_TO_CONTEST" in ids
    assert "WALK_TO_CONTEST_FAR" not in ids


# ── no collateral damage to the existing cases ──────────────────────────
def test_own_seam_is_untouched_by_the_rival_walk_in():
    pats = sc.build_seam_menu(_view(live=_near_live(), mine=True), [],
                              probe_stock=3)
    ids = _ids(pats)
    assert not any(i.startswith("WALK_TO_CONTEST") for i in ids)
