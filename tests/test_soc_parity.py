"""Parity tests for the Snowpark engine wrapper.

These tests confirm that running the same scenarios through the engine
in :mod:`sea_of_colours.snowpark.engine` (against an
:class:`InMemorySocStore`) produces the same end-state as running them
directly against :class:`~sea_of_colours.game.session.GameSession`. The
two backends are expected to diverge in *one* deliberate way:

* The Snowpark wrapper persists each replay frame as an immutable row,
  so historical frames are visible via ``get_replay`` across all days,
  while the direct ``GameSession.last_night_replay`` only retains the
  most recent night.

Everything else — entity positions, hoard contents, log lines,
ledger / asset records, harvest counts — should match.

If a live Snowflake config exists (``SOC_TEST_LIVE=1`` and
``~/.ssh/sf_config`` present) the suite *also* runs against the
``SnowparkSocStore``, but skips otherwise. The default CI lane stays
fully in-process.
"""

from __future__ import annotations

import os
import pytest

from sea_of_colours.game.session import GameSession
from sea_of_colours.snowpark import (
    InMemorySocStore,
    get_inventory,
    get_observer,
    get_replay,
    get_session_status,
    get_view,
    init_session,
    list_sessions,
    submit_policy,
)
from sea_of_colours.snowpark.engine import _hydrate_session


SCENARIOS = [
    {
        "id": "empty_night",
        "seed": 17,
        "width": 18,
        "height": 12,
        "days": [
            {"p1": [], "p2": []},
        ],
    },
    {
        "id": "single_probe_each",
        "seed": 23,
        "width": 22,
        "height": 14,
        "days": [
            {"p1": [{"a": "probe", "at": [5, 5]}],
             "p2": [{"a": "probe", "at": [9, 9]}]},
        ],
    },
    {
        "id": "two_nights",
        "seed": 31,
        "width": 24,
        "height": 16,
        "days": [
            {"p1": [{"a": "probe", "at": [4, 4]}], "p2": []},
            {"p1": [], "p2": [{"a": "probe", "at": [10, 10]}]},
        ],
    },
]


def _direct_run(scenario):
    """Run the scenario through GameSession directly."""
    sess = GameSession.new(
        scenario["width"], scenario["height"], scenario["seed"],
    )
    sid = sess.session_id
    for day_moves in scenario["days"]:
        for player, moves in day_moves.items():
            sess.stash_policy(player, moves)
        sess.maybe_resolve_if_ready()
    return sid, sess


def _engine_run(scenario):
    """Run the scenario through the snowpark engine wrapper."""
    store = InMemorySocStore()
    info = init_session(
        store,
        seed=scenario["seed"],
        width=scenario["width"],
        height=scenario["height"],
    )
    sid = info["session_id"]
    for day_moves in scenario["days"]:
        for player, moves in day_moves.items():
            res = submit_policy(store, sid, player, moves)
            assert res["ok"], res
    return store, sid


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_engine_matches_direct(scenario):
    """End-state through the snowpark wrapper matches direct GameSession."""
    direct_sid, direct_sess = _direct_run(scenario)
    store, sid = _engine_run(scenario)

    # The two paths use independent session_ids (uuids). Compare on the
    # observable state, not on the id itself.
    reloaded = _hydrate_session(store, sid)

    assert reloaded.day == direct_sess.day
    assert reloaded.phase == direct_sess.phase
    assert reloaded.width == direct_sess.width
    assert reloaded.height == direct_sess.height
    assert reloaded.seed == direct_sess.seed

    # Entity layout (positions / cargo).
    def _ents(s):
        return sorted(
            (
                e.id,
                e.entity_type,
                e.owner,
                e.x,
                e.y,
                bool(e.carrying_red),
                bool(e.orbital_cargo_red),
                len(e.cargo_squares),
            )
            for e in s.entities.values()
        )

    assert _ents(reloaded) == _ents(direct_sess)

    # Hoard / shipped contents (deep-compare on the relevant fields).
    for p in ("p1", "p2"):
        assert reloaded.hoard_squares[p] == direct_sess.hoard_squares[p]
        assert (
            reloaded.shipped_squares.get(p, [])
            == direct_sess.shipped_squares.get(p, [])
        )

    # Asset ledger.
    def _assets(s):
        return sorted(
            (rec.asset_id, rec.total_red_harvested, rec.total_days_on_surface,
             rec.first_deployed_day, rec.destroyed_on_day)
            for rec in s.asset_records.values()
        )

    assert _assets(reloaded) == _assets(direct_sess)

    # Structured log content (level + text only, both deterministic).
    def _log(s):
        return [(e["level"], e["text"]) for e in s.log]

    assert _log(reloaded) == _log(direct_sess)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_persisted_replay_frames_per_day(scenario):
    """Replay rows persist per-day across the session lifetime."""
    store, sid = _engine_run(scenario)
    replay = get_replay(store, sid)
    if not scenario["days"]:
        return
    # Each ran night should appear as a day bucket with at least the
    # opening frame, but it's also legal for an entirely-invalid queue
    # to produce just the "open" frame -> 1 frame for that day.
    days = [d["day"] for d in replay["days"]]
    assert len(days) == len(scenario["days"])
    assert days == sorted(set(days)), "days should be unique and sorted"
    assert replay["total_frames"] >= len(scenario["days"])


