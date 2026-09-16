"""The fast loop: one turn, diffed against the frozen V12 turn.

``soc suite`` answers "is my agent good" and costs twenty minutes
against a model. This answers "did the change I just made do anything",
costs one model call, and is therefore the loop somebody actually runs
while building. Its whole value is that the answer is trustworthy at
one sample, so these pin the line between what is trustworthy and what
is not — and the two ways the first version of it lied.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sea_of_colours.evals.battles import live


# ── reading the option menu ─────────────────────────────────────────

def test_menu_section_labels_are_not_options():
    # The menu's own headings parse as UPPER tokens before a colon. Read
    # as options they became "new options: BUDGET, WHY" on every diff —
    # two plays that do not exist, at the top of the output.
    menu = [
        "BUDGET: 3 probes, 1 harvester",
        "WHY: the seam is unwatched",
        "  [PR1] probe at (7,13): extends the blue sign",
        "  [BLIND_AND_GRAB] drop blind on the beacon: 4 steps",
    ]
    assert live._option_ids(menu) == {"PR1", "BLIND_AND_GRAB"}


def test_lower_case_prose_is_never_an_option():
    assert live._option_ids(["walk the harvester in: 4 steps"]) == set()


# ── what the turn chose ─────────────────────────────────────────────

def test_the_chosen_plan_is_read_from_the_card_when_it_is_there():
    card = {"options_chosen": ["EMP_SCORCH", "PR1"]}
    assert live._chosen(card, "") == ["EMP_SCORCH", "PR1"]


def test_the_chosen_plan_falls_back_to_the_rationale():
    # The frozen V12 turns pre-date options_chosen, but their rationale
    # carries the same thing. Without this the diff's most useful line
    # — "you played X where V12 played Y" — is permanently blank.
    rationale = (
        "[plan=aggressive: BLIND_AND_GRAB, PR1, PR2] [predicted=high] "
        "[fallback=False] moves=7"
    )
    assert live._chosen({}, rationale) == ["BLIND_AND_GRAB", "PR1", "PR2"]


def test_a_rationale_with_no_plan_yields_nothing():
    assert live._chosen({}, "[fallback=True] moves=0") == []


# ── the diff ────────────────────────────────────────────────────────

def _turn(*, card=None, moves=(), fired=0, rationale="", checks=()):
    return SimpleNamespace(
        card=dict(card or {}),
        moves=list(moves),
        checks=[{"name": n, "passed": p} for n, p in checks],
        result={
            "weapons_fired": {"emp_launch": fired},
            "rationale": rationale,
        },
    )


def _rec(turn):
    return SimpleNamespace(bake=SimpleNamespace(turns=[turn]))


def _frozen(**kw):
    base = {
        "moves": [{"a": "probe", "at": [1, 1]}],
        "rationale": "[plan=steady: BLIND_AND_GRAB, PR1]",
        "result": {"score": 0.8, "weapons_fired": {"emp_launch": 0}},
        "checks": [{"name": "denial_probe", "passed": False}],
    }
    base.update(kw)
    return base


@pytest.fixture
def frozen(monkeypatch):
    def _install(card):
        monkeypatch.setattr(live.baseline, "card", lambda _bid: card)
    return _install


def test_menus_are_not_compared_when_the_frozen_turn_has_none(frozen):
    # The baseline records what V12 picked, not what it was offered.
    # Diffed against an empty menu every option the fork has looks
    # newly gained, which is the most misleading thing this can say.
    frozen(_frozen())
    turn = _turn(
        card={"options_offered": "  [PR1] probe: here\n  [EMP_SCORCH] burn: there"},
        rationale="[plan=aggressive: EMP_SCORCH, PR1]",
    )
    d = live.diff(SimpleNamespace(score=0.8), _rec(turn), "b@armed+emp", 1)
    assert d["categorical"]["menus_comparable"] is False
    assert d["categorical"]["options_gained"] == []
    assert d["categorical"]["options_lost"] == []


def test_menus_are_compared_when_both_sides_have_one(frozen):
    frozen(_frozen(card={"options_offered": "  [PR1] probe: here"}))
    turn = _turn(
        card={"options_offered": "  [PR1] probe: here\n  [EMP_SCORCH] burn: there"},
    )
    cat = live.diff(
        SimpleNamespace(score=0.8), _rec(turn), "b@armed+emp", 1,
    )["categorical"]
    assert cat["menus_comparable"] is True
    assert cat["options_gained"] == ["EMP_SCORCH"]


def test_the_headline_leads_with_the_play_that_differed(frozen):
    frozen(_frozen())
    turn = _turn(rationale="[plan=aggressive: UNBEATEN_FLANK, PR1]")
    d = live.diff(SimpleNamespace(score=0.8), _rec(turn), "b@armed+emp", 1)
    assert "UNBEATEN_FLANK" in d["headline"]
    assert "BLIND_AND_GRAB" in d["headline"]


def test_firing_where_v12_was_silent_outranks_everything(frozen):
    frozen(_frozen())
    turn = _turn(fired=3, rationale="[plan=aggressive: EMP_SCORCH]")
    d = live.diff(SimpleNamespace(score=0.8), _rec(turn), "b@armed+emp", 1)
    assert d["headline"].startswith("you fired 3")


def test_a_score_that_moved_alone_is_reported_as_not_a_finding(frozen):
    frozen(_frozen())
    # Same plan, same verbs as the frozen turn: nothing structural to
    # report, so all that is left is a score, which one run cannot read.
    turn = _turn(
        moves=[{"a": "probe", "at": [1, 1]}],
        rationale="[plan=steady: BLIND_AND_GRAB, PR1]",
    )
    d = live.diff(SimpleNamespace(score=1.0), _rec(turn), "b@armed+emp", 1)
    assert "nothing structural changed" in d["headline"]
    assert "the model's mood" in d["headline"]


def test_one_run_and_three_runs_carry_different_warnings(frozen):
    frozen(_frozen())
    turn = _turn()
    one = live.diff(SimpleNamespace(score=0.8), _rec(turn), "b@a+e", 1)
    three = live.diff(SimpleNamespace(score=0.8), _rec(turn), "b@a+e", 3)
    assert "never the decimal" in one["indicative"]["caveat"]
    assert "still not a score" in three["indicative"]["caveat"]


def test_a_board_with_no_frozen_turn_says_so_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(live.baseline, "card", lambda _bid: None)
    d = live.diff(SimpleNamespace(score=1.0), _rec(_turn()), "new@armed+emp", 1)
    assert d["available"] is False
    assert "baseline" in d["why"]


# ── cache ───────────────────────────────────────────────────────────

def test_the_cache_key_tracks_the_agent_source(tmp_path, monkeypatch):
    # Keyed on content, so a coding assistant rewriting a file it did
    # not change does not invalidate the menagerie, and checking out an
    # older agent does not keep serving the newer result.
    monkeypatch.setattr(live, "CACHE_ROOT", tmp_path)
    a = live._cache_key("b@a+e", "mine", 1, "aaaa")
    b = live._cache_key("b@a+e", "mine", 1, "bbbb")
    assert a != b

    live._write_cache(a, {"score": 1.0})
    assert live._read_cache(a) == {"score": 1.0}
    assert live._read_cache(b) is None


def test_a_mangled_cache_entry_costs_a_rerun_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "CACHE_ROOT", tmp_path)
    (tmp_path / "k.json").write_text("{not json")
    assert live._read_cache("k") is None


def test_an_agent_we_cannot_locate_is_not_cached():
    # A stale cached turn is a lie; a missing one is twenty seconds.
    assert live.fingerprint("no_such_agent_anywhere") == "unknown"


def test_every_battle_id_parses():
    for bid in live.battle_ids():
        board, rung, loadout = live.parse_battle_id(bid)
        assert f"{board.id}@{rung.id}+{loadout.id}" == bid


def test_an_unknown_board_names_the_ones_that_exist():
    with pytest.raises(ValueError, match="early_solo_seam"):
        live.parse_battle_id("nope@armed+emp")
