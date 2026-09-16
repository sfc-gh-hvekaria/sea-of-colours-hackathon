"""BufferedSocStore regression tests (v1.43).

The buffered store is the highest-risk component in the storage path: it
holds writes in memory, so a bug here does not raise, it silently loses a
day of log lines or replay frames. These tests pin the three properties that
make it safe.

1. **The ordering guarantee (§7).** The authoritative ``SOC_GAME_SESSION``
   row must be the LAST write of any flush. ``_hydrate_session`` reads only
   that row, so if it lands before its siblings a following hydrate can see
   a pre-resolution snapshot and the season runner re-resolves the same
   night — the "day 3 repeats three times" bug.
2. **Nothing is lost.** Everything buffered must reach the inner store, at
   the latest on the day rollover or at exit.
3. **Buffer-aware reads.** ``list_log`` and ``day_index`` must report pending
   writes, because deferring them would otherwise make the LOG panel and the
   replay gate go blind mid-day.

They run against ``InMemorySocStore``, so no Snowflake connectivity is
needed; what is being tested is the decorator's bookkeeping, which is
backend-independent.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sea_of_colours.snowpark.buffered_store import BufferedSocStore
from sea_of_colours.snowpark.store import InMemorySocStore


class _RecordingStore(InMemorySocStore):
    """InMemorySocStore that appends every mutating call to ``calls``."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: List[str] = []

    def save_session(self, row):  # type: ignore[override]
        self.calls.append("save_session")
        super().save_session(row)

    def upsert_policy(self, session_id, day, player, queue):  # type: ignore[override]
        self.calls.append("upsert_policy")
        super().upsert_policy(session_id, day, player, queue)

    def append_log(self, session_id, day, entries):  # type: ignore[override]
        self.calls.append("append_log")
        super().append_log(session_id, day, entries)

    def append_replay_frames(self, session_id, day, frames):  # type: ignore[override]
        self.calls.append("append_replay_frames")
        super().append_replay_frames(session_id, day, frames)

    def append_agent_invocations(self, rows):  # type: ignore[override]
        self.calls.append("append_agent_invocations")
        super().append_agent_invocations(rows)

    def replace_hoard_bundle(self, session_id, by_owner):  # type: ignore[override]
        self.calls.append("replace_hoard_bundle")
        super().replace_hoard_bundle(session_id, by_owner)

    def replace_shipped_bundle(self, session_id, by_owner):  # type: ignore[override]
        self.calls.append("replace_shipped_bundle")
        super().replace_shipped_bundle(session_id, by_owner)

    def load_session(self, session_id):  # type: ignore[override]
        self.calls.append("load_session")
        return super().load_session(session_id)


def _row(sid: str, day: int = 1, phase: str = "planning") -> Dict[str, Any]:
    return {
        "session_id": sid, "season_name": "T", "width": 8, "height": 6,
        "seed": 42, "day": day, "phase": phase,
        "json_state": {"day": day, "log": []},
    }


def test_session_row_is_written_last_on_every_flush() -> None:
    """THE §7 GUARANTEE. Do not relax this test."""
    inner = _RecordingStore()
    buf = BufferedSocStore(inner)
    buf.append_replay_frames("s1", 1, [{"frame_idx": 0}])
    buf.append_log("s1", 1, [{"level": "info", "text": "x"}])
    buf.append_agent_invocation({"session_id": "s1", "day": 1, "agent_id": "a"})
    buf.upsert_policy("s1", 1, "p1", [])
    buf.replace_hoard_bundle("s1", {"p1": []})
    buf.replace_shipped_bundle("s1", {"p1": []})
    buf.save_session(_row("s1"))

    inner.calls.clear()
    buf.flush()
    assert inner.calls, "flush issued nothing"
    assert inner.calls[-1] == "save_session", (
        "the authoritative session row MUST be the final write of a flush — "
        f"got {inner.calls}. See docs/SNOWFLAKE_LATENCY_BRIEF.md §7."
    )
    assert inner.calls.count("save_session") == 1


def test_flush_turn_lands_game_state_but_defers_appends() -> None:
    """Per-turn tier vs per-day tier."""
    inner = _RecordingStore()
    buf = BufferedSocStore(inner)
    buf.upsert_policy("s1", 1, "p1", [{"m": 1}])
    buf.append_log("s1", 1, [{"level": "info", "text": "deferred"}])
    buf.append_replay_frames("s1", 1, [{"frame_idx": 0}])
    buf.save_session(_row("s1"))

    inner.calls.clear()
    buf.flush_turn()
    assert "save_session" in inner.calls, inner.calls
    assert "upsert_policy" in inner.calls, inner.calls
    assert inner.calls[-1] == "save_session"
    assert "append_log" not in inner.calls, (
        "log lines are the per-day tier and must not be flushed per turn"
    )
    assert "append_replay_frames" not in inner.calls
    # ...and they are still pending, not dropped
    assert inner.list_log("s1") == []
    buf.flush()
    assert len(inner.list_log("s1")) == 1


