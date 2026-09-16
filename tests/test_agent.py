"""SOC_RED_REAPER agent runtime tests.

Exercises the heuristic path end-to-end against the in-memory SOC
store. The Cortex path is covered by the live-Snowflake lane in
``test_soc_parity.py`` (gated by ``SOC_TEST_LIVE=1``); here we just
confirm the orchestrator falls back gracefully when no PAT is set.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SOC_BACKEND", "memory")

from fastapi.testclient import TestClient

from sea_of_colours.agent.heuristic_agent import HeuristicAgent, plan_moves
from sea_of_colours.agent.runtime import (
    HEURISTIC_AGENT_NAME,
    HEURISTIC_LITE_AGENT_NAME,
    run_agent_turn,
)
from sea_of_colours.snowpark import backend as soc_backend
from sea_of_colours.snowpark import engine as soc_engine


@pytest.fixture()
def store():
    soc_backend.reset_for_tests()
    return soc_backend.get_store()


@pytest.fixture()
def session(store):
    info = soc_engine.init_session(store, seed=21, width=18, height=12)
    return info["session_id"]


def _make_view_payload(*, red_xy=(6, 4)):
    """Tiny hand-rolled agent_view for unit-testing :func:`plan_moves`."""
    return {
        "grid": {"width": 18, "height": 12, "cell_counts": {"fog": 100, "stale": 0, "fresh": 12}},
        "red_tiles": [
            {"x": red_xy[0], "y": red_xy[1], "purity": 200, "freshness": "fresh", "square_id": "abc"},
        ],
        "green_tiles": [],
        "fog_clusters": [
            {"centroid": [12, 7], "size": 40, "nearest_visible_edge": [10, 6]},
            {"centroid": [3, 9], "size": 18, "nearest_visible_edge": [4, 9]},
        ],
        "entities": {
            "mine": [
                {"id": "harvester_p1", "type": "harvester", "pos": None,
                 "carrying_red": False, "cargo_count": 0},
                {"id": "orblift_p1", "type": "orblift", "pos": None,
                 "holds_red": False},
            ],
            "echoes": [],
        },
    }


def test_plan_moves_emits_drop_then_step_when_orbital():
    view = _make_view_payload(red_xy=(6, 4))
    moves, rationale = plan_moves(view)
    # First move: drop adjacent to (6,4); next: step onto (6,4).
    assert moves[0]["a"] == "drop"
    assert moves[0]["unit"] == "harvester_p1"
    assert moves[1]["a"] == "step"
    assert moves[1]["to"] == [6, 4]
    # And probes on the two largest fog clusters — now aimed at the
    # CLUSTER CENTROID (deep in the fog) rather than the visible-edge
    # boundary, so each probe expands the visible region meaningfully.
    probes = [m for m in moves if m["a"] == "probe"]
    assert len(probes) == 2
    centroids = {(12, 7), (3, 9)}
    for p in probes:
        assert tuple(p["at"]) in centroids, (
            f"probes should hit cluster centroids, got {p['at']}"
        )
    assert "harvester" in rationale.lower() or "probe" in rationale.lower()


def test_probes_avoid_piling_on_existing_probes():
    """Successive probes must spread out — that was the root cause of
    the 'no RED visible' stall the player hit on day 6+.

    Setup: a fog cluster centroid sits 3 cells from an existing probe.
    With ``min_separation=8`` the planner should NOT drop another probe
    on top of the same area; it falls through to the cluster's
    visible-edge anchor instead (or skips that cluster entirely).
    """
    view = _make_view_payload(red_xy=(6, 4))
    view["red_tiles"] = []  # force the exploratory branch
    # One large cluster whose centroid is right next to an existing probe.
    view["fog_clusters"] = [
        {"centroid": [11, 7], "size": 60, "nearest_visible_edge": [3, 1]},
    ]
    # Existing probe at (10,6) — centroid (11,7) is only 2 cells away.
    view["entities"]["mine"].append({
        "id": "probe_p1_1", "type": "probe", "pos": [10, 6],
    })
    moves, _ = plan_moves(view)
    probes = [m for m in moves if m["a"] == "probe"]
    assert probes, "should still drop SOME probe (fall back to edge)"
    for p in probes:
        # The probe must NOT have landed near the existing one — that
        # would defeat the whole point of the exploration heuristic.
        from sea_of_colours.agent.heuristic_agent import _manhattan
        assert _manhattan(tuple(p["at"]), (10, 6)) >= 8, (
            f"probe at {p['at']} is piled on existing probe (10,6); "
            "spread-out rule failed"
        )


def test_exploratory_drop_moves_off_centre_on_subsequent_nights():
    """When no RED is visible AND map centre has already been explored,
    the harvester should land somewhere ELSE on subsequent nights — not
    keep dropping at the same coordinates forever.

    This is the exact regression the player reported: 5+ nights of
    "p1 dropped harvester_p1 at (20,14)" with hoard frozen at 9/50.
    """
    view = _make_view_payload(red_xy=(0, 0))
    view["red_tiles"] = []  # no harvest target → exploratory branch
    # Pretend the map centre is already covered by probes.
    width, height = view["grid"]["width"], view["grid"]["height"]
    cx, cy = width // 2, height // 2
    view["entities"]["mine"].extend([
        {"id": f"probe_p1_{i}", "type": "probe", "pos": [cx + dx, cy + dy]}
        for i, (dx, dy) in enumerate([(0, 0), (2, 1), (-1, 2)])
    ])
    # A fog cluster far from the centre — that's where the harvester
    # should choose to drop instead of piling on the centre again.
    view["fog_clusters"] = [
        {"centroid": [2, 2], "size": 80, "nearest_visible_edge": [4, 4]},
    ]
    moves, rationale = plan_moves(view)
    drops = [m for m in moves if m["a"] == "drop"]
    assert drops, "exploratory branch should still drop the harvester"
    drop_xy = tuple(drops[0]["at"])
    # The chosen drop site must NOT be the map centre — that's the bug.
    assert drop_xy != (cx, cy), (
        f"harvester dropped at map centre {drop_xy} despite probes "
        "already there; exploratory drop should target the fog cluster"
    )


def test_plan_moves_falls_back_to_fog_frontier_when_no_red_visible():
    """Orbital harvester + no RED visible → drop at the largest fog
    cluster's NEAREST_VISIBLE_EDGE so the harvester lands on a
    live/echo tile (RULEBOOK §3.10) and its 2-cell vision disk
    reveals fresh ground beyond the frontier next turn.

    Pre-v0.9.5 the heuristic aimed at the fog *centroid*, which
    the conftest patch let through but the live game would have
    rejected as "drop into fog". v0.9.5 picks the visible edge so
    the drop actually succeeds in production.
    """
    view = _make_view_payload(red_xy=(0, 0))
    view["red_tiles"] = []
    # Fog clusters from _make_view_payload: largest is the [12,7]
    # cluster with visible edge at [10, 6].
    moves, rationale = plan_moves(view)
    drops = [m for m in moves if m["a"] == "drop"]
    assert len(drops) == 1
    assert drops[0]["at"] == [10, 6], (
        "fog-push drop should target the cluster's NEAREST_VISIBLE_EDGE, "
        "not the deep-fog centroid (RULEBOOK §3.10 — harvesters must land "
        "on live or echo cells)"
    )
    # v0.9.6 — after the drop the harvester now WALKS into the fog
    # (zero-or-more step moves) before lifting. The HARVESTER chain
    # must end with exactly one pickup so dawn doesn't destroy the
    # unit. Anything AFTER the pickup is the probe-drop block; the
    # pickup is the last action that targets this harvester.
    drop_idx = moves.index(drops[0])
    pickups = [
        i for i, m in enumerate(moves)
        if m.get("a") == "pickup" and m.get("unit") == drops[0].get("unit")
    ]
    assert len(pickups) == 1, (
        f"scout chain must end with exactly one pickup for this harvester, "
        f"got {len(pickups)}"
    )
    pickup_idx = pickups[0]
    assert pickup_idx > drop_idx, (
        "pickup for the scouted harvester must come AFTER its drop"
    )
    # Any actions between the drop and the pickup must be ``step``s
    # belonging to the same harvester — the v0.9.6 fog-scout walk.
    middle = moves[drop_idx + 1 : pickup_idx]
    assert all(m["a"] == "step" for m in middle), (
        f"expected step actions between drop and pickup, got {middle}"
    )
    # Whatever follows the pickup is the probe-drop block; it must
    # not target the scouted harvester (engine §3.11.2 — dawn would
    # crash the unit if it were still on the surface).
    tail = moves[pickup_idx + 1 :]
    assert all(m.get("a") != "step" or m.get("unit") != drops[0].get("unit") for m in tail), (
        "no harvester steps may run after the unit's pickup"
    )
    assert "no red" in rationale.lower() or "drop" in rationale.lower() or "scouting" in rationale.lower()


def test_plan_moves_only_probes_when_harvester_already_surfaced_with_no_red():
    """If the harvester is already on the surface and no RED is visible, emit probes only."""
    view = _make_view_payload(red_xy=(0, 0))
    view["red_tiles"] = []
    # Surface the harvester so the orbital fallback doesn't fire.
    for ent in view["entities"]["mine"]:
        if ent["type"] == "harvester":
            ent["pos"] = [4, 4]
    moves, _ = plan_moves(view)
    assert all(m["a"] == "probe" for m in moves)


def test_agent_view_includes_echoed_red_tiles_as_stale():
    """REGRESSION: red_tiles must include echo-only reds, marked stale.

    The bug: ``build_agent_view`` only populated ``red_tiles`` from the
    *currently-visible* set. The moment the harvester returned to orbit
    and the player had no probes on a known-RED area, the agent went
    completely blind to those tiles — even though the player's screen
    still showed them via the echo (probe intel) layer. RED_HARVEST
    then stalled forever on the no-RED fallback.
    """
    from sea_of_colours.game.entities import Entity
    from sea_of_colours.game.session import GameSession
    from sea_of_colours.generator import Cell, Tile
    from sea_of_colours.snowpark.view import build_agent_view

    sess = GameSession.new(16, 10, seed=12345)
    # Force a RED tile at (8, 5) and a probe at (8, 5) so the snapshot
    # gets recorded into probe_intel, then *remove the probe* — that's
    # the state where live LoS no longer covers the cell but the echo
    # remembers it.
    sess.grid[5][8] = Cell(Tile.RED, 220)
    probe_id = "probe_p1_echotest"
    sess.entities[probe_id] = Entity(
        id=probe_id, entity_type="probe", owner="p1", x=8, y=5,
    )
    sess._pulse_vision_intel()
    # Drop probe — only echo intel remains.
    del sess.entities[probe_id]
    # Lift the harvester to orbit so the player has no live LoS at all.
    h = sess.entities["harvester_p1"]
    h.x = None
    h.y = None

    view = build_agent_view(sess, "p1")
    reds = view["red_tiles"]
    matching = [r for r in reds if (r["x"], r["y"]) == (8, 5)]
    assert matching, (
        "echo'd RED at (8,5) must appear in agent's red_tiles — "
        "this was the missing data that caused RED_HARVEST to stall"
    )
    assert matching[0]["freshness"] == "stale", (
        "echo-sourced reds should be flagged stale so the agent can "
        "still prefer fresh reds when available"
    )
    assert matching[0]["purity"] == 220, (
        "purity must round-trip from the snapshot, not get lost"
    )


def test_agent_view_red_rows_carry_canonical_tier_and_value():
    """Every red_tiles row (fresh OR stale) must carry tier + value.

    These fields are what the agent prompt + heuristic now reason
    over (see RULEBOOK §2.2). They MUST match the canonical
    ``sea_of_colours.render.red_level`` / ``RED_LEVEL_NAMES`` so
    the rulebook, the engine, the agent_view payload, and the
    Cortex prompt all share a single source of truth.
    """
    from sea_of_colours.game.entities import Entity
    from sea_of_colours.game.session import GameSession
    from sea_of_colours.generator import Cell, Tile
    from sea_of_colours.render import RED_LEVEL_NAMES, red_level
    from sea_of_colours.snowpark.view import build_agent_view

    sess = GameSession.new(16, 10, seed=2026)
    # One cell per tier so we exercise the whole ladder. Coordinates
    # picked to keep them inside the harvester's vision disk when we
    # plant a probe there.
    samples = [
        (3, 3, 40, "trace"),
        (6, 4, 120, "vein"),
        (9, 5, 200, "mass"),
        (12, 6, 255, "pure"),
    ]
    for x, y, purity, _ in samples:
        sess.grid[y][x] = Cell(Tile.RED, purity)
        pid = f"probe_p1_{x}_{y}"
        sess.entities[pid] = Entity(
            id=pid, entity_type="probe", owner="p1", x=x, y=y,
        )
    sess._pulse_vision_intel()
    view = build_agent_view(sess, "p1")
    rows = {(int(r["x"]), int(r["y"])): r for r in view["red_tiles"]}
    for x, y, purity, expected_tier in samples:
        row = rows.get((x, y))
        assert row is not None, f"sample RED@({x},{y}) missing from red_tiles"
        assert row["purity"] == purity
        assert row["tier"] == expected_tier == RED_LEVEL_NAMES[red_level(purity) - 1]
        # value == purity today, capped at 255 per parcel.
        assert row["value"] == min(255, purity)


def test_agent_view_stale_red_rows_also_carry_tier_and_value():
    """The echo (stale) path must surface the same tier + value fields."""
    from sea_of_colours.game.entities import Entity
    from sea_of_colours.game.session import GameSession
    from sea_of_colours.generator import Cell, Tile
    from sea_of_colours.snowpark.view import build_agent_view

    sess = GameSession.new(16, 10, seed=99)
    sess.grid[5][8] = Cell(Tile.RED, 222)
    probe_id = "probe_p1_echotest"
    sess.entities[probe_id] = Entity(
        id=probe_id, entity_type="probe", owner="p1", x=8, y=5,
    )
    sess._pulse_vision_intel()
    del sess.entities[probe_id]
    h = sess.entities["harvester_p1"]
    h.x = None
    h.y = None

    view = build_agent_view(sess, "p1")
    row = next(r for r in view["red_tiles"] if (r["x"], r["y"]) == (8, 5))
    assert row["freshness"] == "stale"
    assert row["tier"] == "mass"  # 222 sits in 151–254
    assert row["value"] == 222


def test_agent_acts_on_stale_red_when_no_fresh_red_visible():
    """When only stale RED is in the view, the agent should still act.

    Without this, day 6+ of a season looks identical to day 1: the
    agent picks the no-RED fallback and dumps the harvester at the
    map centre forever.
    """
    view = _make_view_payload(red_xy=(0, 0))
    # No fresh reds — only an echo-sourced one.
    view["red_tiles"] = [
        {"x": 12, "y": 6, "purity": 180, "freshness": "stale", "square_id": "s1"},
    ]
    moves, rationale = plan_moves(view)
    # Must produce a real harvest chain (drop+step+pickup), NOT the
    # exploratory branch.
    actions = [m["a"] for m in moves]
    assert "drop" in actions
    assert "pickup" in actions
    assert "step" in actions
    # The first step should head toward (12, 6).
    first_step = next(m for m in moves if m["a"] == "step")
    assert first_step["to"][0] in (11, 12, 13) or first_step["to"][1] in (5, 6, 7)


def test_heuristic_agent_play_wraps_plan_moves():
    agent = HeuristicAgent()
    plan = agent.play(_make_view_payload())
    assert plan["moves"]
    assert "rationale" in plan
    assert plan["tool_calls"]


def test_run_agent_turn_logs_invocation(store, session):
    """RED_HARVEST (heuristic) always lands an audit row, even on a fully-fogged Day 1."""
    result = run_agent_turn(store, session, "p1")
    assert result["ok"]
    assert result["runtime"] == "heuristic"
    assert result["agent_id"] == HEURISTIC_AGENT_NAME
    audit = store.list_agent_invocations(session)
    assert len(audit) == 1
    assert audit[0]["agent_id"] == HEURISTIC_AGENT_NAME
    assert audit[0]["player"] == "p1"
    assert audit[0]["rationale"]


def test_run_agent_turn_submits_when_view_has_targets(store, session):
    """When the structured view exposes red tiles, the agent submits a queue."""
    # Hand-rolled view payload with a known RED tile so we exercise the
    # submission path even when the live grid hasn't revealed any reds.
    view = _make_view_payload(red_xy=(6, 4))
    moves, _ = plan_moves(view)
    assert moves, "heuristic should produce a queue when a RED tile is visible"
    # Submit it the way the runtime would and assert the engine accepts it.
    res = soc_engine.submit_policy(store, session, "p1", moves)
    assert res["ok"]


def test_agent_route_smoke(store, session):
    soc_backend._memory_store = store  # share the fixture's store
    from server.app import app

    client = TestClient(app)
    r = client.post(f"/api/game/{session}/agent/think", params={"player": "p1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"]
    assert body["agent_id"] == HEURISTIC_AGENT_NAME
    assert body["runtime"] == "heuristic"


def test_agent_resolves_night_when_partner_already_locked(store, session):
    """If p2 already submitted, the agent's turn should resolve the night."""
    soc_engine.submit_policy(store, session, "p2", [])
    result = run_agent_turn(store, session, "p1")
    assert result["ok"]
    # Either the policy was empty (no moves) → submit_result missing, OR
    # the night actually resolved. We accept both: red-less map may
    # legitimately produce a zero-move queue.
    if result["moves"]:
        assert result["night_resolved"] is True


