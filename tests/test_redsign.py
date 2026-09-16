"""Redsign discovery-beacon tests (RULEBOOK §4.11).

Redsign is a DISCOVERY-TRIGGERED, PERSISTENT, fog-independent public
beacon. UNLIKE blue-sign (static at birth), a region is minted the first
time ANY seat's probe/harvester sees a pure-RED cell (purity 255). From
then on it is visible to EVERY house regardless of fog, persists for the
rest of the season (even after the seam is harvested), stays anonymous,
and paints a fuzzy off-centre smear that never reveals the exact squares.
"""

from __future__ import annotations

from sea_of_colours.game.entities import Entity
from sea_of_colours.game.session import GameSession
from sea_of_colours.generator import Cell, Tile
from sea_of_colours.snowpark.view import build_agent_view

_SEED = 42


def _blank_session(w: int = 20, h: int = 14, seed: int = _SEED) -> GameSession:
    """Fresh session with an all-EMPTY grid so the only pure-RED cell is
    the one a test plants — keeps discovery deterministic."""
    sess = GameSession.new(w, h, seed=seed)
    for row in sess.grid:
        for i in range(len(row)):
            row[i] = Cell(Tile.EMPTY, 0)
    # Drop the birth entities so their vision disks don't pulse anything.
    sess.entities.clear()
    return sess


def _plant_pure_red(sess: GameSession, x: int, y: int) -> None:
    sess.grid[y][x] = Cell(Tile.RED, 255)


def _place_probe(sess: GameSession, x: int, y: int, owner: str = "p1") -> None:
    uid = f"probe_{owner}_test_{x}_{y}"
    sess.entities[uid] = Entity(uid, "probe", owner, x, y)


def _place_harvester(
    sess: GameSession, uid: str, x: int, y: int, owner: str = "p1",
) -> str:
    sess.entities[uid] = Entity(uid, "harvester", owner, x, y)
    return uid


def test_redsign_triggers_when_probe_sees_pure_red() -> None:
    sess = _blank_session()
    _plant_pure_red(sess, 10, 7)
    _place_probe(sess, 10, 7)
    assert sess.redsign == []
    sess._pulse_vision_intel()
    assert len(sess.redsign) == 1
    r = sess.redsign[0]
    assert set(r.keys()) >= {"id", "center", "cells", "day", "hour"}
    assert r["cells"], "region paints at least one cell"
    for x, y, intensity in r["cells"]:
        assert 0 <= x < sess.width
        assert 0 <= y < sess.height
        assert 0.0 < float(intensity) <= 1.0
    assert "10,7" in sess.redsign_seen


def test_redsign_records_discovery_day() -> None:
    sess = _blank_session()
    sess.day = 4
    _plant_pure_red(sess, 8, 6)
    _place_probe(sess, 8, 6)
    sess._pulse_vision_intel()
    assert sess.redsign[0]["day"] == 4


def test_redsign_does_not_retrigger_on_reobservation() -> None:
    sess = _blank_session()
    _plant_pure_red(sess, 10, 7)
    _place_probe(sess, 10, 7)
    sess._pulse_vision_intel()
    sess._pulse_vision_intel()
    sess._pulse_vision_intel()
    assert len(sess.redsign) == 1


def test_redsign_seam_mints_single_region() -> None:
    """A connected pure-RED seam yields ONE beacon, not one per cell."""
    sess = _blank_session()
    for x in range(9, 13):
        for y in range(6, 9):
            _plant_pure_red(sess, x, y)
    _place_probe(sess, 10, 7)
    sess._pulse_vision_intel()
    assert len(sess.redsign) == 1
    # Every cell of the seam is marked seen so nothing re-triggers.
    assert "9,6" in sess.redsign_seen and "12,8" in sess.redsign_seen


def test_redsign_ignores_impure_red() -> None:
    sess = _blank_session()
    sess.grid[7][10] = Cell(Tile.RED, 254)
    _place_probe(sess, 10, 7)
    sess._pulse_vision_intel()
    assert sess.redsign == []


def test_redsign_requires_vision() -> None:
    """A pure-RED cell no unit can see does NOT mint a beacon."""
    sess = _blank_session()
    _plant_pure_red(sess, 2, 2)
    _place_probe(sess, 18, 12)  # far away, radius-2 disk can't reach
    sess._pulse_vision_intel()
    assert sess.redsign == []


def test_redsign_triggered_by_harvester_vision() -> None:
    sess = _blank_session()
    _plant_pure_red(sess, 10, 7)
    uid = "harvester_p2_test"
    sess.entities[uid] = Entity(uid, "harvester", "p2", 10, 7)
    sess._pulse_vision_intel()
    assert len(sess.redsign) == 1


