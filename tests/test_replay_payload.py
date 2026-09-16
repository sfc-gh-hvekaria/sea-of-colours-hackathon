"""Phase 0 regression: replay frames must carry per-seat percepts.

Background: under the original Snowflake port,
:func:`GameSession.replay_push_scene` emitted a single ``cells_player``
key (hardcoded to p1) while
:meth:`SnowparkSocStore.append_replay_frames` wrote two separate columns
``cells_player_p1`` and ``cells_player_p2``. Because the in-memory frame
dicts never set those two new keys, BOTH Snowflake columns persisted as
``NULL`` and the playing UI's REPLAY panel rendered no player overlay
once you flipped ``SOC_BACKEND=snowflake``.

The fix surfaces both percepts on every frame and the watcher viewer
toggle (Phase 5) builds on top of that contract. These tests pin it.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SOC_BACKEND", "memory")

from sea_of_colours.snowpark import (
    InMemorySocStore,
    init_session,
    submit_policy,
)


def _drive_one_resolved_night(store):
    info = init_session(store, seed=4242, width=18, height=12)
    sid = info["session_id"]
    # Both seats lock empty policies — fastest way to resolve a night.
    submit_policy(store, sid, "p1", [])
    submit_policy(store, sid, "p2", [])
    return sid


def test_every_replay_frame_has_both_player_percepts():
    """In-memory: each frame must expose cells_player_p1 AND cells_player_p2."""
    store = InMemorySocStore()
    sid = _drive_one_resolved_night(store)
    frames = store.list_replay_frames(sid)
    assert frames, "expected at least one replay frame after a resolved night"
    for f in frames:
        assert f.get("cells_player_p1") is not None, (
            f"frame {f.get('frame_idx')} missing cells_player_p1"
        )
        assert f.get("cells_player_p2") is not None, (
            f"frame {f.get('frame_idx')} missing cells_player_p2"
        )
        # Back-compat alias for the existing playing UI (app.js still
        # reads frame.cells_player). Until the viewer toggle ships in
        # Phase 5, this alias must equal cells_player_p1.
        assert f.get("cells_player") == f.get("cells_player_p1")


def test_player_percepts_diverge_once_probes_drop():
    """Once each seat plants its OWN probe, the per-seat percepts diverge."""
    store = InMemorySocStore()
    info = init_session(store, seed=4242, width=18, height=12)
    sid = info["session_id"]
    # Far-apart probes: p1 sees the upper-left, p2 sees the lower-right.
    submit_policy(store, sid, "p1", [{"a": "probe", "at": [3, 3]}])
    submit_policy(store, sid, "p2", [{"a": "probe", "at": [14, 9]}])
    frames = store.list_replay_frames(sid)
    differing = [
        f for f in frames if f.get("cells_player_p1") != f.get("cells_player_p2")
    ]
    assert differing, (
        "expected at least one frame where p1/p2 percepts differ "
        "(each seat just deployed a probe in a different region)"
    )


def test_replay_frames_idempotent_per_day():
    """E1b: re-appending a day's frames must not duplicate it in the replay.

    Reproduces the "day 3 repeats three times" bug: a stale-read re-dispatch
    could resolve the same night twice, and the old append stacked a second
    identical copy of that day's frames. The append is now replace-by-(sid, day)
    so the replay only ever holds ONE copy per day.
    """
    store = InMemorySocStore()
    sid = _drive_one_resolved_night(store)
    day1 = [f for f in store.list_replay_frames(sid) if int(f["day"]) == 1]
    assert day1, "expected day-1 frames after a resolved night"

    # Simulate a duplicate resolution re-appending the SAME night's frames.
    store.append_replay_frames(
        sid, 1, [{k: v for k, v in f.items()
                  if k not in ("session_id", "global_idx")} for f in day1],
    )
    after = [f for f in store.list_replay_frames(sid) if int(f["day"]) == 1]
    assert len(after) == len(day1), (
        f"day 1 duplicated: {len(day1)} -> {len(after)} frames"
    )
    # global_idx stays unique + contiguous so the viewer ordering holds.
    gids = [f["global_idx"] for f in store.list_replay_frames(sid)]
    assert len(gids) == len(set(gids)), "global_idx collided after re-append"


@pytest.mark.skipif(
    os.environ.get("SOC_TEST_LIVE") != "1",
    reason="SOC_TEST_LIVE not set",
)
def test_live_snowflake_replay_payload_round_trips():
    """Snowflake round-trip: VARIANT columns come back parsed, alias intact."""
    from scripts.deploy_soc_schema import create_snowpark_session
    from sea_of_colours.snowpark.snowpark_store import SnowparkSocStore

    config = os.environ.get(
        "SF_CONFIG_FILE", os.path.expanduser("~/.ssh/sf_config"),
    )
    session = create_snowpark_session(config)
    try:
        store = SnowparkSocStore(session)
        sid = _drive_one_resolved_night(store)
        frames = store.list_replay_frames(sid)
        assert frames, "expected at least one persisted replay frame"
        for f in frames:
            assert f.get("cells_player_p1") is not None
            assert f.get("cells_player_p2") is not None
            # Synthetic back-compat alias surfaced on read for the playing UI.
            assert f.get("cells_player") == f.get("cells_player_p1")
    finally:
        session.close()
