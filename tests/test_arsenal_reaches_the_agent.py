"""v1.38 — the public arsenal, as the agent actually receives it.

``test_arsenal_public_and_capped.py`` pins that the engine broadcasts
every seat's weaponised blue exactly. That is one end of a wire. This
file pins the other end: what a harness makes of the number once it has
it, and whether the sentence the model reads is true.

Three failures lived in the gap between those ends, and none of them
would have been caught by either side on its own.

**A seat holding 100 blue was invisible.** The arsenal block filtered on
``emps_max > 0 or chaff_max > 0``. A lone SNAP satisfies neither, so the
one weapon that refuses a landing outright was the one weapon a rival
could hold without the prompt ever mentioning it.

**A seat holding 600 blue was overstated, by exactly double.** The block
rendered per-weapon marginals — ``emp=[0..3] chaff=[0..2]`` — which are
two independent ranges printed adjacently with nothing to say they are
alternatives. Read together they describe a 1200-blue rack under a
600-blue cap.

**A fired SNAP left no trace in the public silhouette.** The activity
tally had no counter for it, so the strike was public on the board and
absent from the tally that summarises the board.

What is pinned here is the *content of the claim*, not its wording. The
prose in these blocks is tuned often and a test that spelled it out
would be rewritten every time it was touched, which is the same as not
having it. So: a seat that cannot afford a weapon must not draw a
warning about it; a total that admits several racks must not be rendered
as one wide range; and every kind the game prices must be reachable
through the whole chain rather than the two that existed first.
"""

from __future__ import annotations

import pytest

from sea_of_colours.game.session import GameSession
from sea_of_colours.game.weapons import (
    BLUE_COST_BY_KIND,
    LEGACY_BLUE_COST_BY_KIND,
    WEAPONISED_BLUE_CAP,
    decode_rack,
)

from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import (
    opponent_weapons as ow,
)
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.prompt import (
    format_opponent_weapons_block,
    _format_combat_event,
    _my_jam_events,
)
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import prompt as v12prompt


PRICES = dict(BLUE_COST_BY_KIND)
SNAP = PRICES["snap"]
EMP = PRICES["emp"]
CHAFF = PRICES["chaff"]


def _est(seat: str, blue: int, prices=None, cap=None) -> ow.WeaponEstimate:
    """An estimate for a seat publishing ``blue``, decoded as the harness does."""
    prices = PRICES if prices is None else prices
    cap = WEAPONISED_BLUE_CAP if cap is None else cap
    est = ow.WeaponEstimate(seat=seat)
    racks = [
        r for r in (decode_rack(blue, prices) or [])
        if cap <= 0 or ow._rack_cost(r, prices) <= cap
    ]
    ow._set_bounds(est, prices, racks, blue=blue, cap=cap)
    return est


# ── the ladder is 1:2:3, and the warnings follow it ───────────────


def test_the_price_ladder_is_the_thing_these_tests_assume():
    """Guard the premise. If a retune lands, this fails first and loudly."""
    assert (SNAP, EMP, CHAFF) == (100, 200, 300), (
        "the 1:2:3 ladder moved — the spend thresholds below are derived "
        "from it, so re-read them rather than rewriting the numbers"
    )


@pytest.mark.parametrize("kind", sorted(PRICES))
def test_no_weapon_is_suspected_of_a_seat_that_cannot_afford_one(kind: str):
    """The spend threshold, per weapon, one blue under the price.

    This is the promise that keeps the warnings meaningful: a seat is
    never warned about a weapon it provably cannot be holding. Derived
    from the price table rather than written out, so a fourth weapon is
    covered on the day it is priced.
    """
    price = PRICES[kind]
    assert not _est("p2", price - 1).could_hold(kind)
    assert _est("p2", price).could_hold(kind)


def test_the_cheapest_weapon_is_the_one_that_used_to_be_invisible():
    """100 blue is a lone SNAP, and it must reach the prompt.

    The old filter asked for EMP or chaff, so this exact seat — armed
    with the weapon that refuses a landing — rendered no block at all.
    """
    est = _est("p2", SNAP)
    assert est.has_any()
    # ``decode_rack`` spells out the zeros; only the non-zero part is the rack.
    assert [
        {k: n for k, n in r.items() if n} for r in est.racks
    ] == [{"snap": 1}]
    assert not est.could_hold("emp")
    assert not est.could_hold("chaff")

    block = format_opponent_weapons_block({"p2": est})
    assert block, "a seat holding ordnance rendered no arsenal block"
    assert "snap" in block.lower()


