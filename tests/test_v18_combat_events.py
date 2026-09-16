"""v1.8 — canonical combat-event feed + decaying EMP scar.

Covers the ephemeral-weapon surface an agent reads instead of trying to
reconstruct dissipated clouds:

* ``last_night.combat_events`` — PUBLIC ``emp`` / ``chaff`` events plus the
  VICTIM-only ``emp_hit`` / ``chaff_jam`` impact rows (fog-gated).
* ``emp_marks`` — the decaying spatial scar surfaced as ``cell["combat"]["emp"]``
  on the dense (agent + human) and observer views.
"""

from __future__ import annotations

from sea_of_colours.game.policy import (
    ChaffFlareMove,
    EmpLaunchMove,
    StepMove,
)
from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.game.simulator import NightSimulator
from sea_of_colours.snowpark.view import build_agent_view


def _fresh_night_session(seed: int = 7) -> GameSession:
    sess = GameSession.new(20, 14, seed=seed)
    assert sess.phase == Phase.PLANNING
    for p in sess.players:
        sess.blue_bank[p] = 0
    return sess


def _stock_weapon(sess: GameSession, owner: str, kind: str, n: int = 1) -> None:
    slot = sess.weapon_stock.setdefault(owner, {"emp": 0, "chaff": 0})
    slot[kind] = int(slot.get(kind, 0)) + int(n)


def _events(sess: GameSession, day: int) -> list:
    return (sess.combat_events_by_day or {}).get(str(day), [])


def _of_type(events: list, etype: str) -> list:
    return [e for e in events if e.get("type") == etype]


def test_emp_launch_records_public_event_with_cells_and_hours() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    day = int(sess.day)

    NightSimulator().run(sess, {"p1": [EmpLaunchMove(at=(8, 8))]})

    emp = _of_type(_events(sess, day), "emp")
    assert len(emp) == 1, emp
    ev = emp[0]
    assert ev["owner"] == "p1"
    assert [8, 8] in ev["targets"]
    assert ev["radius"] >= 1
    # The union of cloud cells includes the centre and Manhattan neighbours.
    assert [8, 8] in ev["cells"]
    assert [7, 8] in ev["cells"] and [9, 8] in ev["cells"]
    # Hours span the cloud lifetime, starting at the launch hour.
    assert ev["hours"] and ev["hours"] == list(
        range(ev["hours"][0], ev["hours"][0] + len(ev["hours"]))
    )


def test_emp_scar_stamped_on_views_and_decays() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    day = int(sess.day)

    NightSimulator().run(sess, {"p1": [EmpLaunchMove(at=(8, 8))]})

    key = "8:8"
    assert key in sess.emp_marks
    scar = sess.emp_marks[key]
    assert scar["owners"] == ["p1"]
    assert scar["hours"] and scar["day"] == day

    # Surfaced on the omniscient observer view (row-major) as a per-cell
    # scar. (Per-cell scars are vision-gated on the fog player/agent views,
    # exactly like the live EMP cloud; the PUBLIC footprint reaches an
    # agent regardless of fog via last_night.combat_events' ``cells``.)
    obs = sess.observer_cells_rowmajor()
    cell = obs[8 + 8 * sess.width]
    assert cell.get("combat", {}).get("emp", {}).get("owners") == ["p1"]
    assert cell["combat"]["emp"]["hours"]

    # Decays after one full game day (mirrors collision marks).
    sess.day = day + 2
    sess._prune_emp_marks()
    assert key not in sess.emp_marks


def test_emp_hit_is_victim_private_in_last_night() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 8, 8
    h2.damaged = False
    day = int(sess.day)

    NightSimulator().run(sess, {
        "p1": [EmpLaunchMove(at=(8, 8))],
        "p2": [
            StepMove(unit="harvester_p2", to=(7, 8)),
            StepMove(unit="harvester_p2", to=(9, 8)),
        ],
    })

    hits = _of_type(_events(sess, day), "emp_hit")
    assert len(hits) == 1, hits
    hit = hits[0]
    assert hit["unit"] == "harvester_p2"
    assert hit["victim"] == "p2"
    assert "p1" in hit["by"]
    assert hit["hours"], hit

    # Victim sees the emp_hit in its recap; the attacker does NOT.
    p2_events = build_agent_view(sess, "p2")["last_night"]["combat_events"]
    assert any(e["type"] == "emp_hit" for e in p2_events)
    p1_events = build_agent_view(sess, "p1")["last_night"]["combat_events"]
    assert not any(e["type"] == "emp_hit" for e in p1_events)
    # Both seats DO see the public EMP launch event.
    assert any(e["type"] == "emp" for e in p1_events)
    assert any(e["type"] == "emp" for e in p2_events)


def test_chaff_flare_and_jam_recorded() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "chaff", n=1)
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 5, 5
    h2.damaged = False
    day = int(sess.day)

    NightSimulator().run(sess, {
        "p1": [ChaffFlareMove()],
        "p2": [StepMove(unit="harvester_p2", to=(5, 6))],
    })

    flares = _of_type(_events(sess, day), "chaff")
    assert len(flares) == 1 and flares[0]["owner"] == "p1"
    assert flares[0]["hours"], flares
    # Chaff has no location — no cells key.
    assert "cells" not in flares[0]

    jams = _of_type(_events(sess, day), "chaff_jam")
    assert jams and jams[0]["victim"] == "p2"
    assert "p1" in jams[0]["by"]

    # Fog-gating: victim sees the jam; attacker only the public flare.
    p2_events = build_agent_view(sess, "p2")["last_night"]["combat_events"]
    assert any(e["type"] == "chaff_jam" for e in p2_events)
    p1_events = build_agent_view(sess, "p1")["last_night"]["combat_events"]
    assert not any(e["type"] == "chaff_jam" for e in p1_events)
    assert any(e["type"] == "chaff" for e in p1_events)


def test_combat_feed_round_trips() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    NightSimulator().run(sess, {"p1": [EmpLaunchMove(at=(8, 8))]})

    restored = GameSession.from_dict(sess.to_dict())

    assert restored.combat_events_by_day == sess.combat_events_by_day
    assert restored.emp_marks == sess.emp_marks