def test_agent_route_rejects_bad_player(store, session):
    soc_backend._memory_store = store
    from server.app import app

    client = TestClient(app)
    r = client.post(f"/api/game/{session}/agent/think", params={"player": "p9"})
    assert r.status_code == 400


def test_agent_route_rejects_bad_runtime_override(store, session):
    """Unknown ``?runtime=...`` values must be rejected at the route level."""
    soc_backend._memory_store = store
    from server.app import app

    client = TestClient(app)
    r = client.post(
        f"/api/game/{session}/agent/think",
        params={"player": "p1", "runtime": "skynet"},
    )
    assert r.status_code == 400


def test_agent_route_runtime_override_heuristic(store, session):
    """``?runtime=heuristic`` pins RED_HARVEST for a single call."""
    soc_backend._memory_store = store
    from server.app import app

    client = TestClient(app)
    r = client.post(
        f"/api/game/{session}/agent/think",
        params={"player": "p1", "runtime": "heuristic"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"]
    assert body["runtime"] == "heuristic"
    assert body["agent_id"] == HEURISTIC_AGENT_NAME


def test_agent_route_runtime_override_lite(store, session):
    """``?runtime=red_harvest_lite`` seats the no-weapons bot."""
    soc_backend._memory_store = store
    from server.app import app

    client = TestClient(app)
    r = client.post(
        f"/api/game/{session}/agent/think",
        params={"player": "p1", "runtime": "red_harvest_lite"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"]
    assert body["agent_id"] == HEURISTIC_LITE_AGENT_NAME


def test_agent_route_rejects_retired_cortex_runtime(store, session):
    """``?runtime=cortex`` is gone — the route must say so, and say where
    the LLM agent moved to, rather than quietly running the heuristic."""
    soc_backend._memory_store = store
    from server.app import app

    client = TestClient(app)
    r = client.post(
        f"/api/game/{session}/agent/think",
        params={"player": "p1", "runtime": "cortex"},
    )
    assert r.status_code == 410
    assert "tabula_v12" in r.json()["detail"]


def test_run_agent_turn_raises_on_retired_cortex_runtime(store, session):
    """Silently degrading an LLM seat to the heuristic is the failure mode
    that hid past regressions, so the engine raises instead."""
    with pytest.raises(ValueError, match="tabula_v12"):
        run_agent_turn(store, session, "p1", runtime_override="cortex")


# ── Cortex invoker SSE diagnostics (v0.7.1) ──────────────────────────
# NOTE: the invoker itself is NOT dead code — it is the shared SSE/PAT
# transport that orchestrator_2 (and therefore V12) builds on. Only the
# legacy Agents-API *runtime wiring* in agent/runtime.py was removed.
#
# After the Lux_Hollow post-mortem revealed that `soc_submit_policy`
# failures were invisible to the orchestrator, we added tool-error and
# wall-clock-cap surfacing to ``CortexAgentInvoker``. These tests pin
# the invoker's parsing of the SSE stream by injecting a fake
# ``requests.post`` that returns a deterministic event sequence.


def _make_sse_response(events):
    """Build a stand-in ``requests`` response for the SSE iterator.

    ``events`` is a list of either dicts (auto-wrapped as ``data: …``
    JSON lines) or already-encoded strings. The returned object mimics
    just enough of the ``requests.Response`` surface that the invoker
    touches: ``.status_code``, ``.iter_lines()`` (returning bytes),
    and ``.close()``.
    """
    import json as _json

    lines = []
    for ev in events:
        if isinstance(ev, dict):
            lines.append(f"data: {_json.dumps(ev)}".encode("utf-8"))
        else:
            lines.append(str(ev).encode("utf-8"))

    class _FakeResponse:
        status_code = 200

        def __init__(self):
            self._lines = lines
            self.closed = False

        def iter_lines(self):
            for line in self._lines:
                yield line

        def close(self):
            self.closed = True

    return _FakeResponse()


def test_cortex_invoker_records_tool_calls_and_submission_flag(monkeypatch):
    """SSE chunks with ``executing_tool`` events build the tool_calls list.

    Also pins ``submitted_policy=True`` whenever a ``soc_submit_policy``
    call appears in the stream — the runtime relies on this flag to
    distinguish "Cortex actually tried" from "Cortex never tried".
    """
    from sea_of_colours.agent import cortex_invoker as ci_mod

    fake = _make_sse_response(
        [
            {"text": "Analyzing the map. "},
            {"status": "executing_tool", "message": "Running soc_submit_policy"},
            {"text": "Submitting one probe."},
            {"status": "executing_tool", "message": "Running soc_save_rationale"},
            "data: [DONE]",
        ]
    )

    def _fake_post(*args, **kwargs):
        return fake

    monkeypatch.setattr("requests.post", _fake_post)

    inv = ci_mod.CortexAgentInvoker(
        agent_name="SOC_RED_REAPER",
        pat_token="test-pat",
    )
    # Force-set the account because the constructor only builds the
    # endpoint URL when the sf_config supplies one.
    inv.account = "test-account"

    result = inv.invoke("hello")
    assert result["ok"] is True
    assert result["submitted_policy"] is True, (
        "soc_submit_policy in tool_calls must flip submitted_policy True"
    )
    names = [c.get("name") for c in result["tool_calls"]]
    assert "soc_submit_policy" in names
    assert "soc_save_rationale" in names
    assert result["hallucinated_tools"] == [], (
        "no forbidden tools were invoked"
    )
    assert result["tool_errors"] == []
    assert result["wallclock_capped"] is False
    assert "Submitting one probe" in result["response"]


def test_cortex_invoker_flags_hallucinated_tool_calls(monkeypatch):
    """Calls to undeclared tools (e.g. ``soc_get_view``) get flagged.

    Before this change Lux_Hollow's Cortex turn opened with
    ``soc_get_view`` every night — a tool we deleted in the harness
    reframe. The hallucination went silent because the SSE parser only
    captured tool names, never compared them to the declared surface.
    """
    from sea_of_colours.agent import cortex_invoker as ci_mod

    fake = _make_sse_response(
        [
            {"status": "executing_tool", "message": "Running soc_get_view"},
            {"text": "(view unavailable, proceeding blind)"},
            {"status": "executing_tool", "message": "Running soc_submit_policy"},
            "data: [DONE]",
        ]
    )

    monkeypatch.setattr("requests.post", lambda *a, **kw: fake)

    inv = ci_mod.CortexAgentInvoker(pat_token="t")
    inv.account = "acct"

    result = inv.invoke("hi")
    assert result["ok"]
    assert "soc_get_view" in result["hallucinated_tools"], (
        "undeclared tools must appear in hallucinated_tools"
    )
    # The declared tool name does NOT get flagged.
    assert "soc_submit_policy" not in result["hallucinated_tools"]


def test_cortex_invoker_surfaces_tool_result_errors(monkeypatch):
    """A ``tool_result`` chunk carrying an error lands in ``tool_errors``.

    This was the missing observability that hid Lux_Hollow's
    soc_submit_policy rejections — the SSE stream did send back
    error payloads, but the parser only ever looked at the text /
    tool_use events.
    """
    from sea_of_colours.agent import cortex_invoker as ci_mod

    fake = _make_sse_response(
        [
            {"status": "executing_tool", "message": "Running soc_submit_policy"},
            {
                "tool_result": {
                    "name": "soc_submit_policy",
                    "is_error": True,
                    "error": "p_policy must be a JSON string",
                }
            },
            "data: [DONE]",
        ]
    )

    monkeypatch.setattr("requests.post", lambda *a, **kw: fake)

    inv = ci_mod.CortexAgentInvoker(pat_token="t")
    inv.account = "acct"

    result = inv.invoke("hi")
    assert result["ok"]
    assert result["submitted_policy"] is True, (
        "Cortex still attempted the call — the flag tracks *attempt*, "
        "not success"
    )
    assert result["tool_errors"], "tool_result error must populate tool_errors"
    first = result["tool_errors"][0]
    assert first["tool"] == "soc_submit_policy"
    assert "p_policy must be a JSON string" in first["error"]


def test_cortex_invoker_enforces_walltime_cap(monkeypatch):
    """A slow streaming Cortex must be cut off at the wall-clock cap.

    Simulated by having ``iter_lines`` sleep between chunks while the
    invoker's clock advances. Once the cap is exceeded, the loop must
    break and ``wallclock_capped`` flips True.
    """
    import time as _time

    from sea_of_colours.agent import cortex_invoker as ci_mod

    # Build a fake response whose iter_lines yields slowly. We patch
    # ``time.time`` inside cortex_invoker to advance by 60s on each
    # iteration so a 75s cap fires after the second chunk.
    times = iter([0.0, 30.0, 80.0, 200.0])
    monkeypatch.setattr(ci_mod.time, "time", lambda: next(times))

    fake = _make_sse_response(
        [
            {"text": "chunk1"},
            {"text": "chunk2"},
            {"text": "chunk3"},   # should never be appended (cap fired)
            "data: [DONE]",
        ]
    )

    monkeypatch.setattr("requests.post", lambda *a, **kw: fake)

    inv = ci_mod.CortexAgentInvoker(pat_token="t")
    inv.account = "acct"

    result = inv.invoke("hi", wallclock_cap_s=75)
    assert result["ok"]
    assert result["wallclock_capped"] is True
    # The third chunk's text must not appear — the cap was supposed to
    # close the stream before it arrived.
    assert "chunk3" not in result["response"]
    assert fake.closed, "wall-clock cap must close the response stream"


# ── v0.9.5 — Orbit playbook + multi-harvester planner ─────────────


def _make_orbit_view(
    *,
    credits: int = 1000,
    cap_used: int = 1,
    cap_max: int = 3,
    damaged_ids: tuple = (),
    healthy_ids: tuple = ("harvester_p1_1",),
    red_parcels: tuple = (),
    green_parcels: int = 0,
    blue_purity_total: int = 0,
    emp_stock: int = 0,
) -> dict:
    """Hand-rolled agent_view for orbit-phase unit tests.

    ``damaged_ids`` lists the ids of harvesters flagged damaged;
    ``healthy_ids`` lists the rest. Together they form the seat's
    fleet exposed via ``entities.mine``.

    v0.9.6 — ``red_parcels`` accepts a tuple of ``(square_id, purity)``
    so a test can hand the catapult some hoard inventory to ship;
    ``green_parcels`` is the number of GREEN parcels to surface for
    the jettison priority path.
    """
    mine_rows = []
    for hid in damaged_ids:
        mine_rows.append({
            "id": hid, "type": "harvester", "pos": None,
            "carrying_red": False, "cargo_count": 0, "damaged": True,
        })
    for hid in healthy_ids:
        mine_rows.append({
            "id": hid, "type": "harvester", "pos": None,
            "carrying_red": False, "cargo_count": 0, "damaged": False,
        })
    hoard_parcels: list = []
    for sid, purity in red_parcels:
        hoard_parcels.append({
            "square_id": sid, "colour": "RED",
            "purity": int(purity),
        })
    for i in range(int(green_parcels)):
        hoard_parcels.append({
            "square_id": f"g-{i}", "colour": "GREEN",
            "purity": 200,
        })
    return {
        "phase": "orbit",
        "grid": {"width": 18, "height": 12, "cell_counts": {"fog": 100, "stale": 0, "fresh": 0}},
        "entities": {"mine": mine_rows, "echoes": []},
        "orbit": {
            "phase_active": True,
            "credits": credits,
            "harvester_cap_used": cap_used,
            "harvester_cap_max": cap_max,
            "actions_max": 3,
            "ship_prices": {
                "harvester_build": 1500,
                "probe_build": 250,
                "repair": 500,
                # v0.9.6 catapult tunables — surfaced via ``view.orbit
                # .ship_prices`` so the agent reads them instead of
                # hard-coding the row schedule.
                "row_count": 4,
                "slots_per_row": 5,
                "row_transit": [10, 25, 50, 100],
                "row_thresholds": [50, 100, 150, 200],
                "quality_mult": {
                    "trace": 0.75, "vein": 1.0, "mass": 1.5, "pure": 3.0,
                },
            },
            "green_catapult": {
                "slots": 12, "cost_base": 50, "cost_step": 5,
                "endgame_penalty": 100,
            },
            "jettison_pricing": {
                "base": 100, "min": 25, "fuel_denominator": 16,
            },
            "hoard_parcels": hoard_parcels,
            # v0.9.9 — blue economy + weapons readouts.
            "blue_purity_total": int(blue_purity_total),
            "weapon_stock": {"emp": int(emp_stock), "chaff": 0},
            "weapon_prices": {
                "emp": {"blue": 200, "credits": 0},
                "chaff": {"blue": 50, "credits": 0},
            },
        },
        # meta drives the seeded RNG used by the 50% EMP-build roll.
        "meta": {"session_id": "test-orbit", "player": "p1", "day": 1},
        "hud": {"day": 1, "player": "p1"},
    }


def test_orbit_playbook_repairs_damaged_then_probes_then_harvester():
    """Full 1000c budget, 1 damaged harvester, 1 healthy → playbook
    fits repair + 2 probes in 2 action slots (500c + 500c = 1000c).
    """
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(
        credits=1000,
        damaged_ids=("harvester_p1_2",),
        healthy_ids=("harvester_p1_1",),
    )
    actions, rationale = plan_orbit_actions(view)
    assert actions[0] == {"a": "repair", "unit": "harvester_p1_2"}
    assert actions[1] == {"a": "build_probe", "count": 2}
    # 1000c - 500 (repair) - 500 (2 probes) = 0c → no harvester yet.
    assert all(a["a"] != "build_harvester" for a in actions)
    assert "repaired harvester_p1_2" in rationale
    assert "built 2 probe(s)" in rationale


def test_orbit_playbook_builds_harvester_when_flush_with_credits():
    """1500c (saved over 2 turns) → 2 probes + harvester in one orbit.

    Demonstrates the "every second / third turn" harvester-building
    cadence the player asked for: with no repairs to make, the
    heuristic spends 500c on probes + 1500c on the harvester.
    """
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=2000, cap_used=1)
    actions, _ = plan_orbit_actions(view)
    tags = [a["a"] for a in actions]
    assert "build_probe" in tags
    assert "build_harvester" in tags
    # And the probe action is the 2-count batch.
    probe_action = next(a for a in actions if a["a"] == "build_probe")
    assert probe_action.get("count") == 2


def test_orbit_playbook_skips_harvester_when_fleet_at_cap():
    """At 3/3 harvesters, the heuristic still buys probes but does
    NOT queue a build_harvester (the resolver would reject it anyway).
    """
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=2000, cap_used=3, cap_max=3)
    actions, _ = plan_orbit_actions(view)
    assert {"a": "build_harvester"} not in actions
    # Probes still buy though.
    assert any(a["a"] == "build_probe" for a in actions)


def test_orbit_playbook_falls_back_to_one_probe_on_tight_budget():
    """With 300c (after some hypothetical repair) the seat can't
    afford 2 probes (500c) but can still buy 1 (250c)."""
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=300, cap_used=1)
    actions, _ = plan_orbit_actions(view)
    probe_action = next(
        (a for a in actions if a["a"] == "build_probe"), None,
    )
    assert probe_action is not None
    assert probe_action.get("count") == 1


def test_orbit_playbook_is_no_longer_capped_at_three_actions():
    """v1.13 — the 3-slot cap is gone; credits are the only limit.

    Three damaged harvesters and plenty of cash used to mean all three
    slots went to repair and everything else was deferred. Now the seat
    repairs all three AND tops the probe magazine up in the same orbit,
    because there was never a reason "repair, repair, repair" should
    forbid also buying a probe.
    """
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(
        credits=5000,
        cap_used=3,
        damaged_ids=("harvester_p1_1", "harvester_p1_2", "harvester_p1_3"),
        healthy_ids=(),
    )
    actions, _ = plan_orbit_actions(view)
    assert len(actions) > 3
    assert [a["a"] for a in actions[:3]] == ["repair"] * 3
    assert any(a["a"] == "build_probe" for a in actions)


def test_orbit_playbook_priority_repair_before_harvester_before_probes():
    """Priority order: repair → build_harvester → weapons → probes.

    With 2000c, one damaged unit and 2/3 fleet: repair (500c, 1500
    left) then the harvester (1500c, 0 left), leaving nothing for
    probes. The ordering is the assertion — a dead rig and an
    undersized fleet both out-earn another probe.
    """
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(
        credits=2000,
        cap_used=2,
        damaged_ids=("harvester_p1_2",),
        healthy_ids=("harvester_p1_1",),
    )
    actions, _ = plan_orbit_actions(view)
    assert actions[0] == {"a": "repair", "unit": "harvester_p1_2"}
    assert actions[1] == {"a": "build_harvester"}


def test_orbit_playbook_banks_credits_on_the_final_orbit():
    """v1.13 — settlement is automatic, so nothing bought on the final
    orbit is ever used. The playbook spends nothing rather than burning
    the balance on a harvester that will never fly."""
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=5000, cap_used=1)
    view["orbit"]["final_orbit"] = True
    actions, rationale = plan_orbit_actions(view)
    assert actions == []
    assert "final settlement orbit" in rationale


