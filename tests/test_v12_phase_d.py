"""Phase D doctrine contracts — tabula_v12.

Phase D is instruction rather than geometry, so most of it cannot be asserted
without asking an LLM. What CAN be pinned is delivery: does the text reach the
card, does the datum it hangs on compute correctly, and does the one new OPTION
appear on exactly the boards that justify it. That is what this file covers.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.orbit_wishlist import Wishlist
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import agency, doctrine, prompt
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import option_economics as oe
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control as sc

_PURE = (16, 6)


def _view(
    *,
    day: int = 6,
    day_cap: int = 9,
    my_probe: Any = (18, 6),
    rival_probes: List[Dict[str, Any]] | None = None,
    opponents: List[str] = ("p2",),
    chaff_flare: bool = False,
) -> Dict[str, Any]:
    mine: List[Dict[str, Any]] = []
    if my_probe is not None:
        mine.append({"type": "probe", "pos": list(my_probe), "nights_remaining": 3})
    return {
        "meta": {"player": "p1", "day": day},
        "hud": {"season_day_cap": day_cap},
        "day": day,
        "grid": {"width": 40, "height": 24},
        "world": {"width": 40, "height": 24, "live": []},
        "red_tiles": [{"x": _PURE[0], "y": _PURE[1], "purity": 255,
                       "freshness": "fresh"}],
        "redsign": [{"center": list(_PURE), "pure_cell": list(_PURE),
                     "mine": True, "cells": [list(_PURE)], "found_day": day - 1}],
        "entities": {"mine": mine, "echoes": []},
        "opponents": [{"player": p} for p in opponents],
        "competitor_intel": {
            "new_this_day": rival_probes or [], "persistent_echoes": [],
        },
        "combat_events": ([{"type": "chaff", "by": ["p2"]}] if chaff_flare else []),
    }


def _rival_probe(at, owner="p2", day_seen=5) -> Dict[str, Any]:
    return {"kind": "enemy_probe_launch", "owner": owner, "at": list(at),
            "day_seen": day_seen}


# ── fix 3.1 — the rationale finally ships ──────────────────────────────
def test_a_seam_options_argument_reaches_the_menu_text():
    """Every pattern has carried a written rationale since v10 and none of it
    has ever been rendered, so each play arrived as bare geometry."""
    pattern = sc.SeamPattern(
        pattern_id="X", kind="X", beacon=_PURE, mine=True,
        title="t", when="w", rationale="the argument nobody ever read",
        waves=[sc.SeamWave(1, 1, [16, 6], [], probe_at=None, direction="E",
                           unit_ordinal=0)],
    )
    opt = agency._seam_option(pattern)
    assert opt.rationale == "the argument nobody ever read"
    block = agency.format_menu_block({opt.option_id: opt}, agent_view=None)
    assert "WHY: the argument nobody ever read" in block


def test_an_option_with_nothing_to_argue_adds_no_empty_line():
    opt = agency.Option(option_id="Y", kind="probe", title="t", detail="d")
    assert "WHY:" not in agency.format_menu_block({"Y": opt}, agent_view=None)


# ── fix 3.2 — the certainty frame ──────────────────────────────────────
def test_the_certainty_block_ships_on_a_redsign_night():
    text = prompt._assemble_doctrine(
        agent_view=_view(), day=6, day_cap=9, hot_drop_hints=[],
        wishlist=Wishlist(), opponent_weapon_estimates=None, supersede_hints=[],
        is_setup_night=False,
    )
    assert "WHAT IS CERTAIN AND WHAT IS A WAGER" in text
    # The three facts the frame exists to deliver.
    assert "THE FIRST-HOUR DROP IS THE ONLY CERTAINTY" in text
    assert "STAKES THE WHOLE HOLD" in text
    assert "NEVER RETRACTED" in text


def test_the_hour_notation_is_stated_once_and_unambiguously():
    """R2.7 — H<n> was doing double duty as an hour and as a harvester ordinal,
    and a wave titled "H2" actually opened at hour 9."""
    text = prompt._assemble_doctrine(
        agent_view=_view(), day=6, day_cap=9, hot_drop_hints=[],
        wishlist=Wishlist(), opponent_weapon_estimates=None, supersede_hints=[],
        is_setup_night=False,
    )
    assert "H01..H24 is the HOUR of the night, always" in text
    assert 'It never means "harvester' in text


def test_the_certainty_block_stays_off_a_quiet_night():
    """It is long, and on a board with no beacon there is no wager to frame."""
    quiet = _view()
    quiet["redsign"] = []
    text = prompt._assemble_doctrine(
        agent_view=quiet, day=6, day_cap=9, hot_drop_hints=[],
        wishlist=Wishlist(), opponent_weapon_estimates=None, supersede_hints=[],
        is_setup_night=False,
    )
    assert "WHAT IS CERTAIN AND WHAT IS A WAGER" not in text


def test_superseding_is_named_as_a_tomorrow_move_not_a_tonight_move():
    """The natural and wrong inference the frame exists to block."""
    assert "DOES NOT STOP THEM DROPPING TONIGHT" in doctrine.DOCTRINE_CERTAINTY


# ── fix 3.3 — who holds H1 ─────────────────────────────────────────────
def test_a_live_disk_over_the_pure_means_that_seat_holds_h1():
    v = _view(rival_probes=[_rival_probe((17, 7))])
    seats = oe.time_to_pure(v, _PURE, day=6)
    assert seats["you"] == "H1"
    assert seats["p2"] == "H1"


def test_a_seat_with_no_probe_on_the_seam_is_an_hour_behind():
    v = _view(rival_probes=[_rival_probe((35, 20))])
    assert oe.time_to_pure(v, _PURE, day=6)["p2"] == "H2"


def test_a_seat_we_have_no_intel_on_is_still_listed():
    """Absence of intel is not absence of a rival — they can launch too."""
    v = _view(rival_probes=[], opponents=("p2", "p3"))
    seats = oe.time_to_pure(v, _PURE, day=6)
    assert seats["p2"] == "H2" and seats["p3"] == "H2"


def test_an_expired_rival_probe_does_not_buy_them_h1():
    v = _view(rival_probes=[_rival_probe((17, 7), day_seen=0)])
    assert oe.time_to_pure(v, _PURE, day=6)["p2"] == "H2"


def test_our_own_seat_is_never_listed_as_an_opponent():
    v = _view(rival_probes=[_rival_probe((17, 7), owner="p1")])
    assert list(oe.time_to_pure(v, _PURE, day=6)).count("p1") == 0


def test_the_tempo_line_carries_the_floor_that_stops_it_reading_as_concede():
    """A tempo line without this sentence teaches the agent to cede pures."""
    lines = "\n".join(prompt._tempo_lines(_view(), 6))
    assert "SIZES YOUR COMMITMENT" in lines
    assert "ALWAYS commit at least one harvester" in lines


# ── fix 3.4 — the repeat is chaff insurance, nothing else ──────────────
def _own_patterns(v) -> Dict[str, sc.SeamPattern]:
    return {p.pattern_id: p for p in sc.build_seam_menu(
        v, [{"signal_type": "redsign", "drop_at": list(_PURE),
             "probe_at": [18, 6], "mine": True}], probe_stock=2,
    )}


def test_no_second_bite_is_offered_on_a_chaff_free_board():
    """There it is a -100 walk onto your own green and nothing else."""
    assert "CHAFF_INSURANCE" not in _own_patterns(_view())


def test_chaff_in_play_mints_the_second_bite_at_the_same_cell():
    pats = _own_patterns(_view(chaff_flare=True))
    assert "CHAFF_INSURANCE" in pats
    ins, smash = pats["CHAFF_INSURANCE"], pats["SMASH_GRAB"]
    assert list(ins.waves[0].drop_at) == list(smash.waves[0].drop_at)
    assert ins.waves[0].earliest_hour > smash.waves[0].earliest_hour
    assert ins.waves[0].unit_ordinal != smash.waves[0].unit_ordinal


def test_a_flare_aimed_at_someone_else_still_counts_as_chaff_in_play():
    """`it jammed US last night` is too narrow: a launch proves the stock, and
    the launch is public."""
    v = _view(chaff_flare=True)
    v["last_night"] = {"incoming_attacks": []}
    assert sc._threat_context(v)["chaff_in_play"] is True
    assert sc._threat_context(v)["chaff_seen"] is False


def test_the_second_bite_needs_us_to_actually_hold_h1():
    """Without an H1 drop we are already contingent and insure nothing."""
    v = _view(my_probe=None, chaff_flare=True)
    assert "CHAFF_INSURANCE" not in _own_patterns(v)


def test_the_pairing_rule_is_stated_in_the_playbook():
    assert "ONE FROM EACH COLUMN" in doctrine.DOCTRINE_REDSIGN_POKER
    assert "KEYED ON CHAFF ALONE" in doctrine.DOCTRINE_REDSIGN_POKER


# ── fix 3.5 — the ladder, DROP BLOCK, mutual visibility ────────────────
def test_every_rung_of_the_ladder_names_a_unit_count():
    ladder = doctrine.DOCTRINE_RISK_LADDER
    for rung in ("LOW", "MED", "HIGH", "VERY HIGH", "ULTRA HIGH"):
        assert rung in ladder
    assert "ONE unit is enough" in ladder
    assert "TWO UNITS ON THE SAME CELL" in ladder


def test_the_ladder_keeps_commitment_and_chain_length_apart():
    """Collapsing them is how the safest board in the suite got played with the
    barest option on the menu: the redsign floor read as a reason to flinch."""
    ladder = doctrine.DOCTRINE_RISK_LADDER
    assert "IT DOES NOT SET YOUR CHAIN LENGTH" in ladder
    assert "shortens NOTHING" in ladder


def test_a_blind_unarmed_board_is_told_the_high_is_only_the_floor():
    v = _view(day=2, day_cap=9, rival_probes=[])
    level, reason = oe.collision_risk([_PURE], v, day=2)
    assert level == "HIGH"
    assert "redsign floor and nothing else" in reason
    assert "take the full sweep" in reason


def test_a_watched_cell_gets_no_such_reassurance():
    v = _view(rival_probes=[_rival_probe((17, 7))])
    assert "redsign floor and nothing else" not in oe.collision_risk(
        [_PURE], v, day=6,
    )[1]


def test_live_rival_eyes_on_the_pure_surface_the_drop_block():
    v = _view(rival_probes=[_rival_probe((17, 7))])
    _level, reason = oe.collision_risk([_PURE], v, day=6)
    assert "DROP BLOCK is available" in reason
    assert "SPILLS their hold" in reason
    assert "500 credits" in reason


def test_a_blind_rival_gets_no_drop_block_line():
    """They cannot land on a cell they cannot see, so the play does not apply."""
    v = _view(rival_probes=[_rival_probe((35, 20))])
    assert "DROP BLOCK" not in oe.collision_risk([_PURE], v, day=6)[1]


def test_vision_plus_weapons_plus_a_crowd_lifts_the_level_past_high():

    class _Armed:
        emps_max, chaff_max = 1, 0

    v = _view(rival_probes=[_rival_probe((17, 7)), _rival_probe((17, 5), "p3")],
              opponents=("p2", "p3"))
    level, _ = oe.collision_risk([_PURE], v, {"p2": _Armed()}, day=6)
    assert level in ("VERY HIGH", "ULTRA HIGH")


def test_the_late_crowded_watched_board_is_the_top_rung():
    v = _view(day=8, day_cap=9, opponents=("p2", "p3"),
              rival_probes=[_rival_probe((17, 7), day_seen=8),
                            _rival_probe((17, 5), "p3", day_seen=8)])
    assert oe.collision_risk([_PURE], v, day=8)[0] == "ULTRA HIGH"


def test_an_early_quiet_seam_stays_at_the_redsign_floor():
    v = _view(day=2, day_cap=9, rival_probes=[])
    assert oe.collision_risk([_PURE], v, day=2)[0] == "HIGH"


def test_case_three_now_reasons_about_what_they_can_reach():
    poker = doctrine.DOCTRINE_REDSIGN_POKER
    assert "MUTUAL VISIBILITY" in poker
    assert "DOUBLE SMASH" in poker
    assert "BLIND ONE, DOUBLE-DROP THE OTHER" in poker
    assert "NO BRANCH IN WHICH THEY TAKE BOTH" in poker
