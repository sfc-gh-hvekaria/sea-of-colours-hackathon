"""An hour spent on a weapon is the WHOLE hour (RULEBOOK §3.10, §4.9.5).

Bug #15. EMP launches and chaff flares resolve in a pre-hour phase, which
consumes the launcher's slot for that hour and marks the seat pre-empted so
the main dispatch skips it. But the two collision pre-passes — pass-through
swap (§3.6) and simultaneous drop (§0.9.10) — ran *after* that phase and
peeked every seat's next queued move without asking who had already acted.

For a pre-empted seat that "next move" belongs to the FOLLOWING hour, so
pairing it against a rival's move for *this* hour did two illegal things at
once: it handed the launcher a second action in one hour, and it staged a
collision between two moves that were never simultaneous. Both harvesters
came out damaged, which made it an attack rather than a cosmetic glitch —
fire an EMP and still cripple the harvester walking past you.

The chaff case is worse in a second way. When *every* seat flares in the
same hour they are all launch-hour immune, which opens the pre-pass gate
(``all_chaff_immune``), and the swap that leaked through consumed the very
moves that the flare's carry-over hours were supposed to jam. So the
launchers dodged the self-jam that §4.9.5 makes them pay:

    If two seats chaff in the same hour, both fire (each pays cost) and
    both are immune for that launch hour, then both are jammed for the
    carry-over hours.

These tests pin the invariant — one applied action per seat per hour, and
the chaff window landing on the launchers too — rather than the mechanism.
"""

from __future__ import annotations

from typing import Any, Optional

from sea_of_colours.game.session import GameSession, Phase

_CHAFF = {"a": "chaff_flare"}


def _duel(*, chaff: int = 0, emp: int = 0) -> GameSession:
    """Two harvesters nose to nose at (5,5)/(6,5), stocked for weapons.

    Adjacency is the point: it makes their steps a legal head-on swap, so
    any leaked pre-pass shows up as a collision frame rather than nothing.
    """
    sess = GameSession.new(20, 14, seed=31)
    sess.phase = Phase.PLANNING
    for seat in sess.players:
        sess.weapon_stock[seat]["chaff"] = chaff
        sess.weapon_stock[seat]["emp"] = emp
    sess.entities["harvester_p1"].x = 5
    sess.entities["harvester_p1"].y = 5
    sess.entities["harvester_p2"].x = 6
    sess.entities["harvester_p2"].y = 5
    return sess


def _resolve(sess: GameSession, p1: list[dict], p2: list[dict]) -> list[dict]:
    sess.stash_policy("p1", p1)
    sess.stash_policy("p2", p2)
    sess.maybe_resolve_if_ready()
    return [f for f in (sess.last_night_replay or []) if isinstance(f, dict)]


def _tagged(frames: list[dict], tag: str, hour: Optional[int] = None) -> list[dict]:
    hits = [f for f in frames if f.get("tag") == tag]
    if hour is not None:
        hits = [f for f in hits if int(f.get("hour") or 0) == hour]
    return hits


def _step(unit: str, to: tuple[int, int]) -> dict[str, Any]:
    return {"a": "step", "unit": unit, "to": [to[0], to[1]]}


def _watch_harvesters(sess: GameSession) -> dict[str, Any]:
    """Grab both harvesters BEFORE the night resolves.

    A harvester left on the surface at Aurora is destroyed (§3.11.2), so it
    is gone from ``sess.entities`` by the time these tests assert. The object
    still carries whatever ``damaged`` flag the night set on it, which is the
    thing under test.
    """
    return {seat: sess.entities[f"harvester_{seat}"] for seat in ("p1", "p2")}


def _damaged(watched: dict[str, Any]) -> dict[str, bool]:
    return {
        seat: bool(getattr(ent, "damaged", False)) for seat, ent in watched.items()
    }


def test_a_plain_swap_still_collides():
    """Guard on the mechanic itself. If the fix disabled the pre-pass
    outright the tests below would pass vacuously."""
    sess = _duel()
    watched = _watch_harvesters(sess)
    frames = _resolve(
        sess,
        [_step("harvester_p1", (6, 5))],
        [_step("harvester_p2", (5, 5))],
    )
    assert _tagged(frames, "collision_swap", hour=1), (
        "a head-on swap with no weapons in play must still be caught"
    )
    assert _damaged(watched) == {"p1": True, "p2": True}


def test_a_weapon_launch_is_the_whole_hour():
    """p1 spends hour 1 firing an EMP, so it cannot also swap at hour 1 —
    even though its next queued move (hour 2's step) would pair with p2's."""
    sess = _duel(emp=2)
    frames = _resolve(
        sess,
        [{"a": "emp_launch", "at": [15, 10]}, _step("harvester_p1", (6, 5))],
        [_step("harvester_p2", (5, 5))],
    )
    assert _tagged(frames, "emp_launch", hour=1), "the launch should resolve at hour 1"
    assert not _tagged(frames, "collision_swap", hour=1), (
        "p1 acted twice in hour 1: it launched AND swapped"
    )


def test_two_chaffs_in_the_same_hour_do_not_leak_a_swap():
    sess = _duel(chaff=2)
    watched = _watch_harvesters(sess)
    frames = _resolve(
        sess,
        [_CHAFF, _step("harvester_p1", (6, 5))],
        [_CHAFF, _step("harvester_p2", (5, 5))],
    )
    assert len(_tagged(frames, "chaff_flare", hour=1)) == 2, (
        "both flares fire at their launch hour (§4.9.5)"
    )
    assert not _tagged(frames, "collision_swap"), (
        "the steps were queued for hour 2, which the flares jam — they must "
        "never resolve as an hour-1 collision"
    )
    assert _damaged(watched) == {"p1": False, "p2": False}


def test_two_chaffs_in_the_same_hour_still_jam_their_own_houses():
    """The price of a flare is the launch slot PLUS the carry-over hours
    (§4.9.5). The leaked swap used to eat the moves those hours would have
    cancelled, so the launchers paid nothing."""
    sess = _duel(chaff=2)
    frames = _resolve(
        sess,
        [_CHAFF, _step("harvester_p1", (6, 5))],
        [_CHAFF, _step("harvester_p2", (5, 5))],
    )
    jammed = {f.get("owner") for f in _tagged(frames, "chaffed", hour=2)}
    assert jammed == {"p1", "p2"}, (
        f"both launchers should be self-jammed at hour 2, got {jammed}"
    )


def test_a_pre_empted_seat_cannot_join_a_simultaneous_drop():
    """Same hole, other pre-pass: two seats dropping on one cell both take
    damage, so a leaked drop collision is also an attack."""
    sess = _duel(chaff=2)
    watched = _watch_harvesters(sess)
    for ent in watched.values():  # back to orbit so a drop is legal
        ent.x = None
        ent.y = None
    frames = _resolve(
        sess,
        [_CHAFF, {"a": "drop", "unit": "harvester_p1", "at": [9, 7]}],
        [_CHAFF, {"a": "drop", "unit": "harvester_p2", "at": [9, 7]}],
    )
    assert not _tagged(frames, "collision_simultaneous_drops", hour=1), (
        "both seats spent hour 1 on flares; their drops belong to hour 2"
    )
    assert _damaged(watched) == {"p1": False, "p2": False}