def test_orbit_playbook_builds_emp_when_blue_surplus_and_roll_hits():
    """v1.x — blue in the 50%-roll band (250-300) + a winning roll → an
    EMP build is queued (above 300 the always-build tier takes over)."""
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=300, cap_used=1, blue_purity_total=280)
    # day 0 → seeded roll 0.458 < 0.5 → build fires.
    view["meta"]["day"] = 0
    view["hud"]["day"] = 0
    actions, _ = plan_orbit_actions(view)
    assert any(a["a"] == "build_emp" for a in actions)


def test_orbit_playbook_skips_emp_when_roll_misses():
    """v1.x — blue in the roll band but the 50% roll misses → no EMP
    (and no always-build chaff since blue is under 300)."""
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=300, cap_used=1, blue_purity_total=280)
    # day 1 → seeded roll 0.926 ≥ 0.5 → no build.
    view["meta"]["day"] = 1
    view["hud"]["day"] = 1
    actions, _ = plan_orbit_actions(view)
    assert all(a["a"] != "build_emp" for a in actions)
    assert all(a["a"] != "build_chaff" for a in actions)


def test_orbit_playbook_skips_emp_when_blue_below_threshold():
    """v0.9.9 — below the blue threshold the EMP roll never happens."""
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=300, cap_used=1, blue_purity_total=100)
    view["meta"]["day"] = 0  # would-hit roll, but blue gate blocks it
    view["hud"]["day"] = 0
    actions, _ = plan_orbit_actions(view)
    assert all(a["a"] != "build_emp" for a in actions)
    assert all(a["a"] != "build_chaff" for a in actions)


