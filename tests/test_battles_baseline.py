"""The frozen V12 baseline must stay in step with the boards.

A baseline is a control group, and a control group that silently goes
stale is worse than none: an attendee reads "level with stock V12" off a
card recorded against a board that has since been retuned, and believes
it. These tests do not check that V12 is any GOOD — they check that the
snapshot still describes the boards that exist today.
"""

from __future__ import annotations

import json

import pytest

from sea_of_colours.evals.battles import baseline, boards, ladder


def _ids() -> set[str]:
    return {b.id for b in boards.BOARDS}


def test_every_board_has_a_frozen_baseline():
    """A board with no card prints no comparison — so say so loudly here.

    This is the test that fires when someone adds a board (as
    ``plain_night_armed`` was) and forgets to re-record. The fix is in
    the baseline README; the point of failing is that nobody finds out
    by reading a blank line in ``soc why``.
    """
    recorded = {
        bid.split("@")[0] for bid in (baseline.index().get("battles") or {})
    }
    missing = sorted(_ids() - recorded)
    assert not missing, (
        f"no V12 baseline for {missing}. Re-record with the command in "
        "sea_of_colours/evals/battles/baseline/README.md, or every "
        "`soc why` on those boards silently loses its control group"
    )


def test_the_baseline_does_not_name_boards_that_are_gone():
    recorded = {
        bid.split("@")[0] for bid in (baseline.index().get("battles") or {})
    }
    assert not sorted(recorded - _ids()), "baseline names a deleted board"


def test_index_and_cards_agree():
    """The summary is derived from the cards; drift means it was hand-edited."""
    for battle_id, row in (baseline.index().get("battles") or {}).items():
        card = baseline.card(battle_id)
        assert card is not None, f"{battle_id} indexed with no card on disk"
        assert card["result"]["score"] == pytest.approx(row["score"])
        failed = [c["name"] for c in card["checks"] if not c["passed"]]
        assert failed == row["failed"], battle_id


def test_a_card_carries_the_orders_not_just_a_score():
    """The moves are the whole value — a score alone cannot be compared to."""
    card = baseline.card("plain_night_armed@armed+emp")
    assert card and card["moves"], "a baseline without orders teaches nothing"
    assert all("a" in m for m in card["moves"])


def test_the_recorded_battles_are_real_rungs_and_loadouts():
    rungs = {r.id for r in ladder.RUNGS}
    loadouts = {l.id for l in ladder.LOADOUTS}
    for battle_id in (baseline.index().get("battles") or {}):
        _board, rest = battle_id.split("@")
        rung, loadout = rest.split("+")
        assert rung in rungs and loadout in loadouts, battle_id


def test_compare_is_quiet_about_a_board_it_never_saw():
    assert baseline.compare("no_such_board@armed+emp", 1.0) == ""


def test_compare_calls_a_small_gap_noise():
    """An LLM is not deterministic; a decimal point is not a finding."""
    row = next(iter((baseline.index().get("battles") or {}).items()))
    battle_id, data = row
    assert "level with" in baseline.compare(battle_id, float(data["score"]))


def test_stock_v12_fired_nothing_while_armed():
    """The receipt for the kit's headline claim.

    If this ever fails, V12 has learned to fight and the docs that call
    it "buys weapons and never fires them" are wrong.
    """
    armed = {
        bid: row for bid, row in (baseline.index().get("battles") or {}).items()
        if bid.endswith("+emp")
    }
    assert armed, "fixture: the baseline should include armed battles"
    fired = {
        bid: sum((row.get("weapons_fired") or {}).values())
        for bid, row in armed.items()
    }
    assert not any(fired.values()), (
        f"stock V12 fired something: {fired}. That is good news, but "
        "docs/TEACHING_WEAPONS.md and the fork README both describe it "
        "as never firing — reconcile them"
    )
