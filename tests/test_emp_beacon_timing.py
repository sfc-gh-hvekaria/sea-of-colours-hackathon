"""A same-hour EMP must not invalidate a rival's queued landing (§3.9.7).

Found in the wild: season Terra_Kestrel, day 6, hour 1.

    h1 [emp_launch] p1  p1 fired EMP salvo: 3 missile(s) -> (2,14), ... ;
                        1 probe(s) fried
    h1 [waste]      p2  p2: drop harvester_p2_2 @(2,14) -- drop: (2,14)
                        has no live sensor beacon

Three rulebook clauses say that landing was legal:

* **3.9.7 Timing** -- "Landing legality is judged against the seat's
  *hour-start* live snapshot. A beacon a rival destroys, supersedes, or
  EMPs *later in the same hour* still validates that hour's landing."
* **3.9.8** -- "the probe validates the drop even if a rival attempts to
  supersede or EMP it in the same hour."
* **4.9.3** -- a cloud "freshly spawned by a launch resolved this same
  hour" does not count as established, so the landing also HARVESTS.

WHY THE EXISTING COVERAGE MISSED IT.
``test_vision_rework.test_live_only_honours_hour_start_override`` hands
``try_drop_unit`` an override directly, so it only ever pinned the
*plumbing*. The defect was one layer up, in WHEN the simulator takes the
snapshot -- it was captured after ``_pre_hour_phase``, which is the
function that fires this hour's salvos. Every test here therefore drives
the real ``NightSimulator`` and asserts on replay frames; none may call
``try_drop_unit`` directly, or it stops testing the thing that broke.

The last test is the counterweight: an EMP from an EARLIER hour must
still deny the harvest. Without it, "stop the EMP blanking the landing"
is trivially satisfiable by gutting the denial rule altogether.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from sea_of_colours.game.policy import parse_moves
from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.game.simulator import NightSimulator
from sea_of_colours.generator import Cell, Tile

# p2's probe. Radius 4, so it lights both cells below.
PROBE_AT = (10, 7)
# Landing cell for the same-hour tests: the probe's OWN cell, so the
# salvo that lands here necessarily kills the beacon. That is the whole
# point -- a target the EMP misses would validate for the boring reason.
ON_BEACON = (10, 7)
# Landing cell for the established-cloud control: inside the probe's
# disk but 3 Manhattan from it, so an EMP here (radius 2) leaves the
# beacon alive and the seat can still legally drop on a later hour.
OFF_BEACON = (13, 7)


def _session(monkeypatch: pytest.MonkeyPatch) -> GameSession:
    monkeypatch.setenv("SOC_DROP_MODE", "live_only")
    sess = GameSession.new(20, 14, seed=17, players=["p1", "p2"])
    sess.phase = Phase.PLANNING
    # Force RED under both landing cells. The assertions turn on whether
    # an auto-harvest fired, so a seed that happens to put EMPTY here
    # would pass or fail for reasons that have nothing to do with EMP.
    for x, y in {ON_BEACON, OFF_BEACON}:
        sess.grid[y][x] = Cell(Tile.RED, 200)
    ok, msg = sess.spawn_probe("p2", *PROBE_AT)
    assert ok, msg
    sess.weapon_stock["p1"] = {**(sess.weapon_stock.get("p1") or {}), "emp": 1}
    return sess


def _run(sess: GameSession, p1: List[Any], p2: List[Any]) -> List[dict]:
    q1, e1 = parse_moves(p1)
    q2, e2 = parse_moves(p2)
    assert not e1 and not e2, (e1, e2)
    NightSimulator().run(sess, {"p1": q1, "p2": q2})
    return list(sess.last_night_replay or [])


def _drop_frame(frames: List[dict], hour: int) -> Optional[Dict[str, Any]]:
    for f in frames:
        if f.get("owner") != "p2" or int(f.get("hour") or 0) != hour:
            continue
        if "drop" in str(f.get("attempted") or "").lower():
            return f
    return None


def _emp(at: tuple) -> Dict[str, Any]:
    return {"a": "emp_launch", "at": list(at)}


def _drop(at: tuple) -> Dict[str, Any]:
    return {"a": "drop", "unit": "harvester_p2", "at": list(at)}


def test_a_same_hour_emp_does_not_blank_the_landing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported bug. p1's salvo frays p2's beacon in the same hour."""
    sess = _session(monkeypatch)
    frames = _run(sess, [_emp(ON_BEACON)], [_drop(ON_BEACON)])

    launched = [f for f in frames if f.get("tag") == "emp_launch"]
    assert launched, "fixture: the salvo did not fly"
    assert "probe" in str(launched[0].get("caption") or "").lower(), (
        "fixture: the salvo must actually destroy the beacon, or this test "
        f"passes for the wrong reason -- {launched[0].get('caption')!r}"
    )

    got = _drop_frame(frames, hour=1)
    assert got is not None, "p2's drop produced no frame at all"
    assert got.get("tag") != "waste", (
        "RULEBOOK 3.9.7: a beacon EMP'd later in the same hour still "
        f"validates that hour's landing -- {got.get('caption')!r}"
    )
    assert got.get("tag") == "drop"


