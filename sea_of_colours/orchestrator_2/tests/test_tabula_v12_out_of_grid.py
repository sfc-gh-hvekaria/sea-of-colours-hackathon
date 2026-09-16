"""Unit tests for the tabula_v12 OUT-OF-GRID KNOWLEDGE block.

Covers the three self-gating sections (RED SIGNS / BLUE SIGNS / ECHOES), the
sign-access read used by the contest-or-fold doctrine, and the echo filters
(tier floor, live-vision exclusion, retrieval routing, staleness flags).
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import out_of_grid


def _view(**extra):
    base = {
        "world": {"width": 40, "height": 28, "live": [], "echo": []},
        "entities": {"mine": []},
        "red_tiles": [],
    }
    base.update(extra)
    return base


def _probe(x, y):
    return {"type": "probe", "pos": [x, y]}


def _echo(x, y, purity, last_seen_day, tile="RED"):
    return {
        "x": x, "y": y, "tile": tile,
        "purity": purity, "last_seen_day": last_seen_day,
    }


# ── gating ──────────────────────────────────────────────────────────────
def test_quiet_night_renders_nothing():
    assert out_of_grid.format_out_of_grid_block(_view(), day=3) == ""


def test_low_tier_echo_alone_renders_nothing():
    view = _view(world={"width": 40, "height": 28, "live": [],
                        "echo": [_echo(5, 5, 90, 2)]})
    assert out_of_grid.format_out_of_grid_block(view, day=3) == ""


# ── red signs ───────────────────────────────────────────────────────────
def test_red_sign_renders_centre_ownership_and_age():
    view = _view(redsign=[{"center": [18, 3], "mine": True, "day": 4}])
    text = out_of_grid.format_out_of_grid_block(view, day=6)
    assert "RED SIGNS" in text
    assert "~(18,3)" in text
    assert "YOURS" in text
    assert "found day 4 (~2n ago)" in text


def test_rival_sign_labelled_and_pure_economics_present():
    view = _view(redsign=[{"center": [9, 9], "mine": False, "day": 2}])
    text = out_of_grid.format_out_of_grid_block(view, day=3)
    assert "RIVAL" in text
    assert "almost ALWAYS be contested" in text
    assert "DENIAL IS A REAL PLAY" in text


def test_sign_centre_falls_back_to_mean_of_smear_cells():
    view = _view(redsign=[{"cells": [[10, 10, 1], [12, 10, 1], [11, 12, 1]]}])
    text = out_of_grid.format_out_of_grid_block(view, day=2)
    assert "~(11,11)" in text


def test_access_note_reads_my_probe_as_inside_track():
    view = _view(
        redsign=[{"center": [10, 10], "mine": True, "day": 1}],
        entities={"mine": [_probe(11, 11)]},
    )
    text = out_of_grid.format_out_of_grid_block(view, day=2)
    assert "YOURS x1" in text
    assert "inside track" in text


def test_access_note_flags_open_board_when_nobody_is_near():
    view = _view(redsign=[{"center": [35, 25], "mine": False, "day": 1}])
    text = out_of_grid.format_out_of_grid_block(view, day=2)
    assert "NOBODY" in text


# ── blue signs ──────────────────────────────────────────────────────────
def test_blue_signs_listed_and_capped():
    view = _view(blue_sign=[{"center": [i, i]} for i in range(1, 9)])
    text = out_of_grid.format_out_of_grid_block(view, day=3)
    assert "BLUE SIGNS" in text
    listed = [ln for ln in text.splitlines() if ln.strip().startswith("~(")]
    assert len(listed) == out_of_grid._BLUE_SIGN_MAX_ROWS


def test_blue_is_framed_as_secondary_to_red():
    view = _view(blue_sign=[{"center": [4, 4]}])
    text = out_of_grid.format_out_of_grid_block(view, day=3)
    assert "NOT your primary score" in text


# ── echoes ──────────────────────────────────────────────────────────────
def test_rich_echo_listed_with_tier_and_age():
    view = _view(world={"width": 40, "height": 28, "live": [],
                        "echo": [_echo(18, 2, 255, 6)]})
    text = out_of_grid.format_out_of_grid_block(view, day=8)
    assert "ECHOES" in text
    assert "(18,2) pure" in text
    assert "last seen day 6 (2n ago)" in text


def test_echo_still_in_live_vision_is_excluded():
    """A cell we can SEE belongs to WORLD VIEW, never to ECHOES."""
    view = _view(world={
        "width": 40, "height": 28,
        "live": [{"x": 18, "y": 2, "tile": "RED", "purity": 255}],
        "echo": [_echo(18, 2, 255, 6)],
    })
    assert out_of_grid.format_out_of_grid_block(view, day=8) == ""


def test_echo_touching_live_routes_to_walk_in():
    view = _view(world={
        "width": 40, "height": 28,
        "live": [{"x": 8, "y": 4, "tile": "NONE"}],
        "echo": [_echo(8, 5, 200, 7)],
    })
    text = out_of_grid.format_out_of_grid_block(view, day=8)
    assert "WALK IN from the disk edge" in text


def test_echo_outside_vision_routes_to_hot_drop():
    view = _view(world={"width": 40, "height": 28, "live": [],
                        "echo": [_echo(30, 20, 200, 7)]})
    text = out_of_grid.format_out_of_grid_block(view, day=8)
    assert "HOT DROP it" in text


def test_echo_under_rival_probe_is_flagged():
    view = _view(
        world={"width": 40, "height": 28, "live": [],
               "echo": [_echo(20, 20, 255, 7)]},
        competitor_intel={"new_this_day": [
            {"kind": "enemy_probe_launch", "at": [21, 21], "day_seen": 7},
        ]},
    )
    text = out_of_grid.format_out_of_grid_block(view, day=8)
    assert "a rival probe disk covers this cell" in text


def test_echoes_sorted_freshest_first():
    view = _view(world={"width": 40, "height": 28, "live": [], "echo": [
        _echo(1, 1, 255, 2),   # oldest
        _echo(2, 2, 200, 7),   # freshest
    ]})
    text = out_of_grid.format_out_of_grid_block(view, day=8)
    assert text.index("(2,2)") < text.index("(1,1)")


def test_echo_pure_is_called_the_best_target():
    view = _view(world={"width": 40, "height": 28, "live": [],
                        "echo": [_echo(18, 2, 255, 7)]})
    text = out_of_grid.format_out_of_grid_block(view, day=8)
    assert "know the EXACT square" in text


def test_blue_echo_is_not_listed_as_red_loot():
    view = _view(world={"width": 40, "height": 28, "live": [],
                        "echo": [_echo(5, 5, 255, 7, tile="BLUE")]})
    assert out_of_grid.format_out_of_grid_block(view, day=8) == ""


# ── placement ───────────────────────────────────────────────────────────
def test_block_renders_directly_below_world_view():
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import (
        prompt as prompt_mod,
    )

    view = _view(redsign=[{"center": [18, 3], "mine": True, "day": 4}])
    text = prompt_mod.build_prompt(
        mode="thinker", agent_view=view, day=6, day_cap=7, vault_score=0,
        memory_replay="(none)\n", chain_hints=[],
    )
    # Anchor on the block HEADERS: "WORLD VIEW" / "DROP-LEGAL ZONES" also
    # appear as prose inside earlier doctrine, so a bare find() is ambiguous.
    i_wv = text.find("WORLD VIEW — everything you can SEE")
    i_og = text.find("OUT-OF-GRID KNOWLEDGE — facts you KNOW")
    i_dl = text.find("DROP-LEGAL ZONES", i_og)
    assert -1 < i_wv < i_og < i_dl, (i_wv, i_og, i_dl)