def test_a_seat_holding_nothing_is_not_worth_a_block():
    assert not _est("p2", 0).has_any()
    assert format_opponent_weapons_block({"p2": _est("p2", 0)}) == ""


# ── the racks are alternatives, and the block must say so ─────────


def test_a_full_rack_is_never_reported_as_more_than_a_full_rack():
    """The bug that started this: marginals summing past the cap.

    Rather than pin the sentence, pin the arithmetic it has to respect —
    no rack the block names may cost more than the seat has weaponised,
    and the marginals it still exposes must NOT be readable as one.
    """
    est = _est("p2", WEAPONISED_BLUE_CAP)
    for rack in est.racks:
        assert ow._rack_cost(rack, PRICES) == WEAPONISED_BLUE_CAP

    # The marginals, taken together, describe something impossible. That
    # is inherent to marginals and is exactly why they are not rendered.
    joint = sum(hi * PRICES[k] for k, (_lo, hi) in est.bounds.items())
    assert joint > WEAPONISED_BLUE_CAP, (
        "if this ever stops being true the ladder has changed shape and "
        "the reason the block avoids marginals needs re-examining"
    )


def test_the_block_names_the_racks_and_calls_them_exclusive():
    est = _est("p2", WEAPONISED_BLUE_CAP)
    assert len(est.racks) > 1
    block = format_opponent_weapons_block({"p2": est})

    # Every real rack is offered...
    for rack in est.racks:
        for kind, n in rack.items():
            if n:
                assert f"{n} {kind}" in block

    # ...and the model is told they are alternatives, not a total.
    assert "ONE" in block, (
        "an ambiguous total must state that exactly one rack is held; "
        "without it the list reads as an inventory"
    )


def test_an_unambiguous_total_does_not_get_lectured_about_ambiguity():
    """One rack fits 100 blue, so the exclusivity note is noise."""
    block = format_opponent_weapons_block({"p2": _est("p2", SNAP)})
    assert "READ THIS RIGHT" not in block


def test_the_block_states_what_the_weapons_cost():
    """Racks and a blue total are uncheckable without the ladder."""
    block = format_opponent_weapons_block({"p2": _est("p2", WEAPONISED_BLUE_CAP)})
    for kind, cost in PRICES.items():
        assert f"{kind} {cost}" in block
    assert str(WEAPONISED_BLUE_CAP) in block


def test_an_old_season_is_explained_with_the_ladder_it_was_played_on():
    """A legacy board decodes — and quotes prices — at its own rates."""
    legacy = dict(LEGACY_BLUE_COST_BY_KIND)
    blue = legacy["emp"] + legacy["chaff"]
    est = _est("p2", blue, prices=legacy)

    assert est.racks, "a legal legacy rack decoded to nothing"
    assert est.min_spend_for("chaff") == legacy["chaff"]
    assert est.min_spend_for("chaff") != CHAFF, (
        "the fixture stopped being a legacy fixture — pick a total the "
        "two ladders disagree about, or this proves nothing"
    )
    assert f"chaff {legacy['chaff']}" in format_opponent_weapons_block({"p2": est})


# ── the gates every warning shares ────────────────────────────────


def test_one_gate_answers_for_every_warning():
    """``_any_could_hold`` is what the doctrine, geometry and facts ask.

    They used to compute their own maxima, which is how they came to
    disagree about SNAP — two of them had no opinion at all.
    """
    ests = {"p2": _est("p2", SNAP)}
    assert v12prompt._any_could_hold(ests, "snap")
    assert not v12prompt._any_could_hold(ests, "emp")
    assert not v12prompt._any_could_hold(ests, "chaff")


def test_a_pre_v38_estimate_still_answers_the_gate():
    """A fork may hand us an estimate pickled by an older harness.

    It has no ``could_hold``; the fallback reads the named maxima, so an
    old estimate degrades to its old behaviour instead of crashing.
    """
    class Ancient:
        seat = "p2"
        emps_min = emps_max = 1
        chaff_min = chaff_max = 0

    ests = {"p2": Ancient()}
    assert v12prompt._any_could_hold(ests, "emp")
    assert not v12prompt._any_could_hold(ests, "chaff")
    assert not v12prompt._any_could_hold(ests, "snap")


