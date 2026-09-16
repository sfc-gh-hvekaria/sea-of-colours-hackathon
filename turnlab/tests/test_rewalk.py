"""A re-walked season has to *be* the season, not resemble it.

Everything in :mod:`turnlab.rewalk` rests on one claim: replaying a
season's archived orders through the real engine reproduces it exactly.
If that is ever untrue the lab is quietly comparing agents on a board
nobody ever played, and nothing else in the package would notice. So the
central test here does not check a few fields — it plays a whole season,
throws it away, rebuilds it from what was archived, and demands the two
match.

The one divergence this module has ever had is worth knowing about,
because it is the shape the next one will take. Orbit is not archived as
a submission, so it is recovered from the settlement *log*, and the
first version parsed only the ``built ...`` lines. Repairs spend credits
and un-damage a unit, and every season that repaired a harvester drifted
from that day on, while every season that never repaired was exact —
which is precisely the sort of bug that looks like "mostly working".
:func:`test_a_repair_is_an_orbit_action_too` pins it.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from sea_of_colours.snowpark import engine as soc_engine  # noqa: E402
from sea_of_colours.snowpark.store import InMemorySocStore  # noqa: E402
from turnlab import boards, rewalk  # noqa: E402


def _season(days: int = 4, seed: int = 42):
    """Play a real headless season and hand back its store and id."""
    from sea_of_colours.evals import dispatch

    store = InMemorySocStore()
    seats = {"p1": "red_harvest", "p2": "red_harvest"}
    info = soc_engine.init_session(
        store, seed=seed, width=24, height=18, season_name="REWALK_FIXTURE",
        season_day_cap=days, players=list(seats),
        agents={s: dispatch.strategy_slug(a) for s, a in seats.items()},
    )
    sid = info["session_id"]
    for _ in range(400):
        status = soc_engine.get_session_status(store, sid)
        if status.get("phase") == "season_complete":
            break
        pending = status.get("pending") or {}
        seat = next((s for s in seats if not pending.get(s, False)), None)
        if seat is None:
            soc_engine.run_night(store, sid)
            continue
        dispatch.play_turn(store, sid, seat, seats[seat])
    return store, sid


@pytest.fixture(scope="module")
def played():
    return _season()


def test_a_rewalked_season_is_the_season(played):
    """The claim the whole module rests on, checked end to end."""
    store, sid = played
    report = rewalk.check(sid, src=store, into=InMemorySocStore())

    broken = sorted(k for k, v in report["fields"].items() if not v)
    assert not broken, (
        f"re-walking {report['name']} to day {report['day']} diverged in "
        f"{broken}. The engine has acquired a source of nondeterminism, or "
        f"an orbit action is no longer being recovered from the log."
    )
    assert not report["complaints"], report["complaints"]
    assert report["ok"]


def test_it_can_be_paused_at_any_day_not_just_the_end(played):
    """A board is a *mid*-season night, so stopping short has to work."""
    store, sid = played
    source = rewalk.describe(store, sid)
    assert source is not None and len(source.playable_days) > 2

    for day in source.playable_days[:-1]:
        into = InMemorySocStore()
        got = rewalk.rewalk(sid, day, src=store, into=into)
        assert got.day == day, f"asked for day {day}, landed on {got.day}"
        assert got.phase == "planning", (
            f"day {day} came back in phase {got.phase!r}; a board has to be "
            f"pre-decision or an agent cannot plan into it"
        )
        assert not got.complaints, got.complaints
        # And it must be a board the engine will actually play.
        view = soc_engine.get_view(into, got.session_id, "p1")
        assert int(view.get("day") or 0) == day


def test_a_paused_day_matches_that_days_own_replay_frame(played):
    """Checked against the engine's independent record, not against us.

    The re-walk and the frame are produced by different code paths from
    different inputs, so agreement between them is real evidence.
    """
    store, sid = played
    day = rewalk.describe(store, sid).playable_days[-2]

    frame = next(
        (f for f in (store.list_replay_frames(sid, day) or [])
         if f.get("tag") == "open"),
        None,
    )
    if frame is None:
        pytest.skip("this fixture kept no opening frame for that day")

    into = InMemorySocStore()
    got = rewalk.rewalk(sid, day, src=store, into=into)
    live = soc_engine._hydrate_session(into, got.session_id).to_dict()

    for entity in frame.get("entities") or []:
        mine = (live.get("entities") or {}).get(entity["id"])
        assert mine is not None, f"{entity['id']} missing from the re-walk"
        here = [mine.get("x"), mine.get("y")] if mine.get("x") is not None \
            else None
        assert here == entity.get("surface"), (
            f"{entity['id']} is at {here} in the re-walk but the engine's own "
            f"day-{day} frame puts it at {entity.get('surface')}"
        )


def test_a_repair_is_an_orbit_action_too():
    """The divergence that actually happened, pinned as a unit test."""
    log = [
        {"day": 2, "text": "[orbit] p1: built 2 probe(s) (+2 stock → 2) for 500c"},
        {"day": 2, "text": "[orbit] p1: repaired harvester harvester_p1 for 500c"},
        {"day": 2, "text": "[orbit] p2: auto-shipped 3 RED parcel(s) for 9 score"},
        {"day": 3, "text": "[orbit] p2: built harvester harvester_p2_2 for 1500c"},
    ]

    class Log:
        def list_log(self, _sid, *a, **k):
            return log

    got = rewalk.orbit_baskets(Log(), "x")
    assert got[2]["p1"] == [
        {"a": "build_probe", "count": 2},
        {"a": "repair", "unit": "harvester_p1"},
    ], "a repair spends credits and un-damages a unit; dropping it drifts"
    assert got[3]["p2"] == [{"a": "build_harvester"}]
    # Resolver side effects are not player actions and must not be replayed.
    assert "p2" not in got[2]


def test_a_season_using_a_retired_weapon_says_so_instead_of_lying():
    """The caltrop mine has no build action any more (v1.31).

    Replaying such a season would silently produce a *different* season,
    which is worse than refusing.
    """
    class Log:
        def list_log(self, _sid, *a, **k):
            return [{"day": 4, "text":
                     "[orbit] p1: built 2 caltrop mine(s) (+2 mine stock → 2); "
                     "spent 100 blue, 0c"}]

    assert "caltrop" in rewalk.unsupported(Log(), "x")

    class Clean:
        def list_log(self, _sid, *a, **k):
            return [{"day": 4, "text": "[orbit] p1: built 1 probe(s) for 250c"}]

    assert rewalk.unsupported(Clean(), "x") == ""


def test_the_same_turn_always_grabs_to_the_same_id():
    """``turnlab/data`` is disposable, so ids must not be.

    Baselines are recorded against a board id. If grabbing the same turn
    twice produced two ids, deleting the data folder would orphan every
    baseline in ``turnlab/baseline/`` — which is checked in and is not
    disposable.
    """
    a = rewalk.board_id("7ff8fed5a0734acdab80ceade7879362", 5)
    b = rewalk.board_id("7ff8fed5a0734acdab80ceade7879362", 5)
    assert a == b
    assert a != rewalk.board_id("7ff8fed5a0734acdab80ceade7879362", 6)
    assert a != rewalk.board_id("06054924dfc746d7baa55467426a9a62", 5)
    # And it has to read back as a board, or the launcher cannot list it.
    assert boards._parse(a) is not None


def test_a_grabbed_board_is_a_board(played):
    """The whole point: name a season and a day, get something playable."""
    store, sid = played
    into = InMemorySocStore()
    day = rewalk.describe(store, sid).playable_days[-2]

    got = rewalk.grab("REWALK_FIXTURE", day, src=store, into=into)
    assert got.session_id == rewalk.board_id(sid, day)
    assert not got.complaints

    found = boards.discover(into)
    assert [b.id for b in found] == [got.session_id]
    assert found[0].day == day
    # The label is what a person picks from, so it has to name the season.
    assert "REWALK_FIXTURE" in found[0].label
    assert str(day) in found[0].label


def test_grabbing_twice_does_not_re_walk_twice(played):
    """Re-walking is seconds of real simulation; don't pay it repeatedly."""
    store, sid = played
    into = InMemorySocStore()
    day = rewalk.describe(store, sid).playable_days[-2]

    first = rewalk.grab("REWALK_FIXTURE", day, src=store, into=into)
    again = rewalk.grab("REWALK_FIXTURE", day, src=store, into=into)
    assert again.session_id == first.session_id
    assert again.walked == 0, "the second grab re-simulated the season"