def test_orbit_playbook_always_builds_chaff_when_blue_flush_and_no_chaff():
    """v1.x — blue above 300 with no chaff in the locker → ALWAYS build a
    weapon, and it's CHAFF first (needed for the egress jam)."""
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=300, cap_used=1, blue_purity_total=420)
    actions, _ = plan_orbit_actions(view)
    assert any(a["a"] == "build_chaff" for a in actions)
    assert all(a["a"] != "build_emp" for a in actions)


def test_orbit_playbook_builds_emp_when_flush_and_chaff_stocked():
    """v1.x — blue above 300 but we already hold chaff → the always-build
    tier fills the kit with an EMP instead."""
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=300, cap_used=1, blue_purity_total=420)
    view["orbit"]["weapon_stock"]["chaff"] = 1
    actions, _ = plan_orbit_actions(view)
    assert any(a["a"] == "build_emp" for a in actions)
    assert all(a["a"] != "build_chaff" for a in actions)


def test_night_emp_falls_back_to_enemy_harvester_when_no_beacon():
    """v0.9.10 — with no enemy beacon on the board, the EMP salvo
    falls back to the freshest enemy harvester sighting and fans the
    missiles out (centre + spread) over it."""
    from sea_of_colours.agent.heuristic_agent import plan_moves

    view = _make_view_payload(red_xy=(6, 4))
    view["orbit"] = {"weapon_stock": {"emp": 1, "chaff": 0}}
    view["competitor_intel"] = {
        "new_this_day": [
            {"kind": "enemy_harvester_trail", "owner": "p2", "at": [9, 5]},
        ],
        "persistent_echoes": [],
    }
    moves, _ = plan_moves(view)
    emp = next((m for m in moves if m["a"] == "emp_launch"), None)
    assert emp is not None
    # ``at`` is now a list of spread target cells; the aim point is
    # the first (centre) missile.
    assert emp["at"][0] == [9, 5]
    assert len(emp["at"]) >= 2, "salvo should fan out, not single-target"