def test_geometry_describes_the_weapons_in_play_and_no_others():
    """A seat that cannot afford a chaff is not read chaff geometry."""
    only_snap = v12prompt.format_weapon_geometry_block({"p2": _est("p2", SNAP)})
    assert only_snap, "a SNAP-armed board produced no geometry at all"
    assert "SNAP" in only_snap
    assert "CHAFF" not in only_snap

    full = v12prompt.format_weapon_geometry_block(
        {"p2": _est("p2", WEAPONISED_BLUE_CAP)}
    )
    for token in ("EMP", "SNAP", "CHAFF"):
        assert token in full


def test_being_hit_by_a_weapon_counts_as_knowing_about_it():
    """A strike that landed forces the reaction whatever they can afford."""
    view = {"last_night": {"combat_events": [
        {"type": "snap_hit", "unit": "harvester_p1_1", "victim": "p1",
         "by": ["p2"], "hours": [3], "outcome": "landing_aborted"},
    ]}}
    hits = _my_jam_events(view)
    assert [e["type"] for e in hits] == ["snap_hit"]


@pytest.mark.parametrize("outcome,expected", [
    ("landing_aborted", "ORBIT"),
    ("crippled", "SURFACE"),
])
def test_the_two_kinds_of_snap_casualty_do_not_read_the_same(outcome, expected):
    """A refused landing and a wrecked walk-in need different responses.

    One leaves a harvester in orbit with its outing unspent, the other
    leaves a hull on the board. An agent that cannot tell them apart
    goes looking for a wreck that is not there.
    """
    text = _format_combat_event({
        "type": "snap_hit", "unit": "harvester_p1_1", "victim": "p1",
        "by": ["p2"], "hours": [3], "outcome": outcome,
    })
    assert expected in text.upper()
    assert "{" not in text, "fell through to the raw-dict branch"


def test_a_public_snap_reads_as_prose_not_as_a_dict():
    text = _format_combat_event(
        {"type": "snap", "owner": "p2", "at": [4, 5], "hours": [2]}
    )
    assert "{" not in text
    assert "(4,5)" in text


# ── the engine's own silhouette ───────────────────────────────────


def test_every_weapon_the_game_prices_is_counted_when_it_is_fired():
    """The public activity tally, which the audit line sums.

    SNAP shipped without a counter here, so a seat could fire one every
    night and its orbital silhouette stayed flat.
    """
    tally = GameSession._empty_activity_tally()
    for kind in PRICES:
        # ``emp``/``chaff``/``snap`` are counted as ``emps``/``chaff``/``snaps``
        key = {"emp": "emps", "snap": "snaps"}.get(kind, kind)
        assert key in tally, f"no activity counter for {kind}"


def test_a_fired_snap_shows_up_in_the_public_silhouette():
    sess = GameSession.new(12, 10, seed=7)
    frames = [
        {"owner": "p2", "tag": "snap_launch", "caption": "p2: SNAP at (3,4)"},
        {"owner": "p2", "tag": "snap_launch", "caption": "p2: SNAP at (5,5)"},
        {"owner": "p2", "tag": "emp_launch", "caption": "p2: EMP salvo"},
    ]
    tally = sess.tally_orbital_activity(frames)
    assert tally["p2"]["snaps"] == 2
    assert tally["p2"]["emps"] == 1
    assert "snap_launch" in GameSession._ORBITAL_EVENT_TAGS


def test_the_audit_line_counts_every_shot_not_the_first_two_weapons():
    est = _est("p2", SNAP)
    line = ow._audit_line("p2", SNAP, est, {"activity": {"snaps": 3}}, 1)
    assert "fired 3" in line


# ── the four places a situational dial has to be named ────────────