def test_repeat_loads_after_a_save_hit_the_cache() -> None:
    """3x load_session per turn was 1.6s of re-reading our own write."""
    inner = _RecordingStore()
    buf = BufferedSocStore(inner)
    buf.save_session(_row("s1"))
    inner.calls.clear()
    for _ in range(3):
        got = buf.load_session("s1")
        assert got is not None and got["session_id"] == "s1"
    assert inner.calls == [], (
        f"reads after our own write must not hit the store, got {inner.calls}"
    )


def test_a_session_we_only_read_is_never_answered_from_a_stale_cache() -> None:
    """A spectator must see the writer's day, not the one it first saw.

    The cache has no expiry, so answering reads from it would freeze a
    session at whatever this process last saw — for the life of the
    process. Reachable the moment buffering became the default: a server
    with a Snowflake game open while ``soc season`` advances it in
    another terminal showed a board that never moved again.
    """
    inner = _RecordingStore()
    reader = BufferedSocStore(inner)
    inner.save_session(_row("s1", day=1))

    assert reader.load_session("s1")["day"] == 1
    inner.save_session(_row("s1", day=4))  # another process took the night

    assert reader.load_session("s1")["day"] == 4, (
        "a reader that never wrote this session must go back to the store"
    )


def test_day_rollover_flushes_the_previous_days_appends() -> None:
    """The self-managing boundary: no caller has to remember to flush."""
    inner = _RecordingStore()
    buf = BufferedSocStore(inner)
    buf.save_session(_row("s1", day=1))
    buf.append_log("s1", 1, [{"level": "info", "text": "day one"}])
    buf.append_replay_frames("s1", 1, [{"frame_idx": 0}])
    assert inner.list_log("s1") == [], "should still be buffered"

    buf.save_session(_row("s1", day=2))  # rollover
    assert len(inner.list_log("s1")) == 1, (
        "a day rollover must land the previous day's appends"
    )
    assert len(inner.list_replay_frames("s1")) == 1


def test_season_complete_forces_a_full_flush() -> None:
    inner = _RecordingStore()
    buf = BufferedSocStore(inner)
    buf.append_log("s1", 1, [{"level": "info", "text": "final"}])
    buf.save_session(_row("s1", day=1, phase="season_complete"))
    assert len(inner.list_log("s1")) == 1, (
        "the end of a season must not leave anything buffered"
    )


