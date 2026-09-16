"""ASK V12 (read-only advisor) — the guards that keep it read-only.

The advisor exists to answer "what would V12 do here?" for a seat a human
is driving. Everything worth pinning is about what it must NOT do:

* never play the turn (``submit=False``, plus a server-side assertion),
* never write V12's per-seat memory (a human's moves must not come back
  next night as "what V12 did"),
* never appear on a game that can't honour it.

The happy path needs a live Cortex round trip, so these tests drive the
gate and the read-only contract with the harness stubbed — the parts that
can regress silently, as opposed to the parts that fail loudly.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SOC_BACKEND", "memory")

from fastapi.testclient import TestClient

from sea_of_colours.snowpark import backend as soc_backend


@pytest.fixture()
def client():
    soc_backend.reset_for_tests()
    from server.app import app

    return TestClient(app)


@pytest.fixture()
def game(client):
    return client.post(
        "/api/game/new", params={"seed": 5, "width": 16, "height": 10},
    ).json()["session_id"]


def test_a_memory_game_is_not_offered_the_advisor(client, game):
    """LLM seats are a Snowflake feature; the advisor follows that rule.

    Offering the button on a memory game would contradict the seat
    dropdown of the very same game, where ``tabula_v12`` isn't listed.
    """
    body = client.get(f"/api/game/{game}/advisor").json()
    assert body["available"] is False
    assert "memory" in body["reason"].lower()


def test_asking_anyway_is_refused_rather_than_half_answered(client, game):
    """The POST re-checks the gate — a client that skipped the GET, or an
    operator with curl, must not get a credential-less fallback plan
    dressed up as V12's opinion."""
    res = client.post(f"/api/game/{game}/advisor", params={"player": "p1"})
    assert res.status_code == 409
    assert "memory" in res.json()["detail"].lower()


def test_the_advisor_never_runs_during_orbit(client, game, monkeypatch):
    """The harness's orbit branch submits — it has no read-only path.

    Routing an orbit turn to it would play the seat's whole catapult
    turn, which is the one outcome this endpoint exists to prevent. The
    gate has to sit in front of the harness, not inside it.
    """
    import server.app as srv

    monkeypatch.setattr(srv, "_advisor_availability", lambda gid: (True, ""))

    # Report the seat as mid-ORBIT. Steering a real game there and holding
    # it is fragile (empty night orders roll straight back round to
    # planning), and the guard under test reads exactly this one field.
    real_status = srv.soc_engine.get_session_status
    monkeypatch.setattr(
        srv.soc_engine, "get_session_status",
        lambda store, gid: {**real_status(store, gid), "phase": "orbit"},
    )

    called = []
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import harness as v12

    monkeypatch.setattr(v12, "run", lambda **kw: called.append(kw) or {})

    res = client.post(f"/api/game/{game}/advisor", params={"player": "p1"})
    assert res.status_code == 409
    assert "orbit" in res.json()["detail"]
    assert not called, "the harness must not be reached in ORBIT"


def test_the_harness_is_asked_read_only_and_writes_no_memory(
    client, game, monkeypatch,
):
    """The single most important line in the endpoint is ``submit=False``.

    With ``submit=True`` the harness commits the policy AND persists the
    seat's journal / hazard memory / reflection anchor — inventing a V12
    history for a seat V12 isn't playing, and feeding the human's own
    moves back as V12's supposed last night.
    """
    import server.app as srv

    monkeypatch.setattr(srv, "_advisor_availability", lambda gid: (True, ""))

    seen = {}
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import harness as v12

    def fake_run(**kw):
        seen.update(kw)
        return {
            "moves": [{"a": "probe", "at": [3, 4]}],
            "submitted_policy": False,
            "extras": {
                "thinker_reasoning": "seam at 3,4 is the cheapest read",
                "thinker_directive": {"posture": "greedy", "plan": ["probe_seam"]},
            },
        }

    monkeypatch.setattr(v12, "run", fake_run)

    body = client.post(
        f"/api/game/{game}/advisor", params={"player": "p1"},
    ).json()

    assert seen["submit"] is False
    assert seen["player"] == "p1"
    assert body["moves"] == [{"a": "probe", "at": [3, 4]}]
    assert body["plan"]["posture"] == "greedy"
    assert body["thinking"]["reasoning"].startswith("seam at 3,4")

    # And the seat is still waiting on its human.
    assert client.get(f"/api/game/{game}/status").json()["phase"] == "planning"


def test_a_harness_that_submitted_is_reported_as_a_fault(
    client, game, monkeypatch,
):
    """Belt and braces for the read-only contract.

    If a future edit to the harness ever makes this path submit, the
    endpoint must fail loudly rather than hand back a "suggestion" that
    has in fact already been played.
    """
    import server.app as srv

    monkeypatch.setattr(srv, "_advisor_availability", lambda gid: (True, ""))

    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import harness as v12

    monkeypatch.setattr(
        v12, "run",
        lambda **kw: {"moves": [], "submitted_policy": True, "extras": {}},
    )

    res = client.post(f"/api/game/{game}/advisor", params={"player": "p1"})
    assert res.status_code == 500
    assert "submitted" in res.json()["detail"]


def test_a_seat_that_isnt_playing_is_rejected_before_any_think(
    client, game, monkeypatch,
):
    """A think is an expensive live model call, so both flavours of bad
    seat are turned away in front of it: a malformed slug (400, matching
    the other seat-taking routes) and a well-formed slug that this game
    never seated (404)."""
    import server.app as srv

    monkeypatch.setattr(srv, "_advisor_availability", lambda gid: (True, ""))

    assert client.post(
        f"/api/game/{game}/advisor", params={"player": "p9"},
    ).status_code == 400

    seated = client.get(f"/api/game/{game}/status").json()["players"]
    spare = next(s for s in ("p2", "p3", "p4") if s not in seated)
    assert client.post(
        f"/api/game/{game}/advisor", params={"player": spare},
    ).status_code == 404
