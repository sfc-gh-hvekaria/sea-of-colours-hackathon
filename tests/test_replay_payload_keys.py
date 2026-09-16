"""The /replay HTTP projection must not drop engine fields.

``server.app.api_game_replay`` does not return the engine reply — it
rebuilds the response key by key, deliberately, to strip two big sources
of duplication that could OOM a browser tab. The cost of that design is
that anything added to :func:`soc_engine.get_replay` is invisible over
HTTP until someone remembers to widen the projection.

That is not hypothetical: ``seed`` and ``extraction`` were added to the
engine in v1.20 for the replay header's SEED / RED ON MAP / EXTRACTED
readout and never added here, so the strip had no data and hid itself
from the day it shipped until v1.22.

These tests pin the fields the client actually reads, and fail loudly
when the engine grows a key the route forgets.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SOC_BACKEND", "memory")

from fastapi.testclient import TestClient  # noqa: E402

from server.app import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def finished_session(client):
    """A tiny bot-vs-bot season, played to the end."""
    res = client.post("/api/game/new", json={
        "width": 24, "height": 16, "season_day_cap": 2,
        "players": ["p1", "p2"],
        "agents": {"p1": "red_harvest", "p2": "red_harvest"},
        "visibility_mode": "hidden",
    })
    assert res.status_code == 200, res.text
    sid = res.json()["session_id"]
    for _ in range(40):
        st = client.get(f"/api/game/{sid}/status").json()
        if st.get("phase") == "season_complete":
            break
        if client.post(f"/api/game/{sid}/bots", json={}).status_code != 200:
            break
    return sid


def test_replay_carries_seed_and_extraction(client, finished_session):
    """The header readout's two inputs survive the projection."""
    body = client.get(f"/api/game/{finished_session}/replay").json()

    assert body.get("seed") is not None, "seed dropped by the /replay route"

    x = body.get("extraction")
    assert x, "extraction dropped by the /replay route"
    # The strip hides itself unless this is positive, so a zero here is
    # indistinguishable from the field being missing.
    assert x["map_red_value"] > 0
    assert x["map_red_cells"] > 0
    assert isinstance(x.get("harvested_by_day"), dict)


def test_replay_projection_matches_the_engine(client, finished_session):
    """Every key the engine offers is either forwarded or knowingly dropped.

    The drops are the payload-size guard the route exists for. Anything
    new must be added to one list or the other, which is the prompt this
    test is here to deliver.
    """
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import engine as soc_engine

    store = soc_backend.store_for_session(finished_session)
    engine_keys = set(soc_engine.get_replay(store, finished_session))
    http_keys = set(client.get(f"/api/game/{finished_session}/replay").json())

    # Stripped on purpose — see the comment in ``api_game_replay``.
    deliberately_dropped = {
        "session_id",     # the caller already knows it
        "season_name",    # served by /status
        "day_index_full",
        "frames_compact",
    }
    missing = engine_keys - http_keys - deliberately_dropped
    assert not missing, (
        "get_replay grew keys the HTTP route does not forward: "
        f"{sorted(missing)}. Add them to api_game_replay, or to this "
        "test's deliberately_dropped set if the client must not see them."
    )


def test_extraction_is_cumulative_and_bounded(client, finished_session):
    """harvested_by_day is a per-seat running total, never above the map."""
    x = client.get(f"/api/game/{finished_session}/replay").json()["extraction"]
    total = x["map_red_value"]
    for seat, series in x["harvested_by_day"].items():
        assert series == sorted(series), f"{seat} extraction curve went down"
        assert series[-1] <= total, f"{seat} lifted more RED than the map had"
