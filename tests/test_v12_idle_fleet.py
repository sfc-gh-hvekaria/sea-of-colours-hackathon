"""A harvester must never be offered a menu it cannot play — tabula_v12.

Anchored on Caelum_Compass (`7830d381…`) night 6, seat p1. A rival redsign was
live, so the seam filter suppressed both juice chains; probe stock was 0, so
every hot drop and probe option on the menu read UNAFFORDABLE. Three harvesters
were left with exactly ONE playable option between them.

The think pass saw the problem and named the plays it wanted — two lone veins
under its own probes — in prose. It could not select them, because those cells
had no menu id and ``resolve_plan`` accepts ids only. Two thirds of a correct
plan evaporated between the passes and two harvesters banked nothing.

Two guards close it, and this file pins both:
  * the VEIN rung of the value pyramid, so an isolated vein is offerable at all;
  * the idle-fleet backstop in ``build_registry``, which puts seam-suppressed
    chains back when nothing else can field the fleet.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import agency
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control as sc
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import value_pyramid as vp

_W = _H = 40


def _view(
    red: List[Tuple[int, int, int]],
    *,
    probe_stock: int = 0,
    harvesters: int = 3,
) -> Dict[str, Any]:
    """A board whose red cells are all live and drop-legal."""
    cells = [{"x": x, "y": y, "tile": "RED", "purity": p} for x, y, p in red]
    assets: List[Dict[str, Any]] = [
        {"kind": "probe", "state": "deployed", "x": 36, "y": 20, "nights_left": 2},
    ]
    for i in range(harvesters):
        hid = "harvester_p1" if i == 0 else f"harvester_p1_{i + 1}"
        assets.append({"kind": "harvester", "state": "orbit", "id": hid})
    return {
        "day": 6,
        "world": {"width": _W, "height": _H, "live": cells},
        "red_tiles": [
            {"x": x, "y": y, "purity": p, "freshness": "fresh"} for x, y, p in red
        ],
        "entities": {"mine": [
            {"type": "probe", "pos": [36, 20], "nights_remaining": 2},
        ]},
        "my_assets": assets,
        "orbit": {"probe_stock": probe_stock},
        "probe_stock": probe_stock,
        "last_night": {"incoming_attacks": [], "emp_scars": []},
    }


def _actions(specs) -> List[str]:
    return [s.action for s in specs]


# ── the VEIN rung ───────────────────────────────────────────────────────
def test_a_lone_vein_is_offered_to_a_harvester_that_would_otherwise_idle():
    """Too poor for the mass branch and too short to form a juice chain, so
    before this rung existed a visible vein had no surfacing path at all."""
    specs = vp.force_surface_grabs(
        _view([(35, 9, 70)]), harvesters_alive=3, strong_chain_count=0,
    )
    veins = [s for s in specs if s.action == "GRAB_VEIN"]
    assert veins, f"vein never surfaced; menu was {_actions(specs)}"
    assert veins[0].target == (35, 9)
    assert veins[0].drop_at == (35, 9), "a drop auto-harvests its own cell"
    assert veins[0].tier == "vein"


def test_the_vein_rung_stays_quiet_when_every_harvester_is_already_committed():
    """It is a floor for idle units, not a new default — a rich night must not
    have its menu padded with 70-point consolation prizes."""
    specs = vp.force_surface_grabs(
        _view([(35, 9, 70)]), harvesters_alive=2, strong_chain_count=2,
    )
    assert "GRAB_VEIN" not in _actions(specs)


def test_a_richer_tier_is_still_preferred_over_a_vein():
    """The pyramid keeps its ordering: mass is claimed before the vein rung is
    reached, and the vein never displaces it."""
    specs = vp.force_surface_grabs(
        _view([(35, 9, 70), (20, 20, 200)]),
        harvesters_alive=3, strong_chain_count=0,
    )
    actions = _actions(specs)
    assert "GRAB_MASS" in actions
    assert actions.index("GRAB_MASS") < actions.index("GRAB_VEIN")


def test_trace_is_still_beneath_the_floor():
    """The rung stops at a real vein (purity 51). Sending a harvester out for a
    handful of trace points is worse than holding it."""
    specs = vp.force_surface_grabs(
        _view([(35, 9, 12)]), harvesters_alive=3, strong_chain_count=0,
    )
    assert "GRAB_VEIN" not in _actions(specs)


# ── deploy capacity ─────────────────────────────────────────────────────
def _opt(oid: str, kind: str, **payload: Any) -> agency.Option:
    """A hot drop's probe cost lives in its ``probe_at`` payload, exactly as the
    real menu builds it — see ``packager._probe_demand``."""
    return agency.Option(
        option_id=oid, kind=kind, title=oid, detail="", payload=dict(payload),
    )


def _hotdrop(oid: str) -> agency.Option:
    return _opt(oid, "hotdrop", probe_at=[13, 19], drop_at=[13, 19])


def test_capacity_counts_what_the_seat_can_pay_for_not_what_is_printed():
    """The Caelum d6 miscount: six hot drops and one walk-in looked like seven
    deployable options, but with a probe stock of zero only one could fly."""
    reg = {
        "WALK_TO_CONTEST": _opt("WALK_TO_CONTEST", "grab", drop_at=[14, 23]),
        "HD1L": _hotdrop("HD1L"),
        "HD1T": _hotdrop("HD1T"),
        "HD2L": _hotdrop("HD2L"),
    }
    assert agency._deploy_capacity(reg, _view([], probe_stock=0)) == 1


def test_probe_funded_options_are_capped_by_stock_not_counted_each():
    """Two hot drops needing a probe each cannot both fly on a single probe."""
    reg = {"HD1": _hotdrop("HD1"), "HD2": _hotdrop("HD2"), "HD3": _hotdrop("HD3")}
    assert agency._deploy_capacity(reg, _view([], probe_stock=1)) == 1
    assert agency._deploy_capacity(reg, _view([], probe_stock=2)) == 2


def test_probe_only_options_never_count_as_deployable():
    """A probe placement banks nothing and commits no harvester."""
    reg = {"PR1": _opt("PR1", "probe"), "SS1": _opt("SS1", "supersede")}
    assert agency._deploy_capacity(reg, _view([], probe_stock=3)) == 0


# ── the idle-fleet backstop, end to end ─────────────────────────────────
_BEACON = (16, 6)


def _seam(probe_funded: bool = True) -> sc.SeamPattern:
    """A one-wave campaign on the beacon. Probe-funded by default, so a seat
    with no stock cannot actually run it — the Caelum d6 shape."""
    return sc.SeamPattern(
        pattern_id="SMASH_GRAB", kind="smash", beacon=_BEACON, mine=True,
        title="smash the seam", when="", rationale="",
        waves=[sc.SeamWave(
            wave=1, earliest_hour=1, drop_at=_BEACON,
            probe_at=_BEACON if probe_funded else None,
        )],
    )


def _seam_view(red, *, probe_stock=0, harvesters=3):
    v = _view(red, probe_stock=probe_stock, harvesters=harvesters)
    v["redsign"] = [{
        "center": list(_BEACON), "mine": True, "day": 4,
        "cells": [list(_BEACON)],
    }]
    return v


def _chain_ids(reg) -> List[str]:
    return [k for k, o in reg.items() if o.kind == "chain"]


def test_suppressed_chains_come_back_when_nothing_else_can_field_the_fleet():
    """Caelum d6 in miniature: the seam suppresses the chain, the seam itself is
    unaffordable at 0 probes, and three harvesters are left one playable option.
    Menu pressure loses to a harvester with nothing legal to do."""
    on_seam = {"drop_at": [16, 7], "cells": [[16, 7], [16, 8]], "length": 2,
               "purities": [90, 80]}
    reg = agency.build_registry(
        agent_view=_seam_view([], probe_stock=0),
        seam_patterns=[_seam()],
        chain_hints=[on_seam],
        harvesters_alive=3,
    )
    assert _chain_ids(reg), (
        "chain stayed suppressed while the fleet had nothing to do; menu was "
        f"{ {k: o.kind for k, o in reg.items()} }"
    )


def test_the_backstop_is_silent_when_the_menu_can_already_field_the_fleet():
    """It is a last resort. With the seam affordable and only one harvester to
    place, the chain is still a duplicate and must stay off the menu."""
    on_seam = {"drop_at": [16, 7], "cells": [[16, 7], [16, 8]], "length": 2,
               "purities": [90, 80]}
    reg = agency.build_registry(
        agent_view=_seam_view([], probe_stock=2, harvesters=1),
        seam_patterns=[_seam()],
        chain_hints=[on_seam],
        harvesters_alive=1,
    )
    assert not _chain_ids(reg), "menu pressure abandoned on a night that was fine"
