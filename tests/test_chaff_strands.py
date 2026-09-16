"""Chaff's real use: cancel the lift, and let the sunrise do the killing.

Chaff reads like a delay weapon. It jams every House for three hours
including the one that fired it (§4.9.5), and spent as area denial it
mostly wastes your own turn too. What makes it the deadliest thing on
the board is narrower and is not written down as a rule anywhere,
because it is a CONSEQUENCE of three separate ones meeting:

* §4.9.5 -- a flare cancels every non-triggerer's action for its window,
  and unlike EMP there is no exemption for ``pickup``.
* §3.11.2 -- the orblift is the only way off the surface, and anything
  still standing at Aurora is destroyed by the dawn wave, hold and all.
* the queue is committed -- a cancelled pickup cannot be re-queued, so
  there is no recovering inside the same night.

So a flare aimed at the one hour a rival reaches for the lifter is a
kill, and the whole ``adv_chaff`` teaching film is built on it. Nothing
pinned it: the weapons suite covers the cancel, and the dawn-wave suite
covers the strand, but no test walked a rival all the way from a
successful landing to a gravestone with a chaff attribution on it. That
gap is exactly the shape of a refactor that adds a pickup exemption "for
symmetry with EMP" and passes everything.

The last test is the counterweight. Chaff one hour early eats the STEP
instead, the rival lifts on time and lives -- so "chaff kills" cannot be
satisfied by making chaff destroy harvesters outright, which would be a
much simpler and completely wrong weapon.
"""

from __future__ import annotations

from typing import Any, List

import pytest

from sea_of_colours.game.policy import parse_moves
from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.game.simulator import NightSimulator
from sea_of_colours.generator import Cell, Tile

PROBE_AT = (10, 7)
LAND = (10, 7)
STEP = (11, 7)


def _session(monkeypatch: pytest.MonkeyPatch) -> GameSession:
    monkeypatch.setenv("SOC_DROP_MODE", "live_only")
    sess = GameSession.new(20, 14, seed=17, players=["p1", "p2"])
    sess.phase = Phase.PLANNING
    # RED under the rival's route, so the harvester dies holding
    # something. A strand that costs nothing is not the lesson.
    for x, y in (LAND, STEP):
        sess.grid[y][x] = Cell(Tile.RED, 200)
    ok, msg = sess.spawn_probe("p2", *PROBE_AT)
    assert ok, msg
    sess.weapon_stock["p1"] = {**(sess.weapon_stock.get("p1") or {}), "chaff": 1}
    return sess


def _rival_night() -> List[dict]:
    """Land, work, and reach for the lifter on hour four."""
    return [
        {"a": "drop", "unit": "harvester_p2", "at": list(LAND)},
        {"a": "step", "unit": "harvester_p2", "to": list(STEP)},
        {"a": "wait"},
        {"a": "pickup", "unit": "harvester_p2"},
    ]


def _run(sess: GameSession, p1: List[Any], p2: List[Any]) -> List[dict]:
    q1, e1 = parse_moves(p1)
    q2, e2 = parse_moves(p2)
    assert not e1 and not e2, (e1, e2)
    NightSimulator().run(sess, {"p1": q1, "p2": q2})
    return list(sess.last_night_replay or [])


def _harvester(sess: GameSession):
    return sess.entities.get("harvester_p2")


def _on_surface(unit) -> bool:
    """In orbit is ``x``/``y`` unset; a stranded wreck is gone entirely."""
    return unit is not None and unit.x is not None and unit.y is not None


def test_a_flare_on_the_lift_hour_strands_the_rival(monkeypatch):
    """The whole weapon, in one night."""
    sess = _session(monkeypatch)
    # Three waits, so the flare fires on hour four and smothers 4-6.
    # The rival's pickup is hour four; their landing and step are not.
    frames = _run(
        sess,
        [{"a": "wait"}, {"a": "wait"}, {"a": "wait"}, {"a": "chaff_flare"}],
        _rival_night(),
    )

    tags = [str(f.get("tag") or "") for f in frames]
    assert "chaffed" in tags, (
        f"nothing was jammed at all — tags seen: {sorted(set(tags))}"
    )

    # It has to reach the lifter having ALREADY worked, or the film's
    # "let them fill the hold, then take the ride home" is a lie.
    text = " ".join(str(f.get("caption") or "") for f in frames)
    assert "harvested" in text.lower(), (
        f"the rival never harvested before the flare: {text[:300]}"
    )

    assert _harvester(sess) is None, (
        "the rival's harvester survived a night whose only exit was jammed"
    )
    assert "destroyed" in text.lower() or "\u2020" in text, (
        f"no destruction in the night's captions: {text[-400:]}"
    )


def test_the_kill_is_attributed_to_the_chaff(monkeypatch):
    """A gravestone with nobody's name on it teaches the player that
    harvesters sometimes just die."""
    sess = _session(monkeypatch)
    _run(
        sess,
        [{"a": "wait"}, {"a": "wait"}, {"a": "wait"}, {"a": "chaff_flare"}],
        _rival_night(),
    )
    blob = repr(sess.to_dict())
    assert "harv_lost_chaff" in blob, (
        "the strand was not credited to the flare — the kill feed will "
        "show a harvester that died of nothing in particular"
    )


def test_a_flare_an_hour_early_eats_the_walk_and_they_get_away(monkeypatch):
    """The counterweight.

    Chaff does not destroy anything. Fired one hour early it cancels the
    STEP, the pickup on hour four falls outside the window, and the
    rival goes home — poorer, but alive. Without this, "chaff kills"
    would also be satisfied by a chaff that simply kills.
    """
    sess = _session(monkeypatch)
    # Flare on hour two: window is 2-4... which still covers the pickup.
    # So go earlier still — hour one, window 1-3, pickup on 4 is clear.
    _run(sess, [{"a": "chaff_flare"}], _rival_night())

    unit = _harvester(sess)
    assert unit is not None, (
        "chaff destroyed a harvester outright — it is supposed to cancel "
        "actions, and the kill is supposed to come from the dawn wave"
    )
    assert not _on_surface(unit), (
        f"the rival is still on the surface at ({unit.x},{unit.y}) — their "
        "pickup was outside the jam window and should have run"
    )