def test_a_same_hour_emp_does_not_deny_the_landing_harvest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4.9.3: a cloud spawned THIS hour is not 'already established'."""
    sess = _session(monkeypatch)
    frames = _run(sess, [_emp(ON_BEACON)], [_drop(ON_BEACON)])

    got = _drop_frame(frames, hour=1)
    assert got is not None
    caption = str(got.get("caption") or "")
    assert "auto-harvested" in caption, (
        "RULEBOOK 4.9.3: a cloud freshly spawned by a launch resolved this "
        f"same hour must not deny the landing harvest -- {caption!r}"
    )
    assert "denies auto-harvest" not in caption


def test_an_established_cloud_still_denies_the_landing_harvest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counterweight -- 4.9.3's denial rule must survive the fix.

    p1 seeds the cloud on hour 1 and p2 lands on hour 2, so by then the
    cloud IS established. The beacon is deliberately out of blast range
    (3 Manhattan, radius 2) so the landing stays legal and the ONLY thing
    under test is the harvest.
    """
    sess = _session(monkeypatch)
    frames = _run(
        sess,
        [_emp(OFF_BEACON)],
        [{"a": "wait"}, _drop(OFF_BEACON)],
    )

    got = _drop_frame(frames, hour=2)
    assert got is not None, "p2's hour-2 drop produced no frame"
    caption = str(got.get("caption") or "")
    assert got.get("tag") == "drop", (
        f"fixture: the beacon should have survived the blast -- {caption!r}"
    )
    assert "denies auto-harvest" in caption, (
        "RULEBOOK 4.9.3: landing inside an ALREADY-established cloud must "
        f"still forfeit the auto-harvest -- {caption!r}"
    )


def test_a_beacon_killed_on_an_earlier_hour_does_not_validate_a_later_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other counterweight: the reprieve lasts ONE hour, not the night.

    3.9.7 protects the landing queued for the hour the beacon died in.
    p2 waits instead, so by hour 2 the probe is simply gone and the drop
    is correctly refused. This is what stops the fix being 'cache the
    first snapshot and reuse it', which would let a seat land all night
    on vision it no longer has.
    """
    sess = _session(monkeypatch)
    frames = _run(sess, [_emp(ON_BEACON)], [{"a": "wait"}, _drop(ON_BEACON)])

    got = _drop_frame(frames, hour=2)
    assert got is not None, "p2's hour-2 drop produced no frame"
    caption = str(got.get("caption") or "")
    assert got.get("tag") == "waste", (
        "the hour-start reprieve must not persist past the hour the beacon "
        f"was destroyed in -- {caption!r}"
    )
    assert "no live sensor beacon" in caption