def test_redsign_is_global_and_fog_independent() -> None:
    """The finder is p1; p2 (no vision there) still sees the beacon.

    v9: the beacon GEOMETRY (id/center/cells/day/hour) is identical for every
    seat regardless of fog, but each seat's view carries a private ``mine``
    flag and the engine-internal ``discoverer`` identity is stripped so a
    rival can't tell who found it.
    """
    sess = _blank_session()
    sess.visibility_mode = "hidden"
    _plant_pure_red(sess, 10, 7)
    _place_probe(sess, 10, 7, owner="p1")
    sess._pulse_vision_intel()
    v1 = build_agent_view(sess, "p1")
    v2 = build_agent_view(sess, "p2")
    assert v1["redsign"], "redsign must survive fog for non-finders"

    _geom = lambda r: {k: r[k] for k in ("id", "center", "cells", "day", "hour")}
    assert _geom(v1["redsign"][0]) == _geom(v2["redsign"][0])

    # Per-seat ownership: p1 found it (mine=True); p2 did not (mine=False).
    assert v1["redsign"][0]["mine"] is True
    assert v2["redsign"][0]["mine"] is False
    # Discoverer identity is engine-internal, never leaked to any seat's view.
    for v in (v1, v2):
        assert "discoverer" not in v["redsign"][0]
        assert "co_discoverers" not in v["redsign"][0]
    # The raw region DOES carry the attribution for the engine's own use.
    assert sess.redsign[0]["discoverer"] == "p1"


def test_redsign_survives_to_dict_roundtrip() -> None:
    sess = _blank_session()
    _plant_pure_red(sess, 10, 7)
    _place_probe(sess, 10, 7)
    sess._pulse_vision_intel()
    revived = GameSession.from_dict(sess.to_dict())
    assert revived.redsign == sess.redsign
    assert revived.redsign_seen == sess.redsign_seen


def test_redsign_record_persists_but_goes_dead_after_seam_gone() -> None:
    """v9 (I12): the beacon RECORD stays in ``sess.redsign`` (replay/history),
    but once the seam is no longer pure it is marked dead (``live=False``) and
    re-observing the now-empty cell does not double-count or resurrect it."""
    sess = _blank_session()
    _plant_pure_red(sess, 10, 7)
    _place_probe(sess, 10, 7)
    sess._pulse_vision_intel()
    assert sess.redsign[0]["live"] is True
    sess.grid[7][10] = Cell(Tile.EMPTY, 0)  # pure gone (direct edit)
    sess._pulse_vision_intel()
    assert len(sess.redsign) == 1, "record persists for replay/history"
    assert sess.redsign[0]["live"] is False
    assert sess.redsign[0]["spent_day"] == int(sess.day)


def test_redsign_retires_via_harvest_and_leaves_seat_view() -> None:
    """Harvesting the pure through the real engine path (_harvest_at) turns the
    beacon OFF: it is marked dead, stamped with the harvesting seat, and drops
    out of EVERY seat's view so nobody keeps chasing a spent seam."""
    sess = _blank_session()
    sess.visibility_mode = "hidden"
    _plant_pure_red(sess, 10, 7)
    _place_probe(sess, 10, 7, owner="p1")
    sess._pulse_vision_intel()
    assert build_agent_view(sess, "p1")["redsign"], "beacon live pre-harvest"

    _place_harvester(sess, "harvester_p2_x", 10, 7, owner="p2")
    harvested, _sid = sess._harvest_at("p2", "harvester_p2_x", 10, 7)
    assert harvested
    region = sess.redsign[0]
    assert region["live"] is False
    assert region["spent_day"] == int(sess.day)
    assert region["spent_by"] == "p2"
    # Gone from every seat's projected view.
    assert build_agent_view(sess, "p1")["redsign"] == []
    assert build_agent_view(sess, "p2")["redsign"] == []


def test_redsign_stays_live_until_last_pure_of_seam_gone() -> None:
    """A multi-cell seam keeps broadcasting until its LAST pure cell is mined."""
    sess = _blank_session()
    for x in range(9, 12):  # 3-cell horizontal seam at y=7
        _plant_pure_red(sess, x, 7)
    _place_probe(sess, 10, 7)
    _place_harvester(sess, "h1", 10, 7, owner="p1")
    sess._pulse_vision_intel()
    assert len(sess.redsign) == 1
    sess._harvest_at("p1", "h1", 9, 7)
    assert sess.redsign[0]["live"] is True, "still pure cells remain"
    sess._harvest_at("p1", "h1", 10, 7)
    assert sess.redsign[0]["live"] is True
    sess._harvest_at("p1", "h1", 11, 7)
    assert sess.redsign[0]["live"] is False, "last pure gone → beacon dead"