def test_list_log_merges_pending_with_matching_seq() -> None:
    """The LOG panel must see buffered lines, with the seq they will get."""
    inner = _RecordingStore()
    inner.append_log("s1", 1, [{"level": "info", "text": "durable"}])
    buf = BufferedSocStore(inner)
    buf.append_log("s1", 1, [{"level": "info", "text": "pending A"}])
    buf.append_log("s1", 1, [{"level": "info", "text": "pending B"}])

    rows = buf.list_log("s1")
    assert [r["text"] for r in rows] == ["durable", "pending A", "pending B"]
    seqs = [int(r["seq"]) for r in rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3, seqs
    # and the merge must not have flushed
    assert len(inner.list_log("s1")) == 1

    # once flushed, the durable seqs must match what we predicted
    buf.flush()
    assert [int(r["seq"]) for r in inner.list_log("s1")] == seqs

    limited = buf.list_log("s1", limit=2)
    assert [r["text"] for r in limited] == ["pending A", "pending B"]


def test_day_index_reports_pending_frame_counts() -> None:
    """get_session_status gates the replay animation on this total."""
    inner = _RecordingStore()
    buf = BufferedSocStore(inner)
    buf.append_replay_frames("s1", 1, [{"frame_idx": i} for i in range(6)])
    index = buf.day_index("s1")
    assert len(index) == 1 and index[0]["day"] == 1
    assert index[0]["frame_count"] == 6, (
        "a pending night must still report its frame count, or the UI skips "
        "straight to the final frame with no animation"
    )
    # global_idx is assigned server-side, so it is honestly unknown until flush
    assert index[0]["first_global_idx"] is None


def test_unknown_reads_flush_then_delegate() -> None:
    """Anything not explicitly handled must be correct by default."""
    inner = _RecordingStore()
    buf = BufferedSocStore(inner)
    buf.append_log("s1", 1, [{"level": "info", "text": "x"}])
    buf.save_session(_row("s1"))
    # list_sessions is not overridden -> goes through __getattr__
    sessions = buf.list_sessions()
    assert any(s["session_id"] == "s1" for s in sessions), sessions
    assert len(inner.list_log("s1")) == 1, (
        "an unhandled read must flush first so it cannot observe a stale store"
    )


def test_nothing_is_lost_across_a_multi_day_multi_session_run() -> None:
    """The durability property, end to end."""
    inner = _RecordingStore()
    buf = BufferedSocStore(inner)
    expected: Dict[str, int] = {}
    for sid in ("s1", "s2"):
        total = 0
        for day in (1, 2, 3):
            buf.save_session(_row(sid, day=day))
            buf.append_log(sid, day, [
                {"level": "info", "text": f"{sid} d{day} line {i}"}
                for i in range(4)
            ])
            total += 4
            buf.append_replay_frames(
                sid, day, [{"frame_idx": i} for i in range(5)],
            )
            buf.append_agent_invocation(
                {"session_id": sid, "day": day, "agent_id": "a"},
            )
            buf.flush_turn()
        expected[sid] = total
    buf.flush()

    for sid, n_log in expected.items():
        assert len(inner.list_log(sid)) == n_log, (
            f"{sid}: expected {n_log} log rows, got {len(inner.list_log(sid))}"
        )
        assert len(inner.list_replay_frames(sid)) == 15, sid
        assert len(inner.list_agent_invocations(sid)) == 3, sid
        assert inner.load_session(sid) is not None


def test_snowpark_session_for_still_resolves_through_the_wrapper() -> None:
    """AGENT MEMORY depends on this. Do not remove.

    ``SOC_AGENT_MEMORY`` is written by the tabula_v12 harness with raw SQL,
    not through the store protocol — it asks
    ``backend.snowpark_session_for(store)`` for a live session and skips the
    write when there isn't one. That indirection exists so a game running on
    the memory backend never persists its agent memory into the account
    (memory it could never read back).

    Wrapping the store must not disturb either half of that contract:

    * a wrapped Snowflake store must still yield its Session, or agent memory
      silently stops persisting — no error, just an agent that forgets;
    * a wrapped in-memory store must still yield ``None``, or a memory game
      starts writing into Snowflake.

    ``BufferedSocStore.__getattr__`` passes non-callables straight through,
    which is what makes both hold. A future refactor that wraps attribute
    access would break this without failing anything else.
    """
    from sea_of_colours.snowpark.backend import snowpark_session_for

    class _FakeSnowparkStore(InMemorySocStore):
        session = object()  # stands in for a live Snowpark Session

    wrapped_sf = BufferedSocStore(_FakeSnowparkStore())
    assert snowpark_session_for(wrapped_sf) is _FakeSnowparkStore.session, (
        "a wrapped Snowflake store must still expose .session or agent "
        "memory stops being persisted, silently"
    )

    wrapped_mem = BufferedSocStore(InMemorySocStore())
    assert snowpark_session_for(wrapped_mem) is None, (
        "a wrapped memory store must not expose a session, or a memory-backed "
        "game writes agent memory into the account"
    )


def test_buffering_is_on_unless_it_is_turned_off(monkeypatch):
    """The default flipped in v1.43, and the escape hatch has to survive it.

    It shipped off while it was new, which meant the canonical run
    command never used it: the saving was collected only by whoever had
    read the latency brief. Measured on a full seven-day season it is
    97.9s -> 62.2s with heuristic seats and 254.5s -> 205.3s with V12 in
    one, so leaving it off was costing every Snowflake seat real time
    for no stated reason.

    `=0` matters as much as the default. The deferred tier is a day of
    LOG text, replay frames and invocation rows, so a season that comes
    out missing history should have one thing to try before anyone goes
    looking in the store.
    """
    from sea_of_colours.snowpark.buffered_store import buffering_enabled

    monkeypatch.delenv("SOC_BUFFERED_STORE", raising=False)
    assert buffering_enabled(), "buffering should be on when nothing says otherwise"

    for off in ("0", "false", "no", "off", "OFF", " 0 "):
        monkeypatch.setenv("SOC_BUFFERED_STORE", off)
        assert not buffering_enabled(), f"{off!r} must turn buffering off"

    for on in ("1", "true", "yes", "on", "ON"):
        monkeypatch.setenv("SOC_BUFFERED_STORE", on)
        assert buffering_enabled(), f"{on!r} must leave buffering on"