def test_night_emp_targets_latest_enemy_beacon():
    """v0.9.10 — the EMP salvo prefers the opponent's freshest probe
    BEACON over an enemy harvester, blanketing the area the enemy
    just lit up."""
    from sea_of_colours.agent.heuristic_agent import plan_moves

    view = _make_view_payload(red_xy=(6, 4))
    view["orbit"] = {"weapon_stock": {"emp": 1, "chaff": 0}}
    view["competitor_intel"] = {
        "new_this_day": [
            {"kind": "enemy_harvester_trail", "owner": "p2", "at": [2, 2]},
            {"kind": "enemy_probe_launch", "owner": "p2", "at": [10, 6],
             "day_seen": 3},
        ],
        "persistent_echoes": [
            {"kind": "enemy_probe", "owner": "p2", "at": [1, 1],
             "last_seen_day": 1},
        ],
    }
    moves, _ = plan_moves(view)
    emp = next((m for m in moves if m["a"] == "emp_launch"), None)
    assert emp is not None
    # Centre missile aims at the freshest beacon (10,6), not the
    # harvester or the stale echo.
    assert emp["at"][0] == [10, 6]


def test_night_harvesters_avoid_dropping_in_emp_cloud():
    """v0.9.10 — harvesters do not drop inside the EMP salvo's
    friendly-fire footprint."""
    from sea_of_colours.agent.heuristic_agent import (
        plan_moves,
        _emp_cloud_cells,
        _emp_spread_targets,
        _latest_enemy_beacon,
    )

    view = _make_view_payload(red_xy=(6, 4))
    view["orbit"] = {"weapon_stock": {"emp": 1, "chaff": 0}}
    # Beacon sits right on the only RED tile's neighbourhood so the
    # cloud would otherwise be a tempting drop zone.
    view["competitor_intel"] = {
        "new_this_day": [
            {"kind": "enemy_probe_launch", "owner": "p2", "at": [6, 4],
             "day_seen": 2},
        ],
        "persistent_echoes": [],
    }
    aim = _latest_enemy_beacon(view)
    targets = _emp_spread_targets(aim, count=3, radius=2, width=18, height=12)
    zone = _emp_cloud_cells(targets, radius=2, width=18, height=12)
    moves, _ = plan_moves(view)
    drops = [tuple(m["at"]) for m in moves if m["a"] == "drop"]
    for d in drops:
        assert d not in zone, f"harvester dropped at {d} inside EMP cloud {sorted(zone)[:5]}…"


