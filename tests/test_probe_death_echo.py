"""A dying probe's last look must be ONE moment, not a collage.

Bug #14: a harvester riding over a probe left the probe's owner looking
at *two* copies of that harvester — one where it now stood, one where it
had stepped from. Both were echoes, both looked live, and nothing on the
board said which was real.

The cause is that a probe refreshes its whole vision disk on every hourly
pulse, but the death path only refreshed the single cell the probe died
on. So the echo ended up stitched from two different instants: the death
cell as of the crush, and every other cell as of the last pulse before
it. Normally the next pulse would reconcile that — except the observer is
dead, so there is no next pulse, and the stale glyph became permanent for
the rest of the night.

These tests pin the invariant rather than the implementation: after a
crush, a harvester appears in the victim's echo exactly once.
"""

from __future__ import annotations

from sea_of_colours.game.session import (
    Entity,
    GameSession,
    Phase,
    _probe_vision_disk,
    _xy_key,
)


def _fresh(width: int = 24, height: int = 16, seed: int = 909) -> GameSession:
    sess = GameSession.new(width, height, seed=seed)
    sess.memory_tiles = {p: {} for p in sess.players}
    sess.probe_intel = {p: {} for p in sess.players}
    sess.phase = Phase.PLANNING
    return sess


def _harvester_echo_cells(sess: GameSession, viewer: str, unit_id: str):
    """Cells where ``viewer``'s echo still shows ``unit_id`` standing."""
    hits = []
    for key, snap in (sess.probe_intel.get(viewer) or {}).items():
        occ = snap.get("occupants") or []
        if any(isinstance(o, dict) and o.get("id") == unit_id for o in occ):
            hits.append(key)
    return sorted(hits)


def _crush_setup(sess: GameSession):
    """p2 probe at B, p1 harvester one cell away at A, both pulsed.

    Returns ``(a, b)``. A sits inside the probe's disk, which is the
    whole point: the probe sees the harvester coming, and that sighting
    is what used to be left behind as a ghost.
    """
    bx, by = 10, 8
    ax, ay = bx - 1, by
    ok, _ = sess.spawn_probe("p2", bx, by)
    assert ok, "probe should deploy"
    assert (ax, ay) in _probe_vision_disk(bx, by, sess.width, sess.height)

    harv = sess.entities["harvester_p1"]
    harv.x, harv.y = ax, ay
    sess._pulse_vision_intel()
    return (ax, ay), (bx, by)


def test_the_probe_sees_the_harvester_coming():
    """Guard for the setup itself: without this sighting there is no
    ghost to leave behind, and the real test would pass vacuously."""
    sess = _fresh()
    a, _b = _crush_setup(sess)
    assert _harvester_echo_cells(sess, "p2", "harvester_p1") == [_xy_key(*a)]


def test_a_crushed_probe_leaves_one_harvester_echo_not_two():
    sess = _fresh()
    a, b = _crush_setup(sess)

    harv = sess.entities["harvester_p1"]
    harv.x, harv.y = b  # it rides onto the probe
    sess.consume_probes_at(b[0], b[1], crusher_owner="p1")

    seen = _harvester_echo_cells(sess, "p2", "harvester_p1")
    assert seen == [_xy_key(*b)], (
        f"expected the harvester echoed only where it now stands {b}, "
        f"got {seen} — the cell it left ({a}) is a ghost"
    )


def test_the_victim_still_learns_who_killed_its_probe():
    """The v1.8 behaviour this must not regress: the final echo carries
    the killer's glyph, so the owner can see what rode over it."""
    sess = _fresh()
    _a, b = _crush_setup(sess)

    harv = sess.entities["harvester_p1"]
    harv.x, harv.y = b
    sess.consume_probes_at(b[0], b[1], crusher_owner="p1")

    snap = sess.probe_intel["p2"][_xy_key(*b)]
    assert snap.get("glyph_ch"), f"death cell should carry a glyph: {snap!r}"
    ids = {o.get("id") for o in (snap.get("occupants") or [])}
    assert "harvester_p1" in ids, f"killer missing from the final echo: {snap!r}"


def test_the_dead_probe_does_not_echo_itself():
    """Self-exclusion (v0.9.13) still applies through the death path —
    otherwise the owner keeps a frozen ghost-probe on a cell where the
    probe no longer exists."""
    sess = _fresh()
    _a, b = _crush_setup(sess)
    probe_ids = [
        e.id for e in sess.entities.values()
        if e.entity_type == "probe" and e.owner == "p2"
    ]
    assert probe_ids, "setup should have left a p2 probe on the board"

    harv = sess.entities["harvester_p1"]
    harv.x, harv.y = b
    sess.consume_probes_at(b[0], b[1], crusher_owner="p1")

    snap = sess.probe_intel["p2"][_xy_key(*b)]
    ids = {o.get("id") for o in (snap.get("occupants") or [])}
    assert not (ids & set(probe_ids)), (
        f"dead probe echoed itself at its own grave: {snap!r}"
    )


def test_two_probes_crushed_together_do_not_echo_each_other():
    """Both die in the same instant, so neither may be left standing in
    the other's final snapshot.

    The probes are injected rather than deployed: ``spawn_probe`` would
    supersede the first with the second (§3.16), leaving nothing to die
    simultaneously, and would also stamp a §3.15 launch marker in the
    opponent's echo, which is a different (and intended) record.
    """
    sess = _fresh()
    bx, by = 10, 8
    ids = []
    for seat in ("p1", "p2"):
        pid = f"probe_{seat}_sim"
        sess.entities[pid] = Entity(pid, "probe", seat, bx, by)
        ids.append(pid)
    sess._pulse_vision_intel()

    harv = sess.entities["harvester_p1"]
    harv.x, harv.y = bx, by
    sess.consume_probes_at(bx, by, crusher_owner="p1")

    assert not [
        e for e in sess.entities.values() if e.entity_type == "probe"
    ], "every probe on the cell should be crushed"
    for viewer in ("p1", "p2"):
        snap = (sess.probe_intel.get(viewer) or {}).get(_xy_key(bx, by))
        if not snap:
            continue
        seen = {o.get("id") for o in (snap.get("occupants") or [])}
        assert not (seen & set(ids)), (
            f"{viewer} kept a dead probe in echo: {seen & set(ids)}"
        )
