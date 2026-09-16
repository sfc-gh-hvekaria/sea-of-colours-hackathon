"""Unit tests for the v12 ECHO-PURE hot drop.

Closes the gap where a pure we SAW, lost vision of, cannot walk to, and which
sits under no live redsign had no menu entry — while OUT-OF-GRID told the agent
to hot-drop it. These tests pin both halves: the option appears when (and only
when) that case is live, and its geometry keeps the probe alive.
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import value_pyramid
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.probe_hints import _covers


def _view(*, red=(), live=(), probe_stock=2, harvesters=1, **extra):
    """Minimal agent_view. ``red`` rows are (x, y, purity, freshness)."""
    assets = [
        {"kind": "harvester", "id": f"h{i}", "state": "orbit"}
        for i in range(harvesters)
    ]
    base = {
        "world": {
            "width": 40, "height": 28,
            "live": [{"x": x, "y": y, "tile": "NONE"} for x, y in live],
            "echo": [],
        },
        "red_tiles": [
            {"x": x, "y": y, "purity": p, "freshness": f}
            for x, y, p, f in red
        ],
        "entities": {"mine": []},
        "my_assets": assets,
        "probe_stock": probe_stock,
    }
    base.update(extra)
    return base


def _echo_pure(x=20, y=20):
    return _view(red=((x, y, 255, "stale"),))


# ── the option appears for the uncovered case ───────────────────────────
def test_echo_pure_out_of_vision_gets_a_hot_drop():
    hints = value_pyramid.force_surface_echo_hotdrops(_echo_pure())
    assert len(hints) == 1
    h = hints[0]
    assert h["signal_type"] == "echo-pure"
    assert h["exact_cell"] == [20, 20]


def test_probe_disk_actually_covers_the_pure():
    h = value_pyramid.force_surface_echo_hotdrops(_echo_pure())[0]
    px, py = h["probe_at"]
    assert _covers(px, py, 20, 20)


def test_probe_is_offset_so_the_landing_does_not_crush_it():
    h = value_pyramid.force_surface_echo_hotdrops(_echo_pure())[0]
    assert h["probe_at"] != h["drop_at"]


def test_drop_lands_on_the_pure_when_probe_is_offset():
    h = value_pyramid.force_surface_echo_hotdrops(_echo_pure())[0]
    assert h["drop_at"] == [20, 20]


def test_hint_names_a_real_orbit_harvester():
    h = value_pyramid.force_surface_echo_hotdrops(_echo_pure())[0]
    assert h["unit"] == "h0"


# ── and NOT for the cases already covered elsewhere ─────────────────────
def test_live_pure_is_left_to_the_grab_builder():
    view = _view(red=((20, 20, 255, "fresh"),))
    assert value_pyramid.force_surface_echo_hotdrops(view) == []


def test_pure_still_in_vision_is_left_to_the_grab_builder():
    view = _view(red=((20, 20, 255, "stale"),), live=((20, 20),))
    assert value_pyramid.force_surface_echo_hotdrops(view) == []


def test_a_nearby_redsign_does_NOT_suppress_the_hot_drop():
    """Beacon proximity is not a coverage test.

    A redsign is minted the first time any seat sees a pure and persists for the
    whole season, so essentially every echo pure has a beacon within a few cells.
    Suppressing on proximity made this option permanently inert (measured 0/18
    real seat-states); only ACTUAL coverage may suppress it.
    """
    view = _echo_pure()
    view["redsign"] = [{"center": [22, 21], "mine": True, "day": 3}]
    assert len(value_pyramid.force_surface_echo_hotdrops(view)) == 1


def test_cell_a_seam_wave_already_walks_is_suppressed():
    hints = value_pyramid.force_surface_echo_hotdrops(
        _echo_pure(), existing_targets={(20, 20)},
    )
    assert hints == []


def test_echo_mass_is_offered_too():
    """R2.9 — this used to be pure-only, so echo MASS had no id anywhere: the
    grab builder was LIVE-only for mass and seam_control is beacon-only (mass
    mints no beacon). OUT-OF-GRID named it and nothing could take it."""
    view = _view(red=((20, 20, 200, "stale"),))
    hints = value_pyramid.force_surface_echo_hotdrops(view)
    assert len(hints) == 1
    assert hints[0]["signal_type"] == "echo-mass"
    assert hints[0]["exact_cell"] == [20, 20]
    assert "mass p200" in hints[0]["note"]


def test_a_pure_is_still_preferred_over_richer_looking_mass():
    view = _view(red=((20, 20, 254, "stale"), (25, 25, 255, "stale")))
    hints = value_pyramid.force_surface_echo_hotdrops(view, max_hints=1)
    assert hints[0]["exact_cell"] == [25, 25]
    assert hints[0]["signal_type"] == "echo-pure"


def test_vein_and_trace_echoes_are_still_beneath_the_floor():
    """Mass is the floor, not "anything red" — a trace echo is not worth a probe."""
    assert value_pyramid.force_surface_echo_hotdrops(
        _view(red=((20, 20, 150, "stale"),))
    ) == []


# ── budget gates ────────────────────────────────────────────────────────
def test_no_probe_stock_means_no_hot_drop():
    assert value_pyramid.force_surface_echo_hotdrops(
        _view(red=((20, 20, 255, "stale"),), probe_stock=0)
    ) == []


def test_no_orbit_harvester_means_no_hot_drop():
    assert value_pyramid.force_surface_echo_hotdrops(
        _view(red=((20, 20, 255, "stale"),), harvesters=0)
    ) == []


def test_max_hints_is_respected():
    view = _view(red=(
        (20, 20, 255, "stale"), (5, 5, 255, "stale"), (30, 10, 255, "stale"),
    ))
    assert len(value_pyramid.force_surface_echo_hotdrops(view, max_hints=2)) == 2


# ── the coverage test the dedup relies on ───────────────────────────────
def _pattern(waves):
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12.seam_control import (
        SeamPattern,
    )

    return SeamPattern(
        pattern_id="P", kind="own", beacon=(1, 1), mine=True,
        title="t", when="now", rationale="r", waves=waves,
    )


def _wave(drop, comb=(), deny_only=False):
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12.seam_control import (
        SeamWave,
    )

    return SeamWave(
        wave=1, earliest_hour=1, drop_at=drop,
        comb_path=list(comb), deny_only=deny_only,
    )


def test_planned_harvest_cells_unions_drop_and_walk():
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control

    cells = seam_control.planned_harvest_cells(
        [_pattern([_wave((3, 3), [(4, 3), (5, 3)])])]
    )
    assert cells == {(3, 3), (4, 3), (5, 3)}


def test_planned_harvest_cells_ignores_deny_only_waves():
    """A deny-only wave spends a probe and lands no harvester, so it covers nothing."""
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control

    cells = seam_control.planned_harvest_cells(
        [_pattern([_wave((3, 3), [(4, 3)], deny_only=True)])]
    )
    assert cells == set()


# ── it reaches the menu ─────────────────────────────────────────────────
def test_hint_compiles_into_a_menu_option():
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import agency

    view = _echo_pure()
    hints = value_pyramid.force_surface_echo_hotdrops(view)
    reg = agency.build_registry(agent_view=view, hot_drop_hints=hints)
    ids = [i for i in reg if i.startswith("HD1")]
    assert ids, list(reg)
    hd = reg[ids[0]]
    assert hd.kind == "hotdrop"
    assert "echo-pure" in hd.detail


def test_echo_hotdrop_survives_alongside_seam_patterns():
    """Redsign HDs are stripped when seam patterns exist; echo-pure must not be."""
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import agency
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12.seam_control import (
        SeamPattern,
    )

    view = _echo_pure()
    hints = value_pyramid.force_surface_echo_hotdrops(view)
    pattern = SeamPattern(
        pattern_id="SMASH_GRAB", kind="own", beacon=(1, 1), mine=True,
        title="t", when="now", rationale="r",
    )
    reg = agency.build_registry(
        agent_view=view, seam_patterns=[pattern], hot_drop_hints=hints,
    )
    assert any(i.startswith("HD1") for i in reg), list(reg)