def test_night_harvests_blue_when_blue_is_low():
    """v0.9.9 — low blue folds visible BLUE tiles into the harvest
    target pool so a harvester chases them."""
    from sea_of_colours.agent.heuristic_agent import plan_moves

    view = _make_view_payload(red_xy=(6, 4))
    view["red_tiles"] = []  # no RED → harvester must reach for BLUE
    view["blue_tiles"] = [
        {"x": 7, "y": 4, "purity": 180, "value": 180, "freshness": "fresh",
         "square_id": "blue-1"},
    ]
    view["orbit"] = {"blue_purity_total": 10}  # well below threshold
    moves, rationale = plan_moves(view)
    # The harvester should drop adjacent to the BLUE tile and step in.
    steps_to_blue = [m for m in moves if m.get("a") == "step" and m.get("to") == [7, 4]]
    drops = [m for m in moves if m["a"] == "drop"]
    assert drops, "harvester should deploy to reach the BLUE tile"
    assert steps_to_blue, "harvester should walk onto the BLUE tile"
    assert "blue" in rationale.lower()


def test_night_ignores_blue_when_blue_is_plentiful():
    """v0.9.9 — with ample blue, BLUE tiles are NOT folded into the
    target pool: the plan matches a no-blue baseline exactly."""
    from sea_of_colours.agent.heuristic_agent import plan_moves

    def _view(blue_total):
        v = _make_view_payload(red_xy=(6, 4))
        v["red_tiles"] = [
            {"x": 6, "y": 4, "purity": 200, "value": 200,
             "freshness": "fresh", "square_id": "abc"},
        ]
        v["blue_tiles"] = [
            {"x": 7, "y": 4, "purity": 180, "value": 180,
             "freshness": "fresh", "square_id": "blue-1"},
        ]
        v["orbit"] = {"blue_purity_total": blue_total}
        return v

    plentiful, _ = plan_moves(_view(5000))  # way above threshold
    baseline_view = _view(5000)
    baseline_view["blue_tiles"] = []
    baseline, _ = plan_moves(baseline_view)
    assert plentiful == baseline, "blue tiles must not change the plan when blue is high"


def test_night_schedules_chaff_in_egress_window():
    """v1.x — holding chaff, the night plan splices a ``chaff_flare`` at a
    queue position that resolves inside an egress window (5-7 / 11-13)."""
    from sea_of_colours.agent.heuristic_agent import plan_moves

    view = _make_view_payload(red_xy=(6, 4))
    view["orbit"] = {"weapon_stock": {"emp": 0, "chaff": 1}}
    moves, rationale = plan_moves(view)
    idxs = [i for i, m in enumerate(moves) if m.get("a") == "chaff_flare"]
    assert len(idxs) == 1, "exactly one chaff flare should be scheduled"
    # Queue position is 1-indexed hour (one applied move per hour, §3.10).
    hour = idxs[0] + 1
    assert hour in (5, 6, 7, 11, 12, 13), f"chaff at hour {hour} not in a window"
    assert "chaff" in rationale.lower()


def test_night_no_chaff_when_unstocked():
    """v1.x — no chaff in the locker → no flare scheduled."""
    from sea_of_colours.agent.heuristic_agent import plan_moves

    view = _make_view_payload(red_xy=(6, 4))
    view["orbit"] = {"weapon_stock": {"emp": 0, "chaff": 0}}
    moves, _ = plan_moves(view)
    assert all(m.get("a") != "chaff_flare" for m in moves)


# ── RED_HARVEST_LITE — weapons_enabled=False (hackathon tutorial bot) ──


def test_orbit_playbook_weapons_disabled_never_builds_chaff_or_emp():
    """RED_HARVEST_LITE: even with a blue surplus well past the
    always-build threshold, ``weapons_enabled=False`` must never queue
    build_chaff / build_emp — every other priority is unaffected."""
    from sea_of_colours.agent.heuristic_agent import plan_orbit_actions

    view = _make_orbit_view(credits=2000, cap_used=1, blue_purity_total=420)
    actions, rationale = plan_orbit_actions(view, weapons_enabled=False)
    assert all(a["a"] not in ("build_chaff", "build_emp") for a in actions)
    assert "chaff" not in rationale.lower()
    assert "emp" not in rationale.lower()
    # The rest of the playbook still runs (harvester build affordable here).
    assert any(a["a"] == "build_harvester" for a in actions)

    # And with the full heuristic (default weapons_enabled=True) on the
    # SAME view, a weapon build fires — proving the flag, not the view,
    # is what's gating the behaviour.
    full_actions, _ = plan_orbit_actions(view)
    assert any(a["a"] in ("build_chaff", "build_emp") for a in full_actions)


def test_night_weapons_disabled_never_fires_emp_or_chaff():
    """RED_HARVEST_LITE: EMP salvo and chaff egress jam are both
    skipped when ``weapons_enabled=False``, even with full stock."""
    from sea_of_colours.agent.heuristic_agent import plan_moves

    view = _make_view_payload(red_xy=(6, 4))
    view["orbit"] = {"weapon_stock": {"emp": 1, "chaff": 1}}
    view["competitor_intel"] = {
        "new_this_day": [
            {"kind": "enemy_harvester_trail", "owner": "p2", "at": [9, 5]},
        ],
        "persistent_echoes": [],
    }
    moves, rationale = plan_moves(view, weapons_enabled=False)
    assert all(m.get("a") not in ("emp_launch", "chaff_flare") for m in moves)
    assert "emp" not in rationale.lower()
    assert "chaff" not in rationale.lower()

    # Same stock, weapons enabled → both fire (control case).
    full_moves, _ = plan_moves(view)
    assert any(m.get("a") == "emp_launch" for m in full_moves)
    assert any(m.get("a") == "chaff_flare" for m in full_moves)