def test_a_situational_dial_the_schema_forbids_can_never_be_answered():
    """The prompt asks, the schema permits, the sanitiser keeps.

    Under strict structured output these are one mechanism in three
    files. Asking for ``snap`` while the schema omits it is a request
    the model is not allowed to satisfy, and keeping it out of the
    whitelist discards the answer after it arrives.
    """
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12.chat_schema import (
        _V11_DECISION_SCHEMA,
    )
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.directive import (
        _SITUATIONAL_KEYS,
        parse_directive,
    )

    spec = _V11_DECISION_SCHEMA["properties"]["situational"]
    declared = set(spec["properties"])
    assert declared == set(_SITUATIONAL_KEYS), (
        "the schema and the sanitiser whitelist disagree — one of them "
        "is silently dropping a dial the prompt asks for"
    )
    assert set(spec["required"]) == declared

    asked = v12prompt.format_situational_facts_block({}, 2, None)
    for key in _SITUATIONAL_KEYS:
        if key == "players":
            continue
        assert f"{key}:" in asked, f"{key} is permitted but never asked for"

    kept = parse_directive(
        'DECISION: {"posture":"aggressive","situational":'
        '{"mine":true,"players":2,"chaff":false,"emp":false,"snap":true}}'
    )
    assert kept.situational["snap"] is True


def test_the_frozen_v7_schema_was_not_edited_to_get_there():
    """v7 and v8 baselines serve off that dict; v12 rebuilds instead."""
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.chat_schema import (
        _DECISION_SCHEMA as V7,
    )
    assert "snap" not in V7["properties"]["situational"]["properties"]


# ── every shipped harness reads the arsenal the same way ──────────
#
# A harness carries its own copy of the reader, which is what lets a
# fork change it. The cost is that a shipped harness can fall behind
# without anyone noticing, and one did: ``emp_harvest_test`` missed the
# v1.36 fix that decodes against the board's own price table, so it read
# every legacy season's arsenal at today's rates and quietly saw unarmed
# seats. That is not a difference of opinion a fork is entitled to — it
# is a wrong answer about a public fact — so the shipped harnesses are
# held to the same reading here.
#
# This does NOT constrain what a harness DOES with the arsenal. Doctrine,
# pricing dials and buying policy are exactly where a fork should differ.


def _shipped_harnesses():
    import importlib
    from pathlib import Path
    import sea_of_colours.orchestrator_2.harnesses as pkg

    root = Path(pkg.__file__).parent
    out = {}
    for d in sorted(root.iterdir()):
        if not (d / "_v7" / "opponent_weapons.py").exists():
            continue
        out[d.name] = importlib.import_module(
            f"sea_of_colours.orchestrator_2.harnesses.{d.name}"
            "._v7.opponent_weapons"
        )
    return out


def test_the_repo_ships_more_than_one_harness_to_compare():
    names = set(_shipped_harnesses())
    assert "tabula_v12" in names
    assert len(names) > 1, "nothing to compare against — is the example fork gone?"


@pytest.mark.parametrize("blue", [0, 100, 200, 300, 500, 600])
def test_every_shipped_harness_decodes_a_public_total_identically(blue: int):
    reference = None
    for name, mod in _shipped_harnesses().items():
        est = mod.WeaponEstimate(seat="p2")
        racks = decode_rack(blue, PRICES) or []
        mod._set_bounds(est, PRICES, racks, blue=blue, cap=WEAPONISED_BLUE_CAP)
        got = (est.blue, sorted(est.bounds.items()), est.summary())
        if reference is None:
            reference, ref_name = got, name
            continue
        assert got == reference, (
            f"{name} reads a {blue}-blue arsenal differently from "
            f"{ref_name} — the public total has one correct reading"
        )


def test_every_shipped_harness_honours_the_boards_own_price_table():
    """The v1.36 regression that ``emp_harvest_test`` missed.

    Decoding an archived season at today's prices does not merely
    mislabel a rack: a total that was a real loadout under the old table
    is impossible under the new one, so the seat reads as unarmed.
    """
    legacy = dict(LEGACY_BLUE_COST_BY_KIND)
    blue = legacy["emp"] + legacy["chaff"]
    assert not decode_rack(blue, PRICES), (
        "pick a total only the legacy ladder can make, or this proves nothing"
    )
    for name, mod in _shipped_harnesses().items():
        est = mod.WeaponEstimate(seat="p2")
        mod._set_bounds(
            est, legacy, decode_rack(blue, legacy) or [],
            blue=blue, cap=WEAPONISED_BLUE_CAP,
        )
        assert est.has_any(), f"{name} read a legacy arsenal as empty"
        assert est.could_hold("chaff"), f"{name} lost the legacy chaff"
