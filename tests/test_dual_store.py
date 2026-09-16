"""Dual-store (composite) + delete + landing/play routing.

Pins the "one server shows Snowflake *and* local seasons at once" contract
introduced by :class:`CompositeSocStore`, the per-replay delete that backs
the watcher bin button, and the landing/command-centre route split.

All tests run on the in-memory backend so no live Snowflake is required;
the composite is exercised with two in-memory stores (and a deliberately
exploding primary factory for the offline-degradation case).
"""

from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "memory")

import pytest
from fastapi.testclient import TestClient

from sea_of_colours.snowpark import backend as soc_backend
from sea_of_colours.snowpark.multi_store import CompositeSocStore
from sea_of_colours.snowpark.store import InMemorySocStore


def _session_row(sid: str, name: str = "Season") -> dict:
    return {
        "session_id": sid, "season_name": name,
        "width": 8, "height": 8, "seed": 1, "day": 1, "phase": "planning",
    }


# ── CompositeSocStore ────────────────────────────────────────────────
def _composite(primary: InMemorySocStore, secondary: InMemorySocStore):
    return CompositeSocStore(primary_factory=lambda: primary, secondary=secondary)


def test_composite_union_tags_and_dedupes():
    primary, secondary = InMemorySocStore(), InMemorySocStore()
    primary.save_session(_session_row("onlyprim"))
    primary.save_session(_session_row("dup", "Primary copy"))
    secondary.save_session(_session_row("dup", "Local copy"))
    secondary.save_session(_session_row("onlysec"))

    comp = _composite(primary, secondary)
    rows = comp.list_sessions()
    by_id = {r["session_id"]: r for r in rows}

    # Union with no duplicate ids — primary wins the shared id.
    assert set(by_id) == {"onlyprim", "dup", "onlysec"}
    assert by_id["onlyprim"]["source"] == "snowflake"
    assert by_id["onlysec"]["source"] == "local"
    assert by_id["dup"]["source"] == "snowflake"
    assert by_id["dup"]["season_name"] == "Primary copy"


def test_composite_read_and_delete_routing():
    primary, secondary = InMemorySocStore(), InMemorySocStore()
    primary.save_session(_session_row("p"))
    primary.append_replay_frames("p", 1, [{"caption": "prim"}])
    secondary.save_session(_session_row("s"))
    secondary.append_replay_frames("s", 1, [{"caption": "sec"}])

    comp = _composite(primary, secondary)
    comp.list_sessions()  # warm the ownership cache

    # Reads route to the owning store.
    assert comp.list_replay_frames("p")[0]["caption"] == "prim"
    assert comp.list_replay_frames("s")[0]["caption"] == "sec"

    # Delete routes to the owner only.
    comp.delete_session("s")
    assert secondary.load_session("s") is None
    assert primary.load_session("p") is not None
    assert {r["session_id"] for r in comp.list_sessions()} == {"p"}


def test_composite_routes_unknown_id_by_probe():
    """A read for an id we never listed still resolves via a probe."""
    primary, secondary = InMemorySocStore(), InMemorySocStore()
    secondary.save_session(_session_row("ghost"))
    secondary.append_replay_frames("ghost", 1, [{"caption": "local-only"}])

    comp = _composite(primary, secondary)
    # No list_sessions() first — force the fallback probe path.
    assert comp.list_replay_frames("ghost")[0]["caption"] == "local-only"


def test_composite_new_writes_go_to_primary():
    primary, secondary = InMemorySocStore(), InMemorySocStore()
    comp = _composite(primary, secondary)
    comp.save_session(_session_row("fresh"))
    assert primary.load_session("fresh") is not None
    assert secondary.load_session("fresh") is None