def test_heuristic_agent_lite_flag_threads_through_play():
    """``HeuristicAgent(weapons_enabled=False)`` is RED_HARVEST_LITE end
    to end via the public ``play()`` contract (both phases)."""
    from sea_of_colours.agent.heuristic_agent import HeuristicAgent

    lite = HeuristicAgent(name="RED_HARVEST_LITE", weapons_enabled=False)
    orbit_view = _make_orbit_view(credits=2000, cap_used=1, blue_purity_total=420)
    orbit_view["phase"] = "orbit"
    plan = lite.play(orbit_view)
    assert all(
        a["a"] not in ("build_chaff", "build_emp")
        for a in plan["orbit_actions"]
    )

    night_view = _make_view_payload(red_xy=(6, 4))
    night_view["orbit"] = {"weapon_stock": {"emp": 1, "chaff": 1}}
    night_plan = lite.play(night_view)
    assert all(
        m.get("a") not in ("emp_launch", "chaff_flare")
        for m in night_plan["moves"]
    )

    # Default HeuristicAgent() is unaffected (weapons_enabled defaults True).
    full = HeuristicAgent()
    assert full.weapons_enabled is True


def _two_orbital_harvesters() -> list:
    return [
        {"id": "harvester_p1_1", "type": "harvester", "pos": None,
         "carrying_red": False, "cargo_count": 0},
        {"id": "harvester_p1_2", "type": "harvester", "pos": None,
         "carrying_red": False, "cargo_count": 0},
        {"id": "orblift_p1", "type": "orblift", "pos": None,
         "holds_red": False},
    ]


def test_night_surplus_harvester_seeks_blue_when_only_low_red():
    """v1.x — with 2 harvesters, blue below the 400 cap, and only LOW-tier
    RED visible, the surplus unit diverts to BLUE (weapons economy)."""
    from sea_of_colours.agent.heuristic_agent import plan_moves

    view = _make_view_payload(red_xy=(6, 4))
    view["entities"]["mine"] = _two_orbital_harvesters()
    # Only a TRACE seam (purity 40) — not worth diverting the surplus unit.
    view["red_tiles"] = [
        {"x": 6, "y": 4, "purity": 40, "value": 40, "freshness": "fresh",
         "square_id": "red-trace"},
    ]
    view["blue_tiles"] = [
        {"x": 14, "y": 8, "purity": 200, "value": 200, "freshness": "fresh",
         "square_id": "blue-1"},
    ]
    # 300 is ABOVE the low-blue top-up (250) but BELOW the weapon cap (400),
    # so only the surplus-seek path can surface BLUE here.
    view["orbit"] = {"blue_purity_total": 300}
    moves, rationale = plan_moves(view)
    assert any(m.get("a") == "step" and m.get("to") == [14, 8] for m in moves), (
        "surplus harvester should walk onto the BLUE tile"
    )
    assert "blue" in rationale.lower()


def test_night_surplus_harvester_still_grabs_high_red():
    """v1.x — plentiful high-tier RED keeps BOTH harvesters on RED even
    when blue-seek is armed: no unit diverts to BLUE."""
    from sea_of_colours.agent.heuristic_agent import plan_moves

    view = _make_view_payload(red_xy=(6, 4))
    view["entities"]["mine"] = _two_orbital_harvesters()
    # Two MASS seams (purity 200) on opposite sides — one per harvester.
    view["red_tiles"] = [
        {"x": 6, "y": 4, "purity": 200, "value": 200, "freshness": "fresh",
         "square_id": "red-mass-a"},
        {"x": 14, "y": 8, "purity": 200, "value": 200, "freshness": "fresh",
         "square_id": "red-mass-b"},
    ]
    view["blue_tiles"] = [
        {"x": 2, "y": 2, "purity": 200, "value": 200, "freshness": "fresh",
         "square_id": "blue-1"},
    ]
    view["orbit"] = {"blue_purity_total": 300}
    moves, _ = plan_moves(view)
    assert any(m.get("a") == "step" and m.get("to") == [6, 4] for m in moves)
    assert any(m.get("a") == "step" and m.get("to") == [14, 8] for m in moves)
    assert all(m.get("to") != [2, 2] for m in moves if m.get("a") == "step"), (
        "no harvester should divert to BLUE while high RED is unclaimed"
    )


def test_play_dispatches_to_orbit_actions_in_orbit_phase():
    """The :class:`HeuristicAgent` ``play`` method must route the
    submission to orbit_actions when the view's phase is orbit, not
    night-phase moves."""
    view = _make_orbit_view(credits=2000, cap_used=1)
    out = HeuristicAgent().play(view)
    assert out["moves"] == []
    assert out["orbit_actions"], "orbit phase should emit orbit_actions"
    assert any(a["a"] == "build_probe" for a in out["orbit_actions"])


def _make_night_view_two_orbital_harvesters(red_xys=((6, 4), (14, 8))):
    """Two harvesters in orbit, two reds visible on opposite sides.

    With target-claiming the planner should drop one harvester
    next to each red — NOT both next to the same one.
    """
    reds = []
    for i, (rx, ry) in enumerate(red_xys):
        reds.append({
            "x": rx, "y": ry, "purity": 200, "freshness": "fresh",
            "value": 200, "square_id": f"sq-{i}",
        })
    return {
        "grid": {"width": 18, "height": 12, "cell_counts": {}},
        "red_tiles": reds,
        "green_tiles": [],
        "fog_clusters": [],
        "entities": {
            "mine": [
                {"id": "harvester_p1_1", "type": "harvester", "pos": None,
                 "carrying_red": False, "cargo_count": 0},
                {"id": "harvester_p1_2", "type": "harvester", "pos": None,
                 "carrying_red": False, "cargo_count": 0},
                {"id": "orblift_p1", "type": "orblift", "pos": None,
                 "holds_red": False},
            ],
            "echoes": [],
        },
    }


def test_plan_moves_assigns_different_harvesters_to_different_reds():
    """Two orbital harvesters + two visible reds → two drops, each
    targeting a DIFFERENT red. This is the bug the v0.9.5 multi-
    harvester pass fixes: the old planner emitted only one chain so
    the second harvester sat in orbit unused."""
    view = _make_night_view_two_orbital_harvesters(
        red_xys=((6, 4), (14, 8)),
    )
    moves, rationale = plan_moves(view)
    drops = [m for m in moves if m["a"] == "drop"]
    assert len(drops) == 2, (
        f"Expected 2 drops (one per harvester), got {len(drops)}: {moves}"
    )
    # Each drop should target a DIFFERENT unit.
    units = [d["unit"] for d in drops]
    assert len(set(units)) == 2, f"both drops on same unit: {units}"
    # And the chains should aim at DIFFERENT reds. Pick the step
    # destination right after each drop as the harvest target.
    step_targets = []
    for i, m in enumerate(moves):
        if m["a"] == "step":
            step_targets.append(tuple(m["to"]))
    # The two reds in the test are at (6,4) and (14,8); the chains
    # should land on cells adjacent to both, eventually stepping
    # onto each.
    assert (6, 4) in step_targets or (14, 8) in step_targets


def test_plan_moves_pushes_surplus_harvesters_into_fog():
    """One visible red + two orbital harvesters → harvester #1
    claims the red, harvester #2 pushes to the FOG FRONTIER so it
    contributes new vision next turn instead of sitting idle.

    RULEBOOK §3.10 — harvesters can only land on live/echo, so the
    "push beyond visible area" actually means dropping at the
    cluster's NEAREST_VISIBLE_EDGE (the live/echo cell nearest the
    deep-fog centroid) and letting LoS expand the frontier."""
    view = _make_night_view_two_orbital_harvesters(red_xys=((6, 4),))
    view["red_tiles"] = view["red_tiles"][:1]
    view["fog_clusters"] = [
        {"centroid": [14, 9], "size": 40, "nearest_visible_edge": [12, 8]},
    ]
    moves, rationale = plan_moves(view)
    drops = [m for m in moves if m["a"] == "drop"]
    assert len(drops) == 2, (
        f"Both harvesters should drop, got {len(drops)}: {moves}"
    )
    drop_coords = {tuple(d["at"]) for d in drops}
    assert (12, 8) in drop_coords, (
        "Surplus harvester should push to the cluster's visible-edge "
        f"frontier [12,8], got drops {drop_coords}"
    )


