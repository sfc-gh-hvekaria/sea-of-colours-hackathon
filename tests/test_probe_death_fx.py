"""Every probe death on a cell files a splash record (RULEBOOK §3.16).

Bug #18. ``pending_probe_crush_events`` is the simulator → replay-frame
channel for "a probe died here, splash the cell in its owner's colour". It
was only ever filled by :meth:`consume_probes_at` — the harvester crush it
was originally built for. The three §3.16 paths in ``spawn_probe`` wrote a
log line and nothing else, so:

* a **superseded** probe simply blinked out of existence, with no cue that
  the arriving probe had killed it; and
* a **mutual annihilation** rendered *nothing at all* — not even the
  incoming streak. The animator derives a probe's landing by diffing entity
  snapshots, and a probe destroyed on arrival is created and destroyed
  inside one move, so it never appears in one. Two seats could burn a probe
  each on the same cell and the board would not flicker.

These tests pin the records rather than the pixels: the frontend already
owns the splash (and already skips cells the viewing seat has fogged, which
is what keeps a §3.15 launch marker honest for a House that saw the landing
but not the death).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sea_of_colours.game.session import GameSession, Phase, _xy_key

CELL = [10, 8]


def _fresh(*seats: str) -> GameSession:
    return GameSession.new(24, 16, seed=5, players=seats or ("p1", "p2"))


def _night(sess: GameSession, plans: Dict[str, List[dict]]) -> List[dict]:
    """Resolve one night, stepping through the orbit phase that precedes it."""
    if sess.phase == Phase.ORBIT:
        for seat in sess.players:
            sess.stash_orbit_actions(seat, [])
        sess.maybe_resolve_orbit_if_ready()
    for seat in sess.players:
        sess.stash_policy(seat, plans.get(seat, []))
    assert sess.maybe_resolve_if_ready(), f"night stalled in {sess.phase}"
    return [f for f in (sess.last_night_replay or []) if isinstance(f, dict)]


def _probe(at: Optional[list] = None) -> dict[str, Any]:
    return {"a": "probe", "at": list(at or CELL)}


def _deaths(frames: List[dict], owner: Optional[str] = None) -> List[dict]:
    """Every splash record on the night, optionally only the frames a
    given seat acted on."""
    out: List[dict] = []
    for f in frames:
        if owner is not None and f.get("owner") != owner:
            continue
        out.extend(f.get("crushed_probes") or [])
    return out


def test_a_plain_launch_splashes_nothing():
    """Guard. A probe landing on an empty cell kills nothing, so an
    over-eager fix that splashed on every deployment fails here."""
    sess = _fresh()
    frames = _night(sess, {"p1": [_probe()]})
    assert not _deaths(frames), "an unopposed launch must not splash"


def test_a_harvester_crush_still_files_one(monkeypatch):
    """Guard on the original channel — the case this buffer was built for.

    Own board, cell and drop mode: under the default ``live`` rule p2 has no
    sensor over (10,10) — p1's probe is p1's — so the drop is refused before
    it can crush anything. ``live_or_echo`` is the geometry
    ``test_replay_frame_carries_crushed_probes_payload`` runs under.
    """
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")
    at = [10, 10]
    sess = GameSession.new(28, 18, seed=901)
    _night(sess, {"p1": [_probe(at)]})
    frames = _night(sess, {"p2": [
        {"a": "drop", "unit": "harvester_p2", "at": at},
        {"a": "pickup", "unit": "harvester_p2"},
    ]})
    deaths = _deaths(frames)
    assert len(deaths) == 1, f"expected one crush record, got {deaths}"
    assert deaths[0]["probe_owner"] == "p1"
    assert deaths[0]["reason"] == "crushed_by_harvester"
    assert deaths[0]["crusher_owner"] == "p2"


def test_a_superseded_probe_files_one():
    sess = _fresh()
    _night(sess, {"p1": [_probe()]})
    frames = _night(sess, {"p2": [_probe()]})

    deaths = _deaths(frames)
    assert len(deaths) == 1, f"expected the older probe to splash, got {deaths}"
    ev = deaths[0]
    assert ev["reason"] == "probe_superseded"
    assert ev["probe_owner"] == "p1", "the splash is the VICTIM's, in its colour"
    assert ev["crusher_owner"] == "p2"
    assert ev["at"] == CELL


def test_the_splash_rides_the_arriving_seats_frame():
    """It has to land on the mover's frame, because that frame is what
    animates the streak the burst is timed against."""
    sess = _fresh()
    _night(sess, {"p1": [_probe()]})
    frames = _night(sess, {"p2": [_probe()]})
    assert _deaths(frames, owner="p2"), "the record belongs to p2's move"
    assert not _deaths(frames, owner="p1"), "p1 did nothing this night"


def test_superseding_your_own_probe_still_files_one():
    """Refreshing your own cell is the routine play, and it does kill a
    probe — so it splashes, in your own colour."""
    sess = _fresh()
    _night(sess, {"p1": [_probe()]})
    frames = _night(sess, {"p1": [_probe()]})
    deaths = _deaths(frames)
    assert len(deaths) == 1
    assert deaths[0]["probe_owner"] == "p1"
    assert deaths[0]["crusher_owner"] == "p1"


def test_mutual_annihilation_files_both_probes():
    """The headline case: neither probe survives to be diffed out of an
    entity snapshot, so these records are the ONLY thing the animator has
    to work from."""
    sess = _fresh()
    frames = _night(sess, {"p1": [_probe()], "p2": [_probe()]})

    deaths = _deaths(frames)
    assert {e["probe_owner"] for e in deaths} == {"p1", "p2"}, (
        f"both probes must splash, got {deaths}"
    )
    assert all(e["reason"] == "probe_collision" for e in deaths)
    assert all(e["at"] == CELL for e in deaths)


def test_the_arriving_probe_files_its_own_death():
    """Specifically the probe that was never in a snapshot: without a record
    naming it, the frontend has no cell to fly a streak to."""
    sess = _fresh()
    frames = _night(sess, {"p1": [_probe()], "p2": [_probe()]})
    mine = _deaths(frames, owner="p2")
    assert any(e["probe_owner"] == "p2" for e in mine), (
        f"p2's own probe died on arrival and must appear on p2's frame: {mine}"
    )


def test_a_latecomer_into_the_crater_files_one():
    """§3.16 E4 — a third probe onto the same cell in the same hour dies in
    the crater the first two left."""
    sess = _fresh("p1", "p2", "p3", "p4")
    frames = _night(sess, {
        "p1": [_probe()], "p2": [_probe()], "p3": [_probe()],
    })
    p3_deaths = _deaths(frames, owner="p3")
    assert [e["probe_owner"] for e in p3_deaths] == ["p3"], (
        f"p3 lands in the crater and is the only casualty of its own move: "
        f"{p3_deaths}"
    )
    assert p3_deaths[0]["reason"] == "probe_collision"


def test_a_later_hour_arrival_lands_clean():
    """The crater is scoped to the (day, hour) stamp, so an arrival an hour
    later finds the cell swept and grants vision normally — nothing to
    splash on its frame."""
    sess = _fresh("p1", "p2", "p3", "p4")
    frames = _night(sess, {
        "p1": [_probe()],
        "p2": [_probe()],
        "p3": [{"a": "wait"}, _probe()],
    })
    assert not _deaths(frames, owner="p3"), "p3 arrived after the sweep"
    assert any(
        e.entity_type == "probe" and e.owner == "p3"
        for e in sess.entities.values()
    ), "and its probe should be standing"


def _ledger_reason(sess: GameSession, probe_id: str) -> Optional[str]:
    rec = sess.asset_records.get(probe_id)
    return None if rec is None else rec.destroyed_by


def _supersede_credits(sess: GameSession) -> dict:
    return {
        killer: dict(victims)
        for killer, victims in (
            sess.combat_attrib.get("probes_superseded") or {}
        ).items()
        if victims
    }


def test_a_lone_newcomer_still_earns_its_supersede():
    """Guard on §3.16(a). The credit and the ledger word are real when one
    House actually takes the cell — the ruling below must not erase them."""
    sess = _fresh()
    _night(sess, {"p1": [_probe()]})
    _night(sess, {"p2": [_probe()]})

    assert _ledger_reason(sess, "probe_p1_1") == "probe_superseded"
    assert _supersede_credits(sess) == {"p2": {"p1": 1}}


def test_an_incumbent_dies_with_the_pile_not_as_a_supersede():
    """§3.16(c), the ruling. An older probe holds the cell and two probes
    land on it in the same hour: the hour is ONE event, nobody took the
    cell, so the incumbent is a collision casualty like the rest.

    Pinned on the ledger rather than the log because the ledger is what the
    endgame roster and the kill feed read.
    """
    sess = _fresh("p1", "p2", "p3", "p4")
    _night(sess, {"p1": [_probe()]})
    _night(sess, {"p2": [_probe()], "p3": [_probe()]})

    assert _ledger_reason(sess, "probe_p1_1") == "probe_collision", (
        "the incumbent must not be filed as superseded by whichever seat "
        "the resolver reached first"
    )
    for pid in ("probe_p2_1", "probe_p3_1"):
        assert _ledger_reason(sess, pid) == "probe_collision"
    assert not [
        e for e in sess.entities.values() if e.entity_type == "probe"
    ], "nothing survives the cell"


def test_nobody_is_credited_a_supersede_when_the_pile_annihilates():
    """The other half of the ruling: a seat that lost its own probe in the
    same instant did not supersede anything."""
    sess = _fresh("p1", "p2", "p3", "p4")
    _night(sess, {"p1": [_probe()]})
    _night(sess, {"p2": [_probe()], "p3": [_probe()]})

    assert _supersede_credits(sess) == {}, (
        f"unexpected supersede credit: {_supersede_credits(sess)}"
    )


def test_four_probes_onto_one_incumbent_all_die():
    """'Same with four.' The count is not the mechanism — every arrival on
    the stamp dies, and so does whatever was already standing."""
    sess = _fresh("p1", "p2", "p3", "p4")
    _night(sess, {"p1": [_probe()]})
    _night(sess, {seat: [_probe()] for seat in ("p2", "p3", "p4")})

    assert not [
        e for e in sess.entities.values() if e.entity_type == "probe"
    ], "no probe survives a four-way pile"
    for pid in ("probe_p1_1", "probe_p2_1", "probe_p3_1", "probe_p4_1"):
        assert _ledger_reason(sess, pid) == "probe_collision", pid
    assert _supersede_credits(sess) == {}


def test_every_probe_on_the_square_splashes():
    """The FX side of the same ruling: the incumbent bursts too, so the
    square doesn't quietly lose a probe the watcher never saw die."""
    sess = _fresh("p1", "p2", "p3", "p4")
    _night(sess, {"p1": [_probe()]})
    frames = _night(sess, {"p2": [_probe()], "p3": [_probe()]})

    assert {e["probe_owner"] for e in _deaths(frames)} == {"p1", "p2", "p3"}, (
        f"all three should splash, got {_deaths(frames)}"
    )
    assert all(e["at"] == CELL for e in _deaths(frames))


