"""FastAPI proxy → SOC engine integration tests.

Confirms that every game route in :mod:`server.app` round-trips through
the snowpark engine layer end-to-end. The test installs the in-memory
backend (``SOC_BACKEND=memory``) so no live Snowflake account is
required, and resets the shared store between tests so sessions don't
leak across cases.
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


def test_new_status_view_observer_pipeline(client):
    new = client.post(
        "/api/game/new", params={"seed": 11, "width": 16, "height": 10},
    )
    assert new.status_code == 200
    sid = new.json()["session_id"]
    assert new.json()["day"] == 1

    status = client.get(f"/api/game/{sid}/status").json()
    assert status["day"] == 1
    assert status["phase"] == "planning"

    view = client.get(f"/api/game/{sid}/view", params={"player": "p1"}).json()
    assert view["mode"] == "player"
    assert len(view["cells"]) == 16 * 10
    assert "agent_view" in view
    assert "grid_ascii" in view["agent_view"]

    obs = client.get(f"/api/game/{sid}/observer").json()
    assert obs["mode"] == "observer"
    assert len(obs["cells"]) == 16 * 10


def test_policy_resolves_when_both_seats_ready(client):
    sid = client.post(
        "/api/game/new", params={"seed": 27, "width": 18, "height": 12},
    ).json()["session_id"]

    r1 = client.post(
        f"/api/game/{sid}/policy",
        json={"player": "p1", "moves": []},
    ).json()
    assert r1["ok"]
    assert r1["pending"] == {"p1": True, "p2": False}
    assert r1["night_resolved"] is False

    r2 = client.post(
        f"/api/game/{sid}/policy",
        json={"player": "p2", "moves": []},
    ).json()
    assert r2["ok"]
    assert r2["night_resolved"] is True
    assert r2["day"] == 2


@pytest.mark.parametrize(
    "route, good, bad",
    [("policy", "moves", "policy"), ("orbit", "actions", "orders")],
)
def test_a_queue_under_the_wrong_field_name_is_refused(client, route, good, bad):
    """A typo must not read as "this seat is passing".

    Both submit routes used to fall back to an empty queue for any
    unrecognised field, so a misspelling locked a no-op turn and still
    answered ``ok`` — indistinguishable from a deliberate pass, and no
    way to tell from the response that the orders were thrown away.
    Attendees drive these endpoints by hand from their own harnesses.
    """
    sid = client.post(
        "/api/game/new", params={"seed": 41, "width": 16, "height": 10},
    ).json()["session_id"]

    r = client.post(
        f"/api/game/{sid}/{route}",
        json={"player": "p1", bad: [{"a": "probe", "at": [4, 4]}]},
    )
    assert r.status_code == 400
    assert bad in r.json()["detail"] and good in r.json()["detail"]

    # Omitting the queue is still how you pass, so the guard must not
    # fire on it. (Whether the engine then accepts it is a phase
    # question — an orbit submission on a planning board is a legitimate
    # `ok: false` — so this only pins that we got past the field check.)
    ok = client.post(f"/api/game/{sid}/{route}", json={"player": "p1"})
    assert ok.status_code == 200


def test_replay_and_day_index_populate(client):
    sid = client.post(
        "/api/game/new", params={"seed": 33, "width": 16, "height": 10},
    ).json()["session_id"]
    client.post(f"/api/game/{sid}/policy", json={"player": "p1", "moves": []})
    client.post(f"/api/game/{sid}/policy", json={"player": "p2", "moves": []})

    replay = client.get(f"/api/game/{sid}/replay").json()
    assert replay["total_frames"] >= 1
    assert [d["day"] for d in replay["days"]] == [1]

    day_index = client.get(f"/api/game/{sid}/day-index").json()
    assert day_index["session_id"] == sid
    assert day_index["day_index"][0]["day"] == 1


def test_latest_returns_most_recent_session(client):
    client.post("/api/game/new", params={"seed": 1}).json()["session_id"]
    b = client.post("/api/game/new", params={"seed": 2}).json()["session_id"]
    latest = client.get("/api/game/latest").json()
    # `latest` MUST be the most-recently-touched session regardless of
    # backend ordering quirks (was a real bug: in-memory backend returned
    # the OLDEST session under the previous `rows[0]` implementation).
    assert latest["session_id"] == b


def test_missing_session_404s(client):
    r = client.get("/api/game/does-not-exist/view")
    assert r.status_code == 404


def test_meta_backend_diagnostic(client):
    r = client.get("/api/meta/backend").json()
    assert r["backend"] in {"memory", "snowflake"}


# ── Phase C watcher routes ──────────────────────────────────────────────


def test_api_sessions_returns_enriched_session_rows(client):
    """``/api/sessions`` powers the watcher's season picker.

    Two sessions are created; the endpoint must list both with the
    enriched fields the frontend renders into the dropdown
    (``session_id``, ``season_name``, ``season_slug``, ``day``,
    ``phase``, ``scores``).
    """
    a = client.post(
        "/api/game/new", params={"seed": 71, "width": 16, "height": 10},
    ).json()["session_id"]
    b = client.post(
        "/api/game/new", params={"seed": 72, "width": 16, "height": 10},
    ).json()["session_id"]

    payload = client.get("/api/sessions").json()
    assert "sessions" in payload
    ids = [row["session_id"] for row in payload["sessions"]]
    assert a in ids and b in ids
    for row in payload["sessions"]:
        for field in (
            "session_id",
            "season_name",
            "season_slug",
            "day",
            "phase",
            "scores",
        ):
            assert field in row, f"missing field {field} in {row!r}"
        assert isinstance(row["scores"], dict)
        assert set(row["scores"].keys()) == {"p1", "p2"}


def test_api_sessions_filter_by_season_slug(client):
    """``?season=<slug>`` deep-links: returns exactly that session or []."""
    sid = client.post(
        "/api/game/new", params={"seed": 73, "width": 16, "height": 10},
    ).json()["session_id"]

    listing = client.get("/api/sessions").json()
    slug = next(
        row["season_slug"]
        for row in listing["sessions"]
        if row["session_id"] == sid
    )
    assert slug, "engine.list_sessions should populate season_slug"

    hit = client.get("/api/sessions", params={"season": slug}).json()
    assert len(hit["sessions"]) == 1
    assert hit["sessions"][0]["session_id"] == sid

    miss = client.get(
        "/api/sessions", params={"season": "nope-not-a-real-slug"},
    ).json()
    assert miss["sessions"] == []


def test_watch_html_serves_spa_shell(client):
    """``/watch.html`` aliases index.html so app.js can detect watcher
    mode via URL params on the same page."""
    r = client.get("/watch.html")
    assert r.status_code == 200
    # The SPA shell is HTML, not JSON.
    body = r.text
    assert "<html" in body.lower()
    # The watcher picker container must exist in the shell so app.js
    # can populate it on boot.
    assert "watch-season-picker" in body


def test_snowflake_session_store_roundtrip(client):
    """SnowflakeStore wraps SocStore for any code expecting GameSession."""
    from sea_of_colours.game.snowflake_store import SnowflakeStore

    sid = client.post(
        "/api/game/new", params={"seed": 5, "width": 18, "height": 12},
    ).json()["session_id"]

    store = SnowflakeStore(soc_backend.get_store())
    sess = store.load(sid)
    assert sess is not None
    assert sess.session_id == sid
    assert sess.day == 1
    assert sid in store.list_ids()

    latest = store.latest()
    assert latest is not None


# ── LLM-seat credential preflight ────────────────────────────────────
# Without a PAT the V12 harness does not raise: its per-turn fallback
# absorbs the miss and the seat passes every night with zero moves,
# reporting ok=True / error=None. That reads as "the AI is broken"
# rather than "you never set a PAT", so game creation refuses up front.


def _no_cortex_creds(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_PAT", raising=False)
    monkeypatch.setenv("SF_CONFIG_FILE", "/nonexistent/sf_config")


def _with_cortex_creds(monkeypatch, tmp_path):
    cfg = tmp_path / "sf_config"
    cfg.write_text("account=TESTACCT\n")
    monkeypatch.setenv("SNOWFLAKE_PAT", "test-token")
    monkeypatch.setenv("SF_CONFIG_FILE", str(cfg))


def _persistent_backend(monkeypatch, tmp_path):
    """A durable backend an LLM seat is allowed on, without Snowflake.

    v1.14 refuses LLM seats on ``memory`` (nothing to replay afterwards),
    and auto-detection always picks ``memory`` under pytest, so a test
    about *credentials* has to name a persistent backend or it ends up
    asserting the storage rule by accident. ``file`` is the offline one.
    """
    monkeypatch.setenv("SOC_STORE_DIR", str(tmp_path / "seasons"))
    return "file"


def test_new_game_refuses_llm_seat_without_credentials(
    client, monkeypatch, tmp_path,
):
    _no_cortex_creds(monkeypatch)
    r = client.post(
        "/api/game/new",
        json={
            "seed": 3, "width": 16, "height": 10,
            "players": ["p1", "p2"],
            "agents": {"p1": "human", "p2": "tabula_v12"},
            "backend": _persistent_backend(monkeypatch, tmp_path),
        },
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    # Must name the seat, the missing credential, and a way to play now.
    assert "p2" in detail
    assert "SNOWFLAKE_PAT" in detail
    assert "RED_HARVEST_LITE" in detail


def test_new_game_allows_llm_seat_with_credentials(client, monkeypatch, tmp_path):
    _with_cortex_creds(monkeypatch, tmp_path)
    r = client.post(
        "/api/game/new",
        json={
            "seed": 3, "width": 16, "height": 10,
            "players": ["p1", "p2"],
            "agents": {"p1": "human", "p2": "tabula_v12"},
            "backend": _persistent_backend(monkeypatch, tmp_path),
        },
    )
    assert r.status_code == 200
    assert r.json()["agents"]["p2"] == "tabula_v12"


def test_new_game_heuristic_seats_never_need_credentials(client, monkeypatch):
    """The zero-setup path must stay zero-setup."""
    _no_cortex_creds(monkeypatch)
    for label in ("red_harvest_lite", "red_harvest", "human"):
        r = client.post(
            "/api/game/new",
            json={
                "seed": 3, "width": 16, "height": 10,
                "players": ["p1", "p2"],
                "agents": {"p1": "human", "p2": label},
            },
        )
        assert r.status_code == 200, f"{label} should not need credentials"