def test_plan_moves_damaged_surface_harvester_picks_up():
    """A damaged harvester on the surface should pickup so the next
    orbit phase can repair it, instead of trying to step (the engine
    would reject step moves from a damaged unit anyway)."""
    view = _make_night_view_two_orbital_harvesters(red_xys=((6, 4),))
    # Surface one harvester at (5, 4) and mark it damaged. The other
    # stays orbital and healthy.
    view["entities"]["mine"][0].update({
        "pos": [5, 4], "damaged": True,
    })
    moves, rationale = plan_moves(view)
    # The damaged harvester's chain should be exactly one pickup.
    damaged_moves = [m for m in moves if m.get("unit") == "harvester_p1_1"]
    assert damaged_moves == [{"a": "pickup", "unit": "harvester_p1_1"}], (
        f"Damaged harvester should pickup only, got {damaged_moves}"
    )


def test_agent_view_exposes_damaged_flag_on_harvester():
    """REGRESSION: ``entities.mine`` rows for harvesters must carry
    a ``damaged`` field so :func:`plan_orbit_actions` can decide
    whether to queue a repair action."""
    from sea_of_colours.game.session import GameSession
    from sea_of_colours.snowpark.view import build_agent_view

    sess = GameSession.new(18, 12, seed=314)
    # Flip the default harvester to damaged via the same mechanism
    # the simulator uses on collisions.
    sess.entities["harvester_p1"].damaged = True

    view = build_agent_view(sess, "p1")
    mine = view.get("entities", {}).get("mine", [])
    harvester_rows = [r for r in mine if r.get("type") == "harvester"]
    assert harvester_rows, "expected at least one harvester in mine"
    assert harvester_rows[0]["damaged"] is True, (
        "damaged harvester must surface as ``damaged: true`` in entity row"
    )


def test_runtime_orbit_phase_routes_to_heuristic_playbook(store):
    """End-to-end: when the runtime hits the orbit phase, it should
    submit the heuristic's planned action list (not an empty queue).

    The conftest auto-skips the v0.8.0 orbit phase for legacy tests
    (see :file:`tests/conftest.py`), so we flip the persisted
    session into ORBIT manually before running the agent — same
    pattern :file:`tests/test_orbit_v1.py` uses.
    """
    from sea_of_colours.game.session import GameSession, Phase

    info = soc_engine.init_session(store, seed=7, width=18, height=12)
    sid = info["session_id"]
    # Reach into the session row, flip phase + clear pending orbit
    # submissions, and re-persist so the engine's read path sees
    # the session as in ORBIT (with the +1000c stipend already
    # awarded for day 1).
    row = dict(store.load_session(sid) or {})
    sess = GameSession.from_dict(row["json_state"])
    sess.phase = Phase.ORBIT
    sess.pending_orbit_actions = {"p1": None, "p2": None}
    # Force a known credit balance so the playbook assertions are
    # deterministic regardless of any prior +stipend accounting.
    sess.credits = {"p1": 1000, "p2": 1000}
    row["json_state"] = sess.to_dict()
    row["phase"] = Phase.ORBIT.value
    store.save_session(row)

    result = run_agent_turn(store, sid, "p1")
    assert result["agent_id"] == HEURISTIC_AGENT_NAME
    assert result["runtime"] == "heuristic"
    assert "orbit_actions" in result
    assert result["orbit_actions"], "playbook should not be empty"
    tags = [a["a"] for a in result["orbit_actions"]]
    # 1000c budget, healthy fleet (1 starter harvester) → exactly
    # the "build 2 probes" action lands first; harvester build is
    # deferred because 1000c < HARVESTER_BUILD_COST (1500c).
    assert "build_probe" in tags, (
        f"orbit playbook should always include build_probe, got {tags}"
    )


# ── v0.9.8 — fog-scout cardinal-step contract + multi-seat decorrelation ─


def test_fog_scout_walk_only_emits_cardinal_steps():
    """Every step the agent emits must be Manhattan-adjacent to the
    previous position. The pre-v0.9.8 implementation emitted diagonal
    moves (``+1,+1`` per tick) which the engine rejected as ``not
    adjacent`` — the screenshot in the bug report shows ``H02 [p2]
    step harvester_p2 → (23,15)`` from a drop at (22,14), wasting
    every queue slot as a ``waste`` frame and never actually moving
    the unit. The staircase rewrite must produce strictly cardinal
    deltas. (RULEBOOK §3.10 / :meth:`GameSession.try_step_unit`.)
    """
    from sea_of_colours.agent.heuristic_agent import _fog_scout_walk

    view = _make_view_payload(red_xy=(6, 4))
    view["meta"] = {"session_id": "S-cardinal", "day": 1}
    view["hud"] = {"player": "p2", "day": 1}
    view["players"] = ["p1", "p2"]
    # Start at the upper-left so the centroid pull is roughly down-right
    # — that's exactly the case where the buggy diagonal walk fired.
    start = (4, 4)
    visible_set = {(0, 0), (1, 0), (0, 1), (1, 1)}
    moves = _fog_scout_walk(
        view=view, start=start, width=20, height=16,
        harvester_id="harvester_p2", max_steps=8, visible_set=visible_set,
    )
    assert moves, "scout walk must emit at least one step from a deep-fog start"
    here = start
    for m in moves:
        assert m["a"] == "step"
        nxt = tuple(m["to"])
        # Cardinal adjacency: Manhattan distance exactly 1.
        delta = abs(nxt[0] - here[0]) + abs(nxt[1] - here[1])
        assert delta == 1, (
            f"non-cardinal scout step {here} → {nxt} "
            f"(Manhattan distance {delta}); engine will reject as 'not adjacent'"
        )
        here = nxt


def test_probe_drops_decorrelate_across_seats_on_blind_day_one():
    """Two RED_HARVEST seats running the day-1 quadrant fallback with
    identical (no-fog-cluster) views must NOT both land their first
    probe on the same cell. Pre-v0.9.8 the fallback table was fixed
    per seat and seats 0/2 (or p1/p2 in 2-seat games) ended up with
    different tables; in 3- / 4-seat live games seats whose tables
    happened to share a cell at low map widths still collided. The
    (session × seat × day) RNG shuffle is what makes this work.
    """
    from sea_of_colours.agent.heuristic_agent import _plan_probe_drops

    def _blank_view(seat: str) -> dict:
        return {
            "grid": {"width": 24, "height": 18, "cell_counts": {"fog": 100, "stale": 0, "fresh": 0}},
            "red_tiles": [],
            "green_tiles": [],
            # No fog clusters — forces the quadrant fallback path.
            "fog_clusters": [],
            "entities": {"mine": [], "echoes": []},
            "meta": {"session_id": "S-decorrelate", "player": seat,
                     "players": ["p1", "p2", "p3", "p4"], "day": 1},
            "hud": {"player": seat, "day": 1},
            "players": ["p1", "p2", "p3", "p4"],
        }

    cells = []
    for seat in ("p1", "p2", "p3", "p4"):
        probes = _plan_probe_drops(_blank_view(seat))
        assert probes, f"seat {seat} should drop at least one fallback probe"
        # First probe each seat plants — that's the cell that matters
        # for "did they collide?".
        cells.append(tuple(probes[0]["at"]))
    assert len(set(cells)) == 4, (
        f"4 seats must land their first probe on 4 distinct cells, got {cells}"
    )