def test_redsign_pure_cells_never_leak_into_view() -> None:
    """The engine-internal ``pure_cells`` (exact pures) must not appear in any
    seat's view — only the fuzzy smear ``cells``."""
    sess = _blank_session()
    _plant_pure_red(sess, 10, 7)
    _place_probe(sess, 10, 7, owner="p1")
    sess._pulse_vision_intel()
    assert "pure_cells" in sess.redsign[0], "engine keeps it internally"
    for pid in ("p1", "p2"):
        v = build_agent_view(sess, pid)
        assert v["redsign"], "beacon still live"
        assert "pure_cells" not in v["redsign"][0]


def test_redsign_deterministic_smear_for_seed_and_location() -> None:
    """Same seed + same seam ⇒ identical smear across two sessions."""
    a = _blank_session(seed=_SEED)
    _plant_pure_red(a, 10, 7)
    _place_probe(a, 10, 7)
    a._pulse_vision_intel()
    b = _blank_session(seed=_SEED)
    _plant_pure_red(b, 10, 7)
    _place_probe(b, 10, 7)
    b._pulse_vision_intel()
    assert a.redsign == b.redsign


# ── RED_HARVEST sign-guided probe scouting (§4.10 / §4.11) ─────────────


def _scout_view(**over):
    """Minimal ``_plan_probe_drops`` view: a small live patch in one corner
    so ``fog_cells`` is populated, everything else fog. Sign centres placed
    in fog so the heuristic can aim a scouting probe at them."""
    live = [{"x": x, "y": y} for y in range(3) for x in range(3)]
    view = {
        "grid": {"width": 24, "height": 18},
        "red_tiles": [],
        "green_tiles": [],
        "blue_tiles": [],
        "fog_clusters": [],
        "world": {"live": live, "echo": []},
        "entities": {"mine": [], "echoes": []},
        "orbit": {},
        "meta": {"session_id": "S-scout", "player": "p1", "day": 2},
        "hud": {"player": "p1", "day": 2},
    }
    view.update(over)
    return view


def test_heuristic_probes_toward_redsign_jackpot() -> None:
    from sea_of_colours.agent.heuristic_agent import _plan_probe_drops

    view = _scout_view(
        redsign=[{"id": "redsign-0", "center": [18, 12],
                  "cells": [[18, 12, 1.0]], "day": 2, "hour": 5}],
    )
    probes = _plan_probe_drops(view, max_probes=2)
    ats = [tuple(p["at"]) for p in probes]
    assert (18, 12) in ats, f"expected a jackpot probe at the redsign centre, got {ats}"


def test_heuristic_skips_redsign_already_revealed() -> None:
    """A persistent redsign whose centre is already in live vision must not
    keep burning a probe every night."""
    from sea_of_colours.agent.heuristic_agent import _plan_probe_drops

    # Centre (1,1) sits inside the 3x3 live patch → not fog → skip.
    view = _scout_view(
        redsign=[{"id": "redsign-0", "center": [1, 1],
                  "cells": [[1, 1, 1.0]], "day": 2, "hour": 5}],
    )
    probes = _plan_probe_drops(view, max_probes=2)
    assert (1, 1) not in [tuple(p["at"]) for p in probes]


def test_heuristic_probes_toward_blue_sign_when_low_blue() -> None:
    from sea_of_colours.agent.heuristic_agent import _plan_probe_drops

    view = _scout_view(
        blue_sign=[{"id": "bluesign-0", "center": [20, 4],
                    "cells": [[20, 4, 1.0]]}],
        orbit={"blue_purity_total": 0},  # dry weapons economy
    )
    probes = _plan_probe_drops(view, max_probes=2)
    assert (20, 4) in [tuple(p["at"]) for p in probes]


def test_heuristic_ignores_blue_sign_when_blue_healthy() -> None:
    from sea_of_colours.agent.heuristic_agent import _plan_probe_drops

    view = _scout_view(
        blue_sign=[{"id": "bluesign-0", "center": [20, 4],
                    "cells": [[20, 4, 1.0]]}],
        orbit={"blue_purity_total": 9999},  # plenty of blue → no diversion
    )
    probes = _plan_probe_drops(view, max_probes=2)
    assert (20, 4) not in [tuple(p["at"]) for p in probes]
