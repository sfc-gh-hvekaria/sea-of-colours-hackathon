"""R2.1 / R2.2 — the blue grab walks on blue, and is never mistaken for a red one.

Anchored on `V12_HEUR3_GO_s69` night 3 (session `4d63cf63…`): the only grab on the
menu was blue, so it came out as `GRAB1`, and doctrine's "PRIORITY RED GRABS …
grab it FIRST" read as an instruction to spend the first harvester on it. Its
route then padded two steps east across empty ground — `_mass_tail` blind-walking
a halo that only exists around red — which burned hours 2 and 3 and slid the
night's redsign pickup into p4's chaff window.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import value_pyramid as vp

_W = _H = 40


def _view(blue: List[Tuple[int, int, int]], red: List[Tuple[int, int, int]] = ()) -> Dict[str, Any]:
    """A board with the given blue/red cells, all live and all drop-legal."""
    cells = (
        [{"x": x, "y": y, "tile": "BLUE", "purity": p} for x, y, p in blue]
        + [{"x": x, "y": y, "tile": "RED", "purity": p} for x, y, p in red]
    )
    return {
        "day": 3,
        "world": {"width": _W, "height": _H, "live": cells},
        "blue_tiles": [
            {"x": x, "y": y, "purity": p, "freshness": "fresh"} for x, y, p in blue
        ],
        "red_tiles": [
            {"x": x, "y": y, "purity": p, "freshness": "fresh"} for x, y, p in red
        ],
        "entities": {"mine": [
            {"type": "probe", "pos": [36, 20], "nights_remaining": 2},
        ]},
        "my_assets": [
            {"kind": "probe", "state": "deployed", "x": 36, "y": 20,
             "nights_left": 2},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1"},
        ],
        "probe_stock": 1,
        "last_night": {"incoming_attacks": [], "emp_scars": []},
    }


def _blue_spec(view: Dict[str, Any]) -> vp.GrabSpec:
    specs = vp.force_surface_grabs(view, blue_requested=True)
    blues = [s for s in specs if s.action == "GRAB_BLUE"]
    assert blues, f"no blue grab surfaced from {[s.action for s in specs]}"
    return blues[0]


# ── R2.1 — the tail ─────────────────────────────────────────────────────────
def test_a_lone_blue_cell_gets_no_tail_at_all():
    """s69 night 3: (36,20) was a single deposit and the walk still ran east."""
    spec = _blue_spec(_view(blue=[(36, 20, 255)]))
    assert spec.cells == [], f"padded onto empty ground: {spec.cells}"


def test_the_tail_follows_adjacent_blue_and_stops_where_it_ends():
    spec = _blue_spec(_view(blue=[(36, 20, 255), (37, 20, 240)]))
    assert spec.cells == [(37, 20)]


def test_the_tail_takes_the_richest_neighbour_first():
    spec = _blue_spec(_view(blue=[
        (36, 20, 255), (37, 20, 200), (36, 21, 250), (36, 22, 240),
    ]))
    assert spec.cells[0] == (36, 21)


def test_the_tail_never_crosses_red():
    """Red beside blue is a red grab's business; the blue walk stays on blue."""
    spec = _blue_spec(_view(blue=[(36, 20, 255)], red=[(37, 20, 200)]))
    assert (37, 20) not in spec.cells


# ── R2.2 — the id family ────────────────────────────────────────────────────
def _menu_ids(view: Dict[str, Any]) -> Dict[str, str]:
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import agency

    reg = agency.build_registry(agent_view=view, blue_requested=True)
    return {oid: str(getattr(o, "kind", "")) for oid, o in reg.items()}


def test_a_blue_grab_is_never_called_grab1():
    ids = _menu_ids(_view(blue=[(36, 20, 255)]))
    blue_ids = [i for i, k in ids.items() if k == "blue_grab"]
    assert blue_ids, sorted(ids)
    assert all(i.startswith("BL") for i in blue_ids), blue_ids
    assert not any(i.startswith("GRAB") for i in blue_ids)


def test_red_still_owns_grab1_when_both_are_offered():
    ids = _menu_ids(_view(blue=[(36, 20, 255)], red=[(20, 20, 255)]))
    assert ids.get("GRAB1") == "grab", sorted(ids)
    assert ids.get("BL1") == "blue_grab", sorted(ids)