# ── bug #19: the probe mark left on a square with no probe on it ───────
#
# §3.15 publishes a launch to every other House, so a landing immediately
# appears in every opponent's echo. Nothing took it back off again when the
# probe died in a §3.16 contest: the entity went, the marks stayed, and the
# only thing that would eventually clear them is the three-night sweep. So
# a House could watch two streaks converge on one square, watch them burst,
# and go on seeing a probe sitting there for the next three nights.


def _echoed_probes(sess: GameSession, seat: str, at=CELL) -> set[str]:
    """Which probes ``seat``'s echo believes are standing on ``at``.

    Reads the same two places the renderer does: the occupant list, and the
    §3.15 launch overlay merged onto a cell the seat can also see for
    itself.
    """
    snap = (sess.probe_intel.get(seat) or {}).get(_xy_key(at[0], at[1])) or {}
    seen = {
        str(o.get("id"))
        for o in (snap.get("occupants") or [])
        if isinstance(o, dict) and str(o.get("type")) == "probe"
    }
    glyph = snap.get("probe_launch_glyph")
    if isinstance(glyph, dict) and glyph.get("probe_id"):
        seen.add(str(glyph["probe_id"]))
    return seen


def _live_probe_ids(sess: GameSession) -> set[str]:
    return {
        e.id for e in sess.entities.values() if e.entity_type == "probe"
    }