def test_get_view_carries_agent_payload():
    """The view envelope exposes the agent-friendly structured payload."""
    store = InMemorySocStore()
    info = init_session(store, seed=11, width=18, height=12)
    view = get_view(store, info["session_id"], "p1")
    av = view["agent_view"]
    assert set(av.keys()) >= {
        "hud", "grid_ascii", "grid", "red_tiles", "green_tiles",
        "fog_clusters", "entities", "entity_detail", "recent_log",
    }
    assert av["grid"]["width"] == 18
    assert av["grid"]["height"] == 12
    # ASCII map: legend line + height rows of width chars.
    body = av["grid_ascii"].split("\n", 1)[1]
    rows = body.split("\n")
    assert len(rows) == 12
    assert all(len(r) == 18 for r in rows)


def test_session_status_and_list():
    """get_session_status / list_sessions under the append-only contract.

    From v0.5 onwards, ``init_session`` is append-only — both sessions
    coexist. The earlier session's status must therefore still resolve,
    and ``list_sessions`` returns both rows. This test pins the new
    contract: a future regression that silently re-introduces the
    "every NEW GAME wipes history" behaviour breaks this immediately.
    """
    store = InMemorySocStore()
    a = init_session(store, seed=1, width=16, height=10)
    b = init_session(store, seed=2, width=16, height=10)

    # Both sessions resolve under the append-only contract.
    status_a = get_session_status(store, a["session_id"])
    assert status_a["session_id"] == a["session_id"]
    assert status_a["day"] == 1
    assert "log_tail" in status_a

    status_b = get_session_status(store, b["session_id"])
    assert status_b["session_id"] == b["session_id"]
    assert status_b["day"] == 1
    assert "log_tail" in status_b

    sessions = list_sessions(store)
    sids = {s["session_id"] for s in sessions["sessions"]}
    assert sids == {a["session_id"], b["session_id"]}, (
        "list_sessions must return every persisted season (append-only)"
    )


