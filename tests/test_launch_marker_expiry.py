"""A probe launch marker must not outlive the probe's schedule (§3.15).

Bug #17. v1.2 deliberately lets a §3.15 launch marker survive a probe's
crush / collision / supersede for Houses that did not witness it — you saw
it land, you did not see it die, so you keep "last known position". The only
bound on that is the night the probe was always going to expire, and v1.2
says the sweep runs "at every Aurora".

It did not. Two holes, both leaving a seat staring at a probe that had been
gone for the rest of the season:

* The sweep sat behind an early ``return`` taken when no *live* probe
  happened to expire that dawn, so a marker only cleared if some unrelated
  probe died on the same night. With nothing else on the board it never
  cleared at all.
* The sweep matched ``via == "probe_launch"`` only. A launch onto a cell the
  viewer had already scouted is merged onto that richer terrain echo (v1.11)
  and leaves only a ``probe_launch_glyph`` overlay, so those were walked
  past entirely — the exact case v1.11 flagged as lingering "forever as a
  permanent ghost".

These tests drive ``decay_probes`` directly, one call per dawn, because that
is the hook the night simulator uses.
"""

from __future__ import annotations

from typing import Any, Optional

from sea_of_colours.game import session as session_mod
from sea_of_colours.game.session import (
    GameSession,
    Phase,
    _probe_vision_disk,
    _xy_key,
)
from sea_of_colours.game.tuning import probe_lifetime_nights

CELL = (10, 8)
KEY = _xy_key(*CELL)
LIFETIME = probe_lifetime_nights() or 3


def _fresh() -> GameSession:
    sess = GameSession.new(24, 16, seed=77)
    sess.phase = Phase.PLANNING
    sess.probe_intel = {p: {} for p in sess.players}
    sess.day = 1
    return sess


def _secretly_kill(sess: GameSession, probe_id: str) -> None:
    """Destruction the marker's holder never witnessed.

    Dropping the entity is the honest stand-in: crush, collision and
    supersede all reach the same state for a non-witness — probe gone from
    ``entities``, marker untouched, ledger row intact.
    """
    del sess.entities[probe_id]


def _probe_id(sess: GameSession, owner: str) -> str:
    return next(
        e.id for e in sess.entities.values()
        if e.entity_type == "probe" and e.owner == owner
    )


def _dawn(sess: GameSession, day: int) -> None:
    sess.day = day
    sess.decay_probes()


def _marker(sess: GameSession, viewer: str = "p1") -> Optional[dict[str, Any]]:
    return (sess.probe_intel.get(viewer) or {}).get(KEY)


def test_the_launch_is_published_in_the_first_place():
    """Guard on the §3.15 publication itself."""
    sess = _fresh()
    ok, _ = sess.spawn_probe("p2", *CELL)
    assert ok
    assert _marker(sess) is not None, "a launch must be public to the rival"


def test_a_secret_death_keeps_the_marker_until_its_scheduled_dawn():
    """The v1.2 rule this must not regress: no early reveal."""
    sess = _fresh()
    sess.spawn_probe("p2", *CELL)
    _secretly_kill(sess, _probe_id(sess, "p2"))

    for day in range(1, LIFETIME):
        _dawn(sess, day)
        assert _marker(sess) is not None, (
            f"day {day} is inside the probe's {LIFETIME}-night life; the "
            "non-witness must still see its last known position"
        )


def test_a_lone_secretly_killed_probe_still_expires():
    """No other probe on the board, so nothing else can trigger the sweep."""
    sess = _fresh()
    sess.spawn_probe("p2", *CELL)
    _secretly_kill(sess, _probe_id(sess, "p2"))

    for day in range(1, LIFETIME + 3):
        _dawn(sess, day)
    assert _marker(sess) is None, (
        f"marker outlived its {LIFETIME}-night schedule with no other probe "
        "around to trigger the sweep"
    )


def test_the_marker_goes_on_exactly_the_scheduled_dawn():
    sess = _fresh()
    sess.spawn_probe("p2", *CELL)
    _secretly_kill(sess, _probe_id(sess, "p2"))

    _dawn(sess, LIFETIME - 1)
    assert _marker(sess) is not None, "not due yet"
    _dawn(sess, LIFETIME)
    assert _marker(sess) is None, f"due on day {LIFETIME}"


def test_a_live_probes_marker_is_never_retired():
    """Expiry belongs to the lifetime pass; the sweep must not touch a
    probe that is still standing."""
    sess = _fresh()
    sess.spawn_probe("p2", *CELL)
    for day in range(1, LIFETIME):
        _dawn(sess, day)
        assert _marker(sess) is not None, f"probe still alive on day {day}"


def _merged_setup(sess: GameSession) -> str:
    """Give p1 a real terrain echo on CELL, then have p2 launch into it.

    The merge (v1.11) preserves p1's richer snapshot and hangs the enemy
    probe off it as a glyph overlay, which is the shape the old sweep missed.
    """
    sess.spawn_probe("p1", 12, 8)
    assert CELL in _probe_vision_disk(12, 8, sess.width, sess.height)
    sess._pulse_vision_intel()
    assert _marker(sess) is not None and "purity" in _marker(sess), (
        "setup should leave p1 holding a terrain snapshot, not a bare marker"
    )
    sess.spawn_probe("p2", *CELL)
    entry = _marker(sess)
    assert entry is not None and entry.get("probe_launch_glyph"), (
        f"expected a merged glyph overlay, got {entry!r}"
    )
    return _probe_id(sess, "p2")


def test_a_merged_launch_glyph_expires_too():
    sess = _fresh()
    _secretly_kill(sess, _merged_setup(sess))

    for day in range(1, LIFETIME + 3):
        _dawn(sess, day)
    entry = _marker(sess)
    assert entry is not None, "p1's own terrain echo must not be deleted"
    assert not entry.get("probe_launch_glyph"), (
        "the enemy probe glyph outlived its schedule as a permanent ghost"
    )


def test_expiring_the_overlay_leaves_the_viewers_own_intel_intact():
    """The overlay is the rival's; the terrain underneath is p1's own work."""
    sess = _fresh()
    probe_id = _merged_setup(sess)
    before = dict(_marker(sess) or {})
    _secretly_kill(sess, probe_id)

    for day in range(1, LIFETIME + 3):
        _dawn(sess, day)
    after = _marker(sess) or {}

    for field_name in ("tile", "purity"):
        assert after.get(field_name) == before.get(field_name), (
            f"{field_name} changed: {before.get(field_name)!r} -> "
            f"{after.get(field_name)!r}"
        )
    ids = {o.get("id") for o in (after.get("occupants") or []) if isinstance(o, dict)}
    assert probe_id not in ids, f"dead probe still listed as an occupant: {ids}"


def test_nothing_expires_when_probe_decay_is_switched_off(monkeypatch):
    """``SOC_PROBE_LIFETIME_NIGHTS`` off means probes live forever, so their
    markers must too — there is no schedule to clear against."""
    monkeypatch.setattr(session_mod, "probe_lifetime_nights", lambda: None)
    sess = _fresh()
    sess.spawn_probe("p2", *CELL)
    _secretly_kill(sess, _probe_id(sess, "p2"))

    for day in range(1, LIFETIME + 5):
        _dawn(sess, day)
    assert _marker(sess) is not None, (
        "with decay off there is no expiry date, so the marker stands"
    )
