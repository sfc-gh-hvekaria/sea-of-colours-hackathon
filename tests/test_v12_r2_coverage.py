"""R2.5 — a seam wave launches a covering probe only when it buys something.

Anchored on `V12_HEUR3_GO_s56` night 2 (session `0a6a5ebe…`): the BLIND_GRAB wave
shipped a supersede at (29,8) AND a "triangulation" probe at (29,9) to make one
drop at (30,9) legal, while a third friendly probe at (28,10) was already burning
over the same ground. Three launches were budgeted against a stock of two, so the
separately chosen SS option was silently shed.

A probe earns its launch by making the drop legal or by killing a rival probe.
Anything else is coverage the wave already has.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control as sc

_W = _H = 40
_BEACON = (30, 9)
_DROP = (30, 9)


def _view(
    *,
    own_probes: List[Tuple[int, int]],
    enemy_probe: Optional[Tuple[int, int]] = (29, 8),
    pure_visible: bool = False,
) -> Dict[str, Any]:
    """A rival seam. ``own_probes`` are live friendly disks already on the board."""
    red: List[Dict[str, Any]] = [
        {"x": _BEACON[0] + dx, "y": _BEACON[1] + dy, "purity": 180,
         "freshness": "fresh"}
        for dx in range(-2, 3) for dy in range(-2, 3)
    ]
    if pure_visible:
        red.append({"x": _DROP[0], "y": _DROP[1], "purity": 255,
                    "freshness": "fresh"})
    # An enemy launch is PUBLIC (§3.15) and reaches the harness through
    # competitor_intel, not the entity list.
    enemy: List[Dict[str, Any]] = []
    if enemy_probe is not None:
        enemy.append({
            "kind": "enemy_probe_launch",
            "at": [enemy_probe[0], enemy_probe[1]],
            "day_seen": 2,
        })
    return {
        "competitor_intel": {"new_this_day": enemy},
        "day": 2,
        "world": {
            "width": _W, "height": _H,
            "live": [{"x": r["x"], "y": r["y"], "tile": "RED",
                      "purity": r["purity"]} for r in red],
        },
        "red_tiles": red,
        "redsign": [{"center": list(_BEACON), "mine": False, "found_day": 2,
                     "cells": []}],
        "entities": {"mine": [
            {"type": "probe", "pos": [p[0], p[1]], "nights_remaining": 2}
            for p in own_probes
        ]},
        "my_assets": (
            [{"kind": "probe", "state": "deployed", "x": p[0], "y": p[1],
              "nights_left": 2} for p in own_probes]
            + [{"kind": "harvester", "state": "orbit", "id": "harvester_p1"},
               {"kind": "harvester", "state": "orbit", "id": "harvester_p1_2"}]
        ),
        "probe_stock": 2,
        "last_night": {"incoming_attacks": [], "emp_scars": []},
    }


# ── the rule, unit level ────────────────────────────────────────────────────
def test_a_probe_is_dropped_when_a_burning_friendly_disk_already_lights_the_drop():
    view = _view(own_probes=[(28, 10)])
    assert sc._trim_redundant_probe(view, _DROP, (29, 9), None) is None


def test_a_probe_is_dropped_when_this_waves_supersede_will_light_the_drop():
    """The supersede lands first and its own r4 disk swallows the drop cell."""
    view = _view(own_probes=[])
    assert sc._trim_redundant_probe(view, _DROP, (29, 9), (29, 8)) is None


def test_a_probe_that_kills_a_rival_probe_keeps_its_launch():
    """Denial is worth a probe on its own, covered or not."""
    view = _view(own_probes=[(28, 10)], enemy_probe=(29, 9))
    assert sc._trim_redundant_probe(view, _DROP, (29, 9), None) == (29, 9)


def test_the_only_probe_that_makes_the_drop_legal_survives():
    view = _view(own_probes=[], enemy_probe=None)
    assert sc._trim_redundant_probe(view, _DROP, (29, 9), None) == (29, 9)


def test_echo_only_coverage_is_not_mistaken_for_a_friendly_disk():
    """A far friendly probe does not cover the drop, so the launch is still real."""
    view = _view(own_probes=[(10, 10)], enemy_probe=None)
    assert sc._trim_redundant_probe(view, _DROP, (29, 9), None) == (29, 9)


# ── the rule, as the menu actually ships it ─────────────────────────────────
def _rival(view: Dict[str, Any]) -> Dict[str, sc.SeamPattern]:
    pats = sc.build_seam_menu(view, [], probe_stock=2)
    return {p.pattern_id: p for p in pats if not p.mine}


def _wave_probes(p: sc.SeamPattern) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for w in p.waves:
        for c in (w.probe_at, w.supersede):
            if c is not None:
                out.append((int(c[0]), int(c[1])))
    return out


def test_the_seen_grab_under_a_friendly_probe_spends_no_probe_at_all():
    """s69 night 4: the pure was visible under our own disk and the wave still
    launched two probes, which pushed the drop from H01 to H03 and straight into
    a rival landing on the same cell."""
    pats = _rival(_view(own_probes=[_DROP], pure_visible=True, enemy_probe=None))
    seen = pats.get("SEEN_GRAB")
    assert seen is not None, sorted(pats)
    assert _wave_probes(seen) == [], (
        f"SEEN_GRAB under live vision still launches {_wave_probes(seen)}"
    )


def test_no_rival_wave_double_probes_one_drop():
    for pats in (
        _rival(_view(own_probes=[(28, 10)])),
        _rival(_view(own_probes=[])),
    ):
        for pid, p in pats.items():
            for w in p.waves:
                if w.deny_only or w.drop_at is None:
                    continue
                if w.probe_at is None or w.supersede is None:
                    continue
                drop = (int(w.drop_at[0]), int(w.drop_at[1]))
                assert not sc._covers(
                    int(w.supersede[0]), int(w.supersede[1]), drop[0], drop[1]
                ), (
                    f"{pid} wave {w.wave}: supersede {tuple(w.supersede)} already "
                    f"lights {drop}, so probe {tuple(w.probe_at)} buys nothing"
                )