def test_init_session_preserves_prior_season_data():
    """Append-only contract: ``init_session`` MUST NOT wipe prior sessions.

    The old behaviour (DELETE FROM every SOC_* table on each NEW GAME)
    made it impossible for the CLI orchestrator and the live UI to
    coexist. The contract is now append-only — every per-session row
    for the prior season survives into the next.

    We drive a tiny scenario through the engine — submit a policy,
    resolve a night, save a rationale — so all the session-keyed
    tables in :class:`InMemorySocStore` get populated. Then we call
    ``init_session`` again and confirm EVERY old per-session row is
    still there alongside the new session's row.
    """
    store = InMemorySocStore()
    old = init_session(store, seed=11, width=14, height=10)
    old_sid = old["session_id"]

    # Touch as many tables as possible for the old session so we have
    # something concrete to confirm survives the second init.
    submit_policy(store, old_sid, "p1", [{"a": "probe", "at": [5, 5]}])
    submit_policy(store, old_sid, "p2", [])  # resolves night → replay frames + log rows
    from sea_of_colours.snowpark.engine import save_agent_rationale
    save_agent_rationale(
        store, old_sid, day=1, agent_id="RED_HARVEST", player="p1",
        rationale="seeded", tool_calls=[{"name": "probe"}],
    )

    new = init_session(store, seed=22, width=14, height=10)
    new_sid = new["session_id"]
    assert new_sid != old_sid

    # Both sessions must coexist across every per-session structure the
    # save path actually populates.
    #
    # v1.41 — ``square_identity`` / ``asset_records`` / ``entity_state`` /
    # ``grid_cells`` dropped off this list because ``save_session_full``
    # no longer writes them: they were write-only projections costing
    # ~2.7s of every Snowflake turn and nothing ever read them back (see
    # docs/SNOWFLAKE_LATENCY_BRIEF.md). Empty containers would make the
    # assertion below vacuous rather than wrong, which is worse — it
    # would keep passing while testing nothing.
    for attr in ("hoard", "shipped", "policies",
                 "log_rows", "replay_frames", "agent_invocations"):
        container = getattr(store, attr)
        assert old_sid in container, (
            f"{attr} dropped the old session under append-only — "
            "did init_session regress to wipe behaviour?"
        )

    # The new session is there too.
    assert new_sid in store.sessions
    assert old_sid in store.sessions
    # Order: oldest first, newest last (matches sessions_order semantics).
    assert old_sid in store.sessions_order
    assert new_sid in store.sessions_order


def test_wipe_all_sessions_idempotent_on_empty_store():
    """Calling ``wipe_all_sessions`` on a fresh store is a safe no-op.

    The Snowflake backend executes ``DELETE FROM`` against every SOC_*
    table on the very first NEW GAME (when the tables may not yet have
    any rows), so the in-memory equivalent must also tolerate being
    called against an empty store without raising.
    """
    store = InMemorySocStore()
    store.wipe_all_sessions()
    store.wipe_all_sessions()
    assert store.sessions == {}
    assert store.sessions_order == []


def test_inventory_pack_returned():
    """get_inventory wraps GameSession.inventory_pack."""
    store = InMemorySocStore()
    info = init_session(store, seed=3, width=18, height=12)
    inv = get_inventory(store, info["session_id"], "p1")
    assert inv["player"] == "p1"
    assert "hoard" in inv["inventory"]
    assert "assets_by_status" in inv["inventory"]


def test_observer_view():
    """get_observer returns a cheat full-grid mosaic."""
    store = InMemorySocStore()
    info = init_session(store, seed=7, width=18, height=12)
    obs = get_observer(store, info["session_id"])
    assert obs["mode"] == "observer"
    assert len(obs["cells"]) == 18 * 12


def test_invalid_player_rejected():
    store = InMemorySocStore()
    info = init_session(store, seed=5, width=18, height=12)
    res = submit_policy(store, info["session_id"], "p3", [])
    assert not res["ok"]


# --------------------------------------------------------------------------
# Live-Snowflake parity lane (opt-in)
# --------------------------------------------------------------------------

_LIVE = os.environ.get("SOC_TEST_LIVE") == "1"


@pytest.mark.skipif(not _LIVE, reason="SOC_TEST_LIVE not set")
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_live_snowflake_parity(scenario):
    """Drive the same scenarios through a live SnowparkSocStore."""
    from sea_of_colours.snowpark.snowpark_store import SnowparkSocStore
    from scripts.deploy_soc_schema import create_snowpark_session

    config = os.environ.get(
        "SF_CONFIG_FILE", os.path.expanduser("~/.ssh/sf_config"),
    )
    session = create_snowpark_session(config)
    store = SnowparkSocStore(session)

    info = init_session(
        store,
        seed=scenario["seed"],
        width=scenario["width"],
        height=scenario["height"],
    )
    sid = info["session_id"]
    for day_moves in scenario["days"]:
        for player, moves in day_moves.items():
            submit_policy(store, sid, player, moves)

    status = get_session_status(store, sid)
    assert status["day"] >= 1 + len(scenario["days"]) - 1
    replay = get_replay(store, sid)
    assert replay["total_frames"] >= len(scenario["days"])
