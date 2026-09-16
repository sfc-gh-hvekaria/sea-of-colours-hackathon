"""FileSocStore — local, durable, cross-process SOC_* persistence.

These tests pin the contract that makes offline RED_HARVEST seasons
watchable through ``/watch`` without Snowflake: a season written by one
process (the headless runner) must be fully readable by a *separate*
process (the watcher web server) that only shares the on-disk directory.
We simulate the second process with a fresh :class:`FileSocStore`
instance pointed at the same dir.
"""

from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "memory")

from sea_of_colours.snowpark.engine import (
    get_replay,
    get_view,
    init_session,
    list_sessions,
    submit_policy,
)
from sea_of_colours.snowpark.file_store import FileSocStore


def _drive_one_resolved_night(store, *, seed: int = 4242) -> str:
    info = init_session(store, seed=seed, width=18, height=12)
    sid = info["session_id"]
    submit_policy(store, sid, "p1", [])
    submit_policy(store, sid, "p2", [])
    return sid


def test_replay_round_trips_to_disk(tmp_path):
    store = FileSocStore(str(tmp_path))
    sid = _drive_one_resolved_night(store)

    # The season directory exists and the producer can read its own frames.
    assert (tmp_path / sid / "meta.json").exists()
    assert (tmp_path / sid / "frames.jsonl").exists()
    frames = store.list_replay_frames(sid)
    assert frames, "expected at least one replay frame after a resolved night"
    # day_index is derived from frames and must be non-empty.
    assert store.day_index(sid), "day_index empty despite frames on disk"


def test_cross_instance_visibility(tmp_path):
    """A fresh store instance (separate process) sees another's output."""
    producer = FileSocStore(str(tmp_path))
    sid = _drive_one_resolved_night(producer)
    producer_frames = len(producer.list_replay_frames(sid))

    consumer = FileSocStore(str(tmp_path))  # fresh RAM, same dir
    listed = list_sessions(consumer)["sessions"]
    assert any(s["session_id"] == sid for s in listed), (
        "season written by producer is invisible to a fresh consumer store"
    )
    # Replay frames round-trip identically.
    assert len(consumer.list_replay_frames(sid)) == producer_frames
    # Full engine replay payload (day-bucketed) reflects the same frames.
    rep = get_replay(consumer, sid)
    bucket_total = sum(len(b.get("frames") or []) for b in rep.get("days") or [])
    assert bucket_total == producer_frames
    # Session blob hydrates so the seat view renders.
    view = get_view(consumer, sid, "p1")
    assert view.get("phase")


def test_session_blob_and_scores_round_trip(tmp_path):
    producer = FileSocStore(str(tmp_path))
    sid = _drive_one_resolved_night(producer)

    consumer = FileSocStore(str(tmp_path))
    row = consumer.load_session(sid)
    assert row is not None and row.get("json_state"), "json_state blob missing"
    # bulk_session_scores must include the session (picker leaderboard).
    scores = consumer.bulk_session_scores()
    assert sid in scores
    assert set(scores[sid]) == {"p1", "p2"}


def test_tuple_keyed_tables_round_trip(tmp_path):
    """square_identity / grid_cells use (x,y) tuple keys; they must
    serialize to JSON rows and rehydrate without loss.

    v1.41 — writes through the store method directly. ``save_session_full``
    no longer persists grid cells: they are a write-only projection that
    cost ~700ms of every Snowflake turn and that nothing read back (see
    docs/SNOWFLAKE_LATENCY_BRIEF.md). The method survives for backfills,
    and its tuple-key serialisation is still worth pinning — it just is no
    longer reachable from the save path, so the test drives it itself.
    """
    from sea_of_colours.snowpark import file_store as fs

    producer = FileSocStore(str(tmp_path))
    sid = _drive_one_resolved_night(producer)
    producer.upsert_grid_cells(sid, [
        {"x": 3, "y": 4, "tile": 1, "purity": 200, "last_mutated_day": 1},
        {"x": 0, "y": 0, "tile": 0, "purity": 0, "last_mutated_day": None},
    ])

    consumer = FileSocStore(str(tmp_path))
    consumer._reload_part(sid, fs.F_GRID)
    grid = consumer._mem.grid_cells.get(sid, {})
    assert grid, "grid cells did not round-trip"
    for key in grid:
        assert isinstance(key, tuple) and len(key) == 2
        assert all(isinstance(c, int) for c in key)
    assert (3, 4) in grid


def test_append_only_sessions_accumulate(tmp_path):
    """Two seasons in the same dir coexist (init_session is append-only)."""
    store = FileSocStore(str(tmp_path))
    sid1 = _drive_one_resolved_night(store, seed=1)
    sid2 = _drive_one_resolved_night(store, seed=2)
    assert sid1 != sid2

    consumer = FileSocStore(str(tmp_path))
    listed = {s["session_id"] for s in list_sessions(consumer)["sessions"]}
    assert {sid1, sid2} <= listed
    season_dirs = [d for d in tmp_path.iterdir() if (d / "meta.json").exists()]
    assert len(season_dirs) == 2


def test_wipe_removes_session_files(tmp_path):
    store = FileSocStore(str(tmp_path))
    sid = _drive_one_resolved_night(store)
    assert (tmp_path / sid / "meta.json").exists()

    store.wipe_all_sessions()
    assert not (tmp_path / sid).exists()
    consumer = FileSocStore(str(tmp_path))
    assert not list_sessions(consumer)["sessions"]