def test_a_season_can_be_named_the_way_a_person_would_name_it(played):
    """Exact, wrong case, a prefix, or the session id — all should land."""
    store, sid = played
    for spelling in ("REWALK_FIXTURE", "rewalk_fixture", "REWALK_FIX", sid):
        got = rewalk.find(store, spelling)
        assert got is not None and got.session_id == sid, spelling
    assert rewalk.find(store, "no_such_season_anywhere") is None


def test_the_source_is_only_ever_read(played):
    """A lab that can edit a real season is the one bug that matters."""
    store, sid = played
    before = soc_engine._hydrate_session(store, sid).to_dict()

    rewalk.rewalk(sid, 2, src=store, into=InMemorySocStore())
    rewalk.grab("REWALK_FIXTURE", 2, src=store, into=InMemorySocStore())

    after = soc_engine._hydrate_session(store, sid).to_dict()
    assert after == before, "re-walking mutated the season it read from"


def test_it_refuses_to_read_and_write_the_same_store(played):
    """The cheapest possible guard against the above, kept explicit."""
    store, sid = played
    with pytest.raises(ValueError, match="separate 'into'"):
        rewalk.rewalk(sid, 2, src=store, into=store)


def test_asking_for_a_day_the_season_never_reached_is_an_error(played):
    store, sid = played
    with pytest.raises(ValueError, match="outside"):
        rewalk.rewalk(sid, 999, src=store, into=InMemorySocStore())
    with pytest.raises(KeyError):
        rewalk.grab("REWALK_FIXTURE", 999, src=store, into=InMemorySocStore())