def test_get_multi_store_does_not_deadlock(monkeypatch):
    """``_get_multi_store`` must build the file secondary outside the
    backend lock — both take the same non-reentrant lock, so doing it
    under the lock self-deadlocks. Guards a regression that hung
    /api/meta/status under SOC_BACKEND=multi."""
    soc_backend.reset_for_tests()
    # Don't let the primary factory touch Snowflake during the test.
    monkeypatch.setattr(
        soc_backend, "_get_snowflake_store",
        lambda: (_ for _ in ()).throw(RuntimeError("no snowflake in tests")),
    )
    store = soc_backend._get_multi_store()
    assert isinstance(store, CompositeSocStore)
    # health() must answer without contacting the primary.
    assert store.health()["local"] == "ok"
    soc_backend.reset_for_tests()


def test_composite_degrades_when_primary_unavailable():
    secondary = InMemorySocStore()
    secondary.save_session(_session_row("local"))

    def _boom():
        raise RuntimeError("snowflake unreachable")

    comp = CompositeSocStore(primary_factory=_boom, secondary=secondary)

    rows = comp.list_sessions()
    assert [r["session_id"] for r in rows] == ["local"]
    assert rows[0]["source"] == "local"
    assert comp.health()["snowflake"] == "down"
    assert comp.health()["local"] == "ok"

    # New writes still persist — they fall back to the local store.
    comp.save_session(_session_row("offline-game"))
    assert secondary.load_session("offline-game") is not None


# ── delete_session round-trips (memory + file) ───────────────────────
def test_inmemory_delete_session_round_trip():
    store = InMemorySocStore()
    store.save_session(_session_row("a"))
    store.append_replay_frames("a", 1, [{"caption": "x"}])
    store.delete_session("a")
    assert store.load_session("a") is None
    assert store.list_replay_frames("a") == []
    assert store.list_sessions() == []


def test_file_delete_session_round_trip(tmp_path):
    from sea_of_colours.snowpark.file_store import FileSocStore

    store = FileSocStore(str(tmp_path))
    store.save_session(_session_row("b"))
    store.append_replay_frames("b", 1, [{"caption": "x"}])
    assert (tmp_path / "b").is_dir()

    store.delete_session("b")
    assert store.load_session("b") is None
    assert not (tmp_path / "b").exists()
    # A fresh instance (separate "process") agrees the season is gone.
    assert FileSocStore(str(tmp_path)).list_sessions() == []


# ── HTTP layer (memory backend) ──────────────────────────────────────
@pytest.fixture()
def client():
    soc_backend.reset_for_tests()
    from server.app import app
    return TestClient(app)


def test_delete_route_removes_session(client):
    sid = client.post(
        "/api/game/new", params={"seed": 5, "width": 12, "height": 8},
    ).json()["session_id"]
    assert any(
        s["session_id"] == sid for s in client.get("/api/sessions").json()["sessions"]
    )

    resp = client.delete(f"/api/game/{sid}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert all(
        s["session_id"] != sid for s in client.get("/api/sessions").json()["sessions"]
    )


def test_delete_route_refuses_eval_sessions(client):
    store = soc_backend.get_store()
    store.save_session(_session_row("evalsid", name="eval:scenario:cfg"))
    resp = client.delete("/api/game/evalsid")
    assert resp.status_code == 403
    assert store.load_session("evalsid") is not None


def test_meta_status_reports_backend(client):
    body = client.get("/api/meta/status").json()
    assert body["backend"] == "memory"
    assert "stores" in body and "tunnel" in body
    assert body["tunnel"]["running"] is False


# ── landing / play routing ───────────────────────────────────────────
def test_root_serves_landing(client):
    html = client.get("/").text.lower()
    assert "sea of colours" in html
    assert "/static/landing.js" in html


def test_play_serves_command_centre(client):
    html = client.get("/play").text
    # The SPA shell loads app.js and carries the season picker element.
    assert "/static/app.js" in html
    assert "watch-season-picker" in html


def test_watch_still_serves_spa_for_deeplinks(client):
    # Deep links remain path-agnostic: the watcher shell is the same SPA.
    html = client.get("/watch.html").text
    assert "/static/app.js" in html