def test_the_launch_mark_survives_a_probe_that_survives():
    """Precondition and guard in one. The §3.15 mark is the whole point of
    publishing a launch, so a fix that simply stopped writing marks — or
    scrubbed them indiscriminately — fails here."""
    sess = _fresh()
    _night(sess, {"p1": [_probe()]})

    assert _echoed_probes(sess, "p2") == {"probe_p1_1"}, (
        "p2 is entitled to see where p1's probe came down (§3.15)"
    )


def test_a_superseded_probe_stops_being_painted_on_the_square():
    """p1's probe died under p2's. The square now holds p2's probe and only
    p2's probe, and every House that was told about the launch must be told
    about the death."""
    sess = _fresh()
    _night(sess, {"p1": [_probe()]})
    _night(sess, {"p2": [_probe()]})

    assert _live_probe_ids(sess) == {"probe_p2_1"}
    for seat in ("p1", "p2"):
        assert "probe_p1_1" not in _echoed_probes(sess, seat), (
            f"{seat} still sees the superseded probe_p1_1 on the square"
        )


def test_the_pile_up_leaves_no_probe_mark_anywhere():
    """§3.16(c): every probe on the square died, so no House anywhere may
    still show a probe standing on it — including the bystander p4, who saw
    all three launches published to it and none of them retracted."""
    sess = _fresh("p1", "p2", "p3", "p4")
    _night(sess, {"p1": [_probe()]})
    _night(sess, {"p2": [_probe()], "p3": [_probe()]})

    assert not _live_probe_ids(sess), "precondition: the square is empty"
    for seat in ("p1", "p2", "p3", "p4"):
        assert not _echoed_probes(sess, seat), (
            f"{seat} still sees {_echoed_probes(sess, seat)} "
            f"on a square with nothing on it"
        )


def test_a_death_elsewhere_does_not_touch_this_square():
    """Guard on the scope. Clearing marks is keyed to the square that was
    contested; a probe dying on (10,8) must not wipe what a House knows
    about a probe standing on (14,8)."""
    sess = _fresh()
    far = [14, 8]
    _night(sess, {"p1": [_probe(), _probe(far)]})
    assert _echoed_probes(sess, "p2", far) == {"probe_p1_2"}

    _night(sess, {"p2": [_probe()]})

    assert _echoed_probes(sess, "p2", far) == {"probe_p1_2"}, (
        "the far probe is alive and its mark must stand"
    )
