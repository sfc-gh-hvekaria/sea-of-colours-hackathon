"""The agent can answer its own SNAP doctrine with a move (v1.40).

The doctrine says a landing resting on one probe can be insured with a
second. Before this, it could not: ``probe_hints`` rejects a re-cover in
three independent places, so the menu had no id for the play and the
advice was unactionable prose.

These pin the shape of the answer — that the cover exists when it should,
is absent when it should not, cannot be placed somewhere useless, and is
worded as an option rather than an order.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import agency
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import snap_cover
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.opponent_weapons import (
    WeaponEstimate,
)
from sea_of_colours.game.weapons import decode_rack
from sea_of_colours.orchestrator_2.harnesses.tabula_v12.seam_control import (
    SeamPattern,
    SeamWave,
)
from sea_of_colours.game.weapons import BLUE_COST_BY_KIND, WEAPONISED_BLUE_CAP


PURE = (10, 10)


def _view(
    *,
    probes: List[Any] = None,
    probe_stock: int = 2,
    red: Mapping[Any, int] = None,
    width: int = 24,
    height: int = 24,
) -> Dict[str, Any]:
    """A board with a pure at PURE and whatever probes the case needs."""
    red = {PURE: 255} if red is None else red
    live = []
    for (px, py) in (probes or []):
        for x in range(max(0, px - 4), min(width, px + 5)):
            for y in range(max(0, py - 4), min(height, py + 5)):
                if (x - px) ** 2 + (y - py) ** 2 <= 16:
                    live.append({"x": x, "y": y})
    return {
        "world": {
            "w": width, "h": height, "fog_count": 400,
            "live": live,
            "red": [
                {"x": c[0], "y": c[1], "purity": p} for c, p in red.items()
            ],
        },
        "probe_stock": probe_stock,
        "entities": {
            "mine": [
                {"type": "probe", "pos": list(p), "nights_remaining": 2}
                for p in (probes or [])
            ],
        },
    }


def _seam(drop_at=PURE, *, probe_at=None, deny_only=False) -> SeamPattern:
    return SeamPattern(
        pattern_id="SMASH_GRAB", kind="smash", beacon=drop_at, mine=True,
        title="smash", when="now", rationale="",
        waves=[SeamWave(
            wave=1, earliest_hour=1, drop_at=drop_at,
            probe_at=probe_at, deny_only=deny_only,
        )],
    )


def _estimate(blue: int) -> WeaponEstimate:
    """An opponent whose published arsenal is worth ``blue``.

    Built through ``decode_rack`` rather than by hand, so the bounds this
    module gates on are the same bounds the prompt path would compute.
    """
    prices = dict(BLUE_COST_BY_KIND)
    racks = decode_rack(blue, prices)
    bounds = {
        kind: (
            min((r.get(kind, 0) for r in racks), default=0),
            max((r.get(kind, 0) for r in racks), default=0),
        )
        for kind in prices
    }
    return WeaponEstimate(
        seat="p2", blue=blue, cap=WEAPONISED_BLUE_CAP,
        prices=prices, racks=racks, bounds=bounds,
        snap_max=bounds.get("snap", (0, 0))[1],
        emps_max=bounds.get("emp", (0, 0))[1],
        chaff_max=bounds.get("chaff", (0, 0))[1],
    )


# ── the play exists when the doctrine says it should ────────────────────
def test_a_landing_seen_by_one_probe_gets_offered_a_second():
    view = _view(probes=[(8, 8)])
    hints = snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(100)],
    )
    assert hints, "a pure seen by exactly one probe is the whole case"
    hint = hints[0]
    assert tuple(hint["covers"]) == PURE
    assert tuple(hint["primary"]) == (8, 8)
    bx, by = hint["at"]
    assert (bx - PURE[0]) ** 2 + (by - PURE[1]) ** 2 <= 16, (
        "a cover that cannot see the cell is not cover"
    )


def test_the_cover_never_stands_on_the_cell_it_is_insuring():
    # A SNAP aimed at the contested cell denies the landing whatever else
    # is true, so cover parked there fails in its only case.
    view = _view(probes=[(8, 8)])
    hints = snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(100)],
    )
    assert tuple(hints[0]["at"]) != PURE


def test_the_cover_never_stands_where_a_probe_already_is():
    view = _view(probes=[(8, 8)])
    hints = snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(100)],
    )
    assert tuple(hints[0]["at"]) != (8, 8), (
        "two probes on one cell are one SNAP, not two"
    )


def test_a_probe_planned_for_tonight_counts_as_the_sight_worth_insuring():
    # A blind grab's vision comes from a probe that has not landed yet, and
    # SNAP resolves before the hour's snapshot, so it is just as deletable.
    view = _view(probes=[], probe_stock=3)
    hints = snap_cover.cover_hints(
        view, seam_patterns=[_seam(probe_at=(8, 8))], estimates=[_estimate(100)],
    )
    assert hints and tuple(hints[0]["primary"]) == (8, 8)


# ── and stays quiet when it should ──────────────────────────────────────
def test_the_gate_reads_the_mapping_the_harness_actually_holds():
    # The harness keeps estimates as {seat: WeaponEstimate}. Iterating that
    # yields seat STRINGS, whose missing could_hold was swallowed by the
    # pre-v38 fallback — so the gate said "nobody could hold a SNAP" about
    # a seat holding all three, and the whole module was dead in the live
    # pipeline while every list-shaped test passed.
    view = _view(probes=[(8, 8)])
    as_map = {"p2": _estimate(100)}
    as_list = [_estimate(100)]
    assert snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=as_map,
    ) == snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=as_list,
    ) != []


def test_nothing_is_offered_when_no_rival_could_afford_a_snap():
    view = _view(probes=[(8, 8)])
    assert snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(0)],
    ) == []


def test_a_landing_two_probes_already_see_is_already_insured():
    view = _view(probes=[(8, 8), (12, 12)])
    assert snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(100)],
    ) == []


def test_a_landing_nothing_can_see_has_no_sight_to_lose():
    # A frontier dive is blind by design; there is no probe for SNAP to take.
    view = _view(probes=[(2, 2)])
    assert snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(100)],
    ) == []


def test_a_deny_only_wave_lands_nothing_so_insures_nothing():
    view = _view(probes=[(8, 8)])
    assert snap_cover.cover_hints(
        view, seam_patterns=[_seam(deny_only=True)], estimates=[_estimate(100)],
    ) == []


def test_a_seat_with_no_probe_left_is_not_offered_one():
    view = _view(probes=[(8, 8)], probe_stock=0)
    assert snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(100)],
    ) == []


def test_a_landing_not_worth_insuring_is_left_alone():
    # Trace red, and no redsign over it. The probe is better spent opening
    # ground, which is the nomadic default the rest of the compiler keeps.
    view = _view(probes=[(8, 8)], red={PURE: 20})
    assert snap_cover.cover_hints(
        view,
        hot_drop_hints=[{"drop_at": list(PURE)}],
        estimates=[_estimate(100)],
    ) == []


def test_a_redsign_is_worth_insuring_on_the_broadcast_alone():
    # The point of a smash-and-grab is that the value is known to be there
    # and not yet visible in detail, so judging it on visible purity would
    # decline cover on exactly the landing the doctrine is about.
    view = _view(probes=[(8, 8)], red={})
    assert snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(100)],
    )


def test_the_offer_is_capped_so_fear_cannot_eat_the_fleet():
    view = _view(
        probes=[(8, 8), (18, 18)], probe_stock=5,
        red={PURE: 255, (20, 20): 255},
    )
    hints = snap_cover.cover_hints(
        view,
        seam_patterns=[_seam(), _seam(drop_at=(20, 20))],
        estimates=[_estimate(100)],
        max_hints=1,
    )
    assert len(hints) == 1


# ── how it reaches the agent ────────────────────────────────────────────
def _cover_option() -> agency.Option:
    view = _view(probes=[(8, 8)])
    hints = snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(100)],
    )
    reg = agency.build_registry(
        agent_view=view, seam_patterns=[_seam()], snap_cover_hints=hints,
    )
    return next(o for oid, o in reg.items() if oid.startswith("PRSNAP"))


def test_the_option_says_the_word_snap_where_the_agent_will_read_it():
    # The doctrine that explains the danger and the option that answers it
    # are read pages apart; the option has to name the fear itself.
    opt = _cover_option()
    blob = f"{opt.title} {opt.detail} {opt.rationale}".upper()
    assert "SNAP" in blob


def test_the_option_leaves_the_decision_with_the_agent():
    opt = _cover_option()
    text = opt.rationale.lower()
    assert "your call" in text or "only if you judge" in text
    for order in ("you must", "always take", "never skip"):
        assert order not in text, f"{order!r} makes an offer into an order"


def test_the_option_admits_it_may_be_a_wasted_probe():
    # An honest cost beside an unknowable risk is the only truthful framing:
    # the public total bounds what a rival could hold, never what it did.
    assert "wasted probe" in _cover_option().rationale.lower()


def test_the_cover_compiles_as_an_ordinary_probe():
    # It must stay kind "probe" or the packager needs a new word for a
    # launch that is a launch in every way it cares about.
    opt = _cover_option()
    assert opt.kind == "probe"
    assert agency._option_probe_cost(opt) == 1


def test_the_cover_reads_under_its_own_heading_not_as_exploration():
    opt = _cover_option()
    assert opt.group == "snap_cover"
    assert any(k == "snap_cover" for k, _ in agency._KIND_HEADERS)


def test_the_cover_names_the_landing_it_backs():
    view = _view(probes=[(8, 8)])
    hints = snap_cover.cover_hints(
        view, seam_patterns=[_seam()], estimates=[_estimate(100)],
    )
    reg = agency.build_registry(
        agent_view=view, seam_patterns=[_seam()], snap_cover_hints=hints,
    )
    opt = next(o for oid, o in reg.items() if oid.startswith("PRSNAP"))
    assert "SMASH_GRAB" in opt.detail, (
        "the hint knows the cell; only the finished menu knows its name"
    )


def test_every_other_option_still_groups_by_what_it_is():
    view = _view(probes=[(8, 8)])
    reg = agency.build_registry(agent_view=view, seam_patterns=[_seam()])
    for opt in reg.values():
        assert opt.group == opt.kind
