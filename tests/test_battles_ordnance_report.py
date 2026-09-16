"""The ORDNANCE section has to distinguish three different silences.

They look identical in a report and mean completely different things:

* the run never armed the agent           -> the suite tested nothing
* it was armed and chose not to fire      -> possibly correct doctrine
* it was armed and fired                  -> the gap is closed

The default loadout is ``empty``, so the first case is the commonest run
in the kit. Reporting it as "nothing fired" would tell forty teams their
agent held its charges when in fact it was never given any.
"""

from __future__ import annotations

from sea_of_colours.evals.battles import boards, ladder, predicates, report
from sea_of_colours.evals.battles.runner import (
    BattleResult, RunResult, SuiteResult,
)


def _battle(loadout_id: str, fired: dict[str, int] | None = None) -> BattleResult:
    board = boards.BOARDS[0]
    battle_id = f"{board.id}@armed+{loadout_id}"
    res = BattleResult(
        battle_id=battle_id,
        board=board,
        rung=ladder.get_rung("armed"),
        loadout=ladder.get_loadout(loadout_id),
    )
    res.runs.append(RunResult(
        battle_id=battle_id,
        board_id=board.id,
        rung_id="armed",
        loadout_id=loadout_id,
        run_index=0,
        checks=[predicates.Check("deploy_all", True, "clean")],
        weapons_fired=fired or {"emp_launch": 0, "chaff_flare": 0},
    ))
    return res


def _render(*battles: BattleResult) -> str:
    return report.render(SuiteResult(agent="t", battles=list(battles)))


def test_an_unarmed_run_says_it_tested_nothing():
    text = _render(_battle("empty"))
    assert "never armed" in text
    assert "empty,both" in text, "must name the flag that fixes it"
    assert "Nothing fired" not in text, (
        "an unarmed run must not be reported as an agent holding fire"
    )


def test_an_armed_and_silent_run_is_reported_but_not_condemned():
    text = _render(_battle("emp"))
    assert "Nothing fired" in text
    assert "your call, not the suite's" in text, (
        "when to spend a charge is doctrine; the suite states the fact "
        "and leaves the judgement to whoever wrote the agent"
    )


def test_firing_is_reported_as_firing():
    text = _render(_battle("emp", {"emp_launch": 2, "chaff_flare": 0}))
    assert "Nothing fired" not in text
    assert "never armed" not in text


def test_a_mixed_run_counts_as_armed():
    """`--loadout empty,both` is the recommended run; it must not read
    as unarmed just because one of its two loadouts is."""
    text = _render(_battle("empty"), _battle("both"))
    assert "never armed" not in text
    assert "Nothing fired" in text
