"""Per-game storage backend (v1.14).

Backend selection used to be a per-*process* decision resolved once at
boot. That split badly: anyone with key-pair credentials — i.e. everyone
likely to demo this — auto-detected to Snowflake and paid a warehouse
round-trip per action having never chosen it, while an attendee on a
clean laptop got memory and no way to keep a season.

Games now choose in the New Game modal. These tests pin the three things
that make that safe:

1. **Routing.** Every game-scoped call must reach the store that owns the
   game, including the background bot worker, which outlives the request
   that started it.
2. **Listing.** The picker merges across whichever stores are open, or
   half the seasons vanish the moment two backends coexist.
3. **The memory/LLM line.** Memory games are heuristics-only, because an
   LLM match with nothing persisted leaves nothing for the turn suite or
   the advisor to read back.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sea_of_colours.snowpark import backend as soc_backend


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SOC_STORE_DIR", str(tmp_path / "seasons"))
    soc_backend.reset_for_tests()
    from server.app import app
    yield TestClient(app)
    soc_backend.reset_for_tests()


def _new(client, **body):
    payload = {"seed": 7, "width": 16, "height": 10}
    payload.update(body)
    return client.post("/api/game/new", json=payload)


# ── routing ──────────────────────────────────────────────────────────
def test_a_game_is_readable_from_the_backend_that_wrote_it(client):
    sid = _new(client, backend="file").json()["session_id"]
    assert client.get(f"/api/game/{sid}/status").status_code == 200


def test_two_backends_serve_two_games_side_by_side(client):
    mem = _new(client, seed=1, backend="memory").json()["session_id"]
    fil = _new(client, seed=2, backend="file").json()["session_id"]
    assert mem != fil
    # The whole point: neither game shadows the other.
    assert client.get(f"/api/game/{mem}/status").json()["day"] == 1
    assert client.get(f"/api/game/{fil}/status").json()["day"] == 1


def test_the_registry_routes_each_id_to_its_own_store(client):
    mem = _new(client, seed=1, backend="memory").json()["session_id"]
    fil = _new(client, seed=2, backend="file").json()["session_id"]
    assert soc_backend.backend_for_session(mem) == "memory"
    assert soc_backend.backend_for_session(fil) == "file"
    # An id nobody registered predates the registry (or another process
    # wrote it), and must fall back to the process default rather than
    # raising.
    assert soc_backend.backend_for_session("unknown") == soc_backend.resolution().name


def test_the_response_says_where_the_game_went(client):
    body = _new(client, backend="memory").json()
    assert body["backend"] == "memory"
    assert body["persists"] is False
    body = _new(client, seed=8, backend="file").json()
    assert body["backend"] == "file"
    assert body["persists"] is True


def test_deleting_a_game_forgets_its_route(client):
    sid = _new(client, backend="file").json()["session_id"]
    assert client.delete(f"/api/game/{sid}").status_code == 200
    assert soc_backend.backend_for_session(sid) == soc_backend.resolution().name


def test_omitting_the_backend_uses_the_server_default(client):
    body = _new(client).json()
    assert body["backend"] == soc_backend.resolution().name


# ── listing ──────────────────────────────────────────────────────────
def test_the_picker_shows_games_from_every_open_backend(client):
    mem = _new(client, seed=1, backend="memory").json()["session_id"]
    fil = _new(client, seed=2, backend="file").json()["session_id"]
    listed = client.get("/api/sessions").json()["sessions"]
    ids = {s["session_id"] for s in listed}
    assert {mem, fil} <= ids, "a merged listing must not hide either store"


def test_listed_rows_carry_the_backend_that_holds_them(client):
    fil = _new(client, backend="file").json()["session_id"]
    row = next(
        s for s in client.get("/api/sessions").json()["sessions"]
        if s["session_id"] == fil
    )
    assert row["backend"] == "file"


def test_listing_keeps_the_enriched_shape_the_picker_renders(client):
    _new(client, backend="file")
    row = client.get("/api/sessions").json()["sessions"][0]
    # Merging must not regress to raw store rows — the picker reads all
    # of these without a follow-up call.
    for key in ("season_name", "season_slug", "kind", "day", "phase", "scores"):
        assert key in row, f"merged listing dropped {key!r}"


def test_latest_walks_the_merged_list(client):
    sid = _new(client, backend="file").json()["session_id"]
    assert client.get("/api/game/latest").json()["session_id"] == sid


# ── the memory / LLM line ────────────────────────────────────────────
def test_memory_games_refuse_an_llm_seat(client):
    r = _new(
        client,
        backend="memory",
        players=["p1", "p2"],
        agents={"p1": "human", "p2": "tabula_v12"},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "p2" in detail
    # The refusal has to name the way forward, not just say no.
    assert "Snowflake" in detail


def test_memory_games_still_take_heuristic_seats(client):
    r = _new(
        client,
        backend="memory",
        players=["p1", "p2"],
        agents={"p1": "human", "p2": "red_harvest_lite"},
    )
    assert r.status_code == 200


def test_a_persistent_backend_takes_an_llm_seat(client, monkeypatch, tmp_path):
    cfg = tmp_path / "sf_config"
    cfg.write_text("account=TESTACCT\n")
    monkeypatch.setenv("SNOWFLAKE_PAT", "test-token")
    monkeypatch.setenv("SF_CONFIG_FILE", str(cfg))
    r = _new(
        client,
        backend="file",
        players=["p1", "p2"],
        agents={"p1": "human", "p2": "tabula_v12"},
    )
    assert r.status_code == 200


def test_the_modal_is_told_memory_has_no_llm(client):
    options = client.get("/api/meta/backend").json()["options"]
    memory = next(o for o in options if o["value"] == "memory")
    assert memory["available"] is True
    assert memory["allows_llm"] is False
    assert memory["persists"] is False


def test_an_unreachable_snowflake_is_offered_but_explained(client):
    options = client.get("/api/meta/backend").json()["options"]
    snow = next(o for o in options if o["value"] == "snowflake")
    if not snow["available"]:
        # Greyed out, not hidden — and it must say why, or it reads as a
        # missing feature rather than an unfinished setup.
        assert snow["reason"]


def test_a_nonsense_backend_is_rejected_by_name(client):
    r = _new(client, backend="postgres")
    assert r.status_code == 400
    assert "postgres" in r.json()["detail"]


def test_requesting_snowflake_without_setup_names_the_fix(client, monkeypatch):
    monkeypatch.setenv("SF_CONFIG_FILE", "/nonexistent/sf_config")
    r = _new(client, backend="snowflake")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "SNOWFLAKE_SETUP" in detail or "Fix:" in detail
    # Never a 500: an unreachable store is a setup state, not a crash.
    assert "Traceback" not in detail


def test_a_configured_but_unreachable_snowflake_is_not_offered(
    client, monkeypatch, tmp_path,
):
    """The gap the offline readiness check can't see on its own.

    A machine can have the extras, a key-pair ``sf_config`` and a real
    key file, and still be unable to connect — a network policy, an
    expired key, an undeployed schema. Readiness opens no connection, so
    it says "ready"; only the boot probe knows better. If the modal
    trusted readiness alone it would offer Snowflake and then 500 on
    spawn, which reads as a broken build rather than a blocked account.
    """
    from sea_of_colours.snowpark.backend import BackendResolution

    monkeypatch.setattr(
        soc_backend, "snowflake_readiness",
        lambda: (True, "deps and key-pair config present", ""),
    )
    monkeypatch.setattr(
        soc_backend, "resolution",
        lambda: BackendResolution(
            name="memory",
            requested="auto",
            reason="auto-detected snowflake, but opening it failed: "
                   "IP is not allowed to access Snowflake",
            fix="python scripts/quickstart_check.py",
        ),
    )
    options = client.get("/api/meta/backend").json()["options"]
    snow = next(o for o in options if o["value"] == "snowflake")
    assert snow["available"] is False
    assert "not allowed to access" in snow["reason"]

    r = _new(client, backend="snowflake")
    assert r.status_code == 400
    assert "not allowed to access" in r.json()["detail"]


# ── the frozen-constant trap ─────────────────────────────────────────
def test_agent_memory_asks_the_store_not_the_process():
    """A memory game must never write its agent memory into Snowflake.

    The V12 memory modules used to compare against ``SOC_BACKEND``, a
    constant frozen at import. On a Snowflake-default server that reads
    True for *every* game, so a memory game's hazard memory would have
    been persisted into the account under a session id that store has
    never heard of.
    """
    memory_store = soc_backend.get_store_for("memory")
    assert soc_backend.snowpark_session_for(memory_store) is None


def test_v12_memory_modules_no_longer_read_the_frozen_constant():
    """Pins the fix at the source, so a fork can't reintroduce it."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    harness = root / "sea_of_colours" / "orchestrator_2" / "harnesses" / "tabula_v12"
    offenders = [
        path.relative_to(root).as_posix()
        for path in harness.rglob("*.py")
        if "SOC_BACKEND" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{offenders} compare against the process-wide backend; ask the "
        f"store instead (backend.snowpark_session_for)"
    )
