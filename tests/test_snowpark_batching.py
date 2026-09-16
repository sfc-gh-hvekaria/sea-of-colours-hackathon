"""SnowparkSocStore batching regression tests (v0.9.5).

Background: pre-v0.9.5 the snowpark store's hottest write paths fired
one ``INSERT`` per row, which dominated the user-perceived PRAXIS
latency on Snowflake — a 150-frame night cost ~150 round-trips per
``append_replay_frames`` call.

These tests pin the new batched behaviour:

* ``append_replay_frames`` issues at most ``ceil(N / 50) + 1``
  statements for N frames (the ``+1`` is the leading
  ``SELECT MAX(global_idx)`` round-trip).
* ``replace_entity_state`` issues exactly 2 statements (DELETE +
  one batched INSERT) regardless of how many entities are in the
  payload.
* ``_replace_parcels`` (via ``replace_hoard``) issues exactly 2
  statements for a non-empty hoard.

v0.9.6 follow-ups:

* ``upsert_grid_cells`` chunks at 1500 rows, so a full 40×28 grid
  (1120 cells) lands in ONE statement instead of six.
* ``replace_hoard_bundle`` / ``replace_shipped_bundle`` collapse
  both players into ONE DELETE + ONE INSERT each (instead of 4
  round-trips for the per-player loop).
* ``save_session_full`` fans every independent phase out across
  a thread pool, so each table is still written exactly once.

We drive the store with a fake Snowpark session that captures every
SQL string instead of executing it, so the tests pass without any
live Snowflake connectivity.
"""

from __future__ import annotations

import json
import re
from typing import Any, List

from sea_of_colours.snowpark.snowpark_store import SnowparkSocStore


class _FakeDF:
    """Snowpark ``DataFrame`` stand-in returned by ``session.sql``.

    The store only needs ``.collect()`` to return an iterable of
    row-like objects. We return an empty list for INSERT/MERGE/DELETE
    statements and pretend the leading ``SELECT MAX(global_idx)`` of
    a fresh table yields ``[-1]`` (so the first batch starts at
    ``global_idx = 0``).
    """

    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def collect(self) -> List[Any]:
        return list(self._rows)


class _FakeSession:
    """Captures every SQL string the store hands to ``session.sql``.

    v1.41 — also captures the bind parameters. The store no longer
    inlines payloads as literals, so a test that only looks at the SQL
    text can no longer see what was written; assertions about *content*
    have to read ``binds`` instead.
    """

    def __init__(self) -> None:
        self.statements: List[str] = []
        self.binds: List[Any] = []

    def sql(self, query: str, params: Any = None) -> _FakeDF:
        self.statements.append(query)
        self.binds.append(params)
        # First SELECT against a fresh SOC_REPLAY_FRAME table returns
        # MAX(global_idx) = -1 → next_global starts at 0.
        if "MAX(global_idx)" in query:
            return _FakeDF([(-1,)])
        return _FakeDF([])

    def bind_for(self, prefix: str) -> Any:
        """The bind list of the first statement starting with ``prefix``."""
        for sql, params in zip(self.statements, self.binds):
            if sql.startswith(prefix):
                return params
        raise AssertionError(f"no statement began with {prefix!r}")


def _make_frame(idx: int) -> dict:
    """Build a frame dict with every JSON-bearing column populated.

    Mirrors the shape ``GameSession.replay_push_scene`` emits so the
    batched code path exercises ``PARSE_JSON`` for every VARIANT
    column. Values are intentionally tiny so the test doesn't trip
    Snowflake's 1MB SQL text limit even if the chunker is broken.
    """
    return {
        "frame_idx": idx,
        "caption": f"frame {idx}",
        "tag": "tick",
        "owner": "p1",
        "cells": [{"x": 0, "y": 0, "ch": "  "}],
        "cells_player_p1": [{"x": 0, "y": 0, "ch": "  "}],
        "cells_player_p2": [{"x": 0, "y": 0, "ch": "  "}],
        "entities": [{"id": "harvester_p1", "x": 1, "y": 2}],
        "hoard": [{"slot": 0}],
        "collisions": [],
        "crushed_probes": [],
        "scheduled_orders": [],
        "attempted": "step",
        "outcome": "applied",
        "hour": 3,
        "emp": None,
        "mine": None,
        "chaff": None,
        "emp_clouds": [],
        "mines_active": [],
    }


def test_append_replay_frames_batches_to_few_statements() -> None:
    """150 frames must collapse to a couple of inserts, not 150.

    Pre-v0.9.5 this was 1 SELECT + 150 INSERTs (~30s+ on live
    Snowflake). v1.41 binds the batch as one JSON array and flattens it
    server-side, which lifts the chunk to 100 frames, and folds the
    ``MAX(global_idx)`` lookup into the INSERT — so 150 frames is
    1 DELETE + 2 INSERTs and no standalone SELECT at all.
    """
    sess = _FakeSession()
    store = SnowparkSocStore(sess)
    frames = [_make_frame(i) for i in range(150)]

    store.append_replay_frames("sess-batch", day=1, frames=frames)

    inserts = [s for s in sess.statements if s.startswith("INSERT INTO SOC_REPLAY_FRAME")]
    standalone = [s for s in sess.statements if s.startswith("SELECT")]
    assert standalone == [], (
        f"the global_idx lookup must ride inside the INSERT, got {standalone}"
    )
    assert len(inserts) == 2, (
        f"expected 2 batched INSERTs for 150 frames (100/batch), "
        f"got {len(inserts)}"
    )
    assert all("MAX(global_idx)" in s for s in inserts)


def test_append_replay_frames_empty_is_a_noop() -> None:
    """Zero frames must not issue any SQL.

    The leading SELECT was also skipped pre-v0.9.5 for the empty
    list — that contract still holds.
    """
    sess = _FakeSession()
    store = SnowparkSocStore(sess)
    store.append_replay_frames("sess-empty", day=1, frames=[])
    assert sess.statements == []


def test_append_replay_frames_small_batch_is_single_insert() -> None:
    """A typical night (~30 frames) fits in one batched INSERT.

    This is the common case — most PRAXIS submissions resolve in a
    single round-trip after the leading MAX(global_idx) query.
    """
    sess = _FakeSession()
    store = SnowparkSocStore(sess)
    frames = [_make_frame(i) for i in range(30)]

    store.append_replay_frames("sess-small", day=1, frames=frames)

    inserts = [s for s in sess.statements if s.startswith("INSERT INTO SOC_REPLAY_FRAME")]
    assert len(inserts) == 1
    body = inserts[0]

    # v1.41 — the rows travel as ONE bound JSON array, so the SQL text is
    # short, constant, and mentions each column exactly once. That is the
    # whole point of the change: SQL text length drove Snowflake's
    # compilation time, which was ~40% of a turn.
    # Two binds: the frame array, and the session id for the running
    # global_idx max that is now folded into the same statement.
    assert "FLATTEN" in body and body.count("?") == 2, body
    assert len(body) < 2000, (
        f"the statement should be ~1KB of constant text, got {len(body)} "
        "bytes — something is being inlined again"
    )
    for col in ("cells", "entities", "mines_active"):
        # ``\b`` keeps ``cells`` from also matching ``cells_player_p1``.
        hits = re.findall(rf"v\.value:{col}\b", body)
        assert len(hits) == 1, f"{col}: {len(hits)}"

    # The payload really does carry all 30 frames. ``global_idx`` is NOT
    # in it any more — the server derives it from the running max, which
    # is what removed the extra round-trip.
    binds = sess.bind_for("INSERT INTO SOC_REPLAY_FRAME")
    payload = json.loads(binds[0])
    assert binds[1] == "sess-small"
    assert len(payload) == 30
    assert "global_idx" not in payload[0]
    assert [r["frame_idx"] for r in payload] == list(range(30))
    assert payload[0]["cells"] == [{"x": 0, "y": 0, "ch": "  "}]
    assert payload[0]["hour"] == 3


def test_replace_entity_state_uses_one_delete_plus_one_insert() -> None:
    """``replace_entity_state`` is now 2 statements regardless of size.

    Pre-v0.9.5 it was 1 DELETE + N per-entity INSERTs; the new path
    is 1 DELETE + 1 batched INSERT.
    """
    sess = _FakeSession()
    store = SnowparkSocStore(sess)

    rows = [
        {
            "entity_id": f"e{i}",
            "entity_type": "harvester",
            "owner": "p1",
            "x": i,
            "y": i,
            "carrying_red": False,
            "orbital_cargo_red": False,
            "cargo_squares": [],
            "lost_last_night": False,
        }
        for i in range(8)
    ]
    store.replace_entity_state("sess-ent", rows)

    deletes = [s for s in sess.statements if s.startswith("DELETE FROM SOC_ENTITY_STATE")]
    inserts = [s for s in sess.statements if s.startswith("INSERT INTO SOC_ENTITY_STATE")]
    assert len(deletes) == 1
    assert len(inserts) == 1, (
        f"expected 1 batched INSERT for 8 entities, got {len(inserts)}"
    )


def test_replace_hoard_uses_one_delete_plus_one_insert() -> None:
    """A 25-parcel hoard must batch into a single INSERT.

    Pre-v0.9.5 a full hoard cost 1 + 25 round-trips PER PLAYER PER
    SAVE — 50 round-trips total just for hoards. The batched path
    keeps it at 4 (2 per player).
    """
    sess = _FakeSession()
    store = SnowparkSocStore(sess)
    rows = [
        {
            "slot": i,
            "square_id": f"sq-{i}",
            "origin_x": i,
            "origin_y": 0,
            "origin_tile": 2,
            "origin_purity": 80,
            "harvested_day": 1,
            "site_uid": f"uid-{i}",
            "payload": {"slot": i, "purity": 80},
        }
        for i in range(25)
    ]
    store.replace_hoard("sess-hoard", "p1", rows)
    deletes = [s for s in sess.statements if s.startswith("DELETE FROM SOC_HOARD_PARCEL")]
    inserts = [s for s in sess.statements if s.startswith("INSERT INTO SOC_HOARD_PARCEL")]
    assert len(deletes) == 1
    assert len(inserts) == 1, (
        f"expected 1 batched INSERT for 25 hoard parcels, got {len(inserts)}"
    )


def test_upsert_grid_cells_full_board_is_a_single_statement() -> None:
    """40×28 = 1120 cells must collapse to ONE MERGE statement.

    Pre-v0.9.6 the chunker capped each statement at 200 rows so a
    full grid cost 6 round-trips. v0.9.6 bumped the cap to 1500
    rows, which fits the entire board in one VALUES list under
    Snowflake's SQL text limit.
    """
    sess = _FakeSession()
    store = SnowparkSocStore(sess)
    rows = [
        {"x": x, "y": y, "tile": (x + y) % 5, "purity": (x * 7 + y * 3) % 256}
        for y in range(28) for x in range(40)
    ]
    assert len(rows) == 1120
    store.upsert_grid_cells("sess-grid", rows)
    merges = [s for s in sess.statements if s.startswith("MERGE INTO SOC_GRID_CELL")]
    assert len(merges) == 1, (
        f"expected 1 batched MERGE for {len(rows)} cells, got {len(merges)}"
    )


def test_replace_hoard_bundle_collapses_both_seats_into_two_statements() -> None:
    """``replace_hoard_bundle`` for {p1,p2} must issue exactly 1 DELETE + 1 INSERT.

    Pre-v0.9.6 ``save_session_full`` looped over both players and
    called the single-owner :meth:`replace_hoard` twice — 4 round-
    trips. The bundle variant covers both seats in 2 round-trips
    (one session-scoped DELETE, one batched INSERT carrying both
    owners interleaved).
    """
    sess = _FakeSession()
    store = SnowparkSocStore(sess)

    def _rows(prefix: str):
        return [
            {
                "slot": i,
                "square_id": f"{prefix}-sq-{i}",
                "origin_x": i,
                "origin_y": 0,
                "origin_tile": 1,
                "origin_purity": 50 + i,
                "harvested_day": 2,
                "site_uid": f"{prefix}-uid-{i}",
                "payload": {"slot": i},
            }
            for i in range(5)
        ]

    store.replace_hoard_bundle(
        "sess-bundle", {"p1": _rows("p1"), "p2": _rows("p2")},
    )

    deletes = [s for s in sess.statements if s.startswith("DELETE FROM SOC_HOARD_PARCEL")]
    inserts = [s for s in sess.statements if s.startswith("INSERT INTO SOC_HOARD_PARCEL")]
    assert len(deletes) == 1, (
        f"expected 1 session-scoped DELETE, got {len(deletes)}"
    )
    # The single DELETE must filter on BOTH owners so we don't blow
    # away a third seat's rows (future-proofing). Check the owner
    # list is present in the WHERE clause.
    assert "owner IN" in deletes[0], deletes[0]
    assert "'p1'" in deletes[0] and "'p2'" in deletes[0]
    assert len(inserts) == 1, (
        f"expected 1 batched INSERT covering both seats, got {len(inserts)}"
    )
    # And the INSERT must carry rows from both seats (look for
    # owner labels in the VALUES literal).
    assert "'p1'" in inserts[0] and "'p2'" in inserts[0]


def test_replace_shipped_bundle_empty_owner_keeps_the_delete() -> None:
    """A seat with no shipped parcels still gets its old rows cleared.

    The bundle path can't skip the DELETE just because one seat is
    empty — otherwise stale shipped rows survive across saves and
    corrupt the score. We accept either ``[]`` or omitted-owner
    payloads.
    """
    sess = _FakeSession()
    store = SnowparkSocStore(sess)
    store.replace_shipped_bundle(
        "sess-shipped-empty", {"p1": [], "p2": []},
    )
    deletes = [s for s in sess.statements if s.startswith("DELETE FROM SOC_SHIPPED_PARCEL")]
    inserts = [s for s in sess.statements if s.startswith("INSERT INTO SOC_SHIPPED_PARCEL")]
    assert len(deletes) == 1, "DELETE must still fire so stale rows are evicted"
    assert len(inserts) == 0, "no INSERT when every owner is empty"


def test_save_session_full_writes_only_what_is_read_back() -> None:
    """v1.41 — the save path must not write the write-only projections.

    Square identity, asset records, entity state and grid cells were
    written on every save and read by nothing (the store protocol has no
    reader for any of them). On Snowflake that was ~2.7s of every turn.
    They are derivable from ``json_state``, so the save path drops them
    and ``SOC_LEADERBOARD`` flattens the ledger out of the blob instead.

    This test is the guard against them creeping back in — reintroducing
    one is a several-hundred-millisecond regression per turn that no
    local timing would ever reveal.
    """
    import os as _os
    from sea_of_colours.snowpark import engine as _engine
    from sea_of_colours.snowpark.store import InMemorySocStore
    from sea_of_colours.game.session import GameSession

    # Build a small session so the test stays fast.
    sess = GameSession.new(8, 6, seed=42)
    # The default backend already saves once on ``GameSession.new`` -
    # bypass that by routing through a fresh InMemorySocStore and
    # capturing how many times each method is invoked.

    call_log: list[str] = []

    class _CountingStore(InMemorySocStore):
        def save_session(self, row):  # type: ignore[override]
            call_log.append("save_session")
            super().save_session(row)

        def upsert_square_identity(self, sid, rows):  # type: ignore[override]
            call_log.append("upsert_square_identity")
            super().upsert_square_identity(sid, rows)

        def upsert_asset_records(self, sid, rows):  # type: ignore[override]
            call_log.append("upsert_asset_records")
            super().upsert_asset_records(sid, rows)

        def replace_entity_state(self, sid, rows):  # type: ignore[override]
            call_log.append("replace_entity_state")
            super().replace_entity_state(sid, rows)

        def upsert_grid_cells(self, sid, rows):  # type: ignore[override]
            call_log.append("upsert_grid_cells")
            super().upsert_grid_cells(sid, rows)

        def replace_hoard_bundle(self, sid, by_owner):  # type: ignore[override]
            call_log.append("replace_hoard_bundle")
            super().replace_hoard_bundle(sid, by_owner)

        def replace_shipped_bundle(self, sid, by_owner):  # type: ignore[override]
            call_log.append("replace_shipped_bundle")
            super().replace_shipped_bundle(sid, by_owner)

    store = _CountingStore()
    # Force parallel mode on regardless of caller environment.
    prev = _engine._PARALLEL_SAVE
    _engine._PARALLEL_SAVE = True
    try:
        _engine.save_session_full(store, sess)
    finally:
        _engine._PARALLEL_SAVE = prev

    expected = {
        "save_session",
        "replace_hoard_bundle",
        "replace_shipped_bundle",
    }
    assert set(call_log) == expected, (
        f"save_session_full wrote {sorted(set(call_log) - expected)} — "
        "those are write-only projections and must stay off the save path"
    )
    assert len(call_log) == 3, (
        f"each phase must run exactly once, got {sorted(call_log)}"
    )

    # THE ORDERING GUARANTEE (E1b). ``json_state`` is the only row
    # ``_hydrate_session`` reads, so it must land LAST — after every
    # sibling write has completed. If it moves earlier, a following
    # hydrate can observe a pre-resolution snapshot and the season runner
    # re-resolves the same night, which is the "day 3 repeats three
    # times" replay bug. That race only opens on real Snowflake, where
    # writes are slow enough to interleave, so this static ordering check
    # is the only cheap defence we have.
    assert call_log[-1] == "save_session", (
        f"the authoritative session row must be written last, got {call_log}"
    )


def test_repeat_empty_parcel_bundle_skips_the_delete() -> None:
    """v1.42 — replacing empty with empty must not re-issue the DELETE.

    Hoards are empty for most of the early game, so the unconditional
    session-scoped DELETE ran dozens of times per season and matched no
    rows — ~8.2s of server time plus ~300ms of client latency each (see
    docs/SNOWFLAKE_LATENCY_BRIEF.md §T1.1).

    The contract this pins:

    * the FIRST empty bundle still DELETEs, because we cannot know the
      table is empty until we have emptied it ourselves;
    * a REPEAT empty bundle for the same scope issues nothing;
    * a NON-EMPTY bundle re-arms the guard, so the next empty bundle
      DELETEs again rather than silently leaving stale rows behind.

    That last case is the one that would corrupt the score, and it is why
    the guard is keyed on the exact (table, session, owner-set) scope.
    """
    sess = _FakeSession()
    store = SnowparkSocStore(sess)

    def _deletes() -> List[str]:
        return [
            s for s in sess.statements
            if s.startswith("DELETE FROM SOC_HOARD_PARCEL")
        ]

    store.replace_hoard_bundle("sess-empty-skip", {"p1": [], "p2": []})
    assert len(_deletes()) == 1, "the first empty bundle must still evict"

    store.replace_hoard_bundle("sess-empty-skip", {"p1": [], "p2": []})
    assert len(_deletes()) == 1, (
        "a repeat empty bundle for a scope we already emptied must issue "
        "no SQL at all"
    )

    # A different session is a different scope and must not inherit the fact.
    store.replace_hoard_bundle("sess-other", {"p1": [], "p2": []})
    assert len(_deletes()) == 2, "the guard must be per-session"

    # Now write real rows, then clear again — the DELETE must come back.
    store.replace_hoard_bundle(
        "sess-empty-skip",
        {"p1": [{"slot": 0, "square_id": "sq-1", "payload": {"a": 1}}], "p2": []},
    )
    assert len(_deletes()) == 3, "a non-empty bundle always DELETEs first"
    store.replace_hoard_bundle("sess-empty-skip", {"p1": [], "p2": []})
    assert len(_deletes()) == 4, (
        "after rows were written the scope is dirty again, so clearing it "
        "must re-issue the DELETE — otherwise stale parcels survive and "
        "corrupt bulk_session_scores"
    )


def test_save_agent_rationale_writes_only_the_authoritative_row() -> None:
    """v1.42 — a rationale must not trigger the full save fan-out.

    ``save_agent_rationale`` appends one line to ``sess.log`` and nothing
    else. Routing that through ``save_session_full`` re-wrote both parcel
    tables with byte-identical data on every agent turn — 6 of the 13
    saves in a 6-turn season, 4 redundant statements each.

    This pins two things at once: that the parcel writes are gone, and
    that the log line still survives a hydrate (which is the behaviour the
    full save was there to provide, and what ``get_session_status`` reads
    for its ``log_tail``).
    """
    from sea_of_colours.snowpark import engine as _engine
    from sea_of_colours.snowpark.store import InMemorySocStore
    from sea_of_colours.game.session import GameSession

    call_log: List[str] = []

    class _CountingStore(InMemorySocStore):
        def save_session(self, row):  # type: ignore[override]
            call_log.append("save_session")
            super().save_session(row)

        def replace_hoard_bundle(self, sid, by_owner):  # type: ignore[override]
            call_log.append("replace_hoard_bundle")
            super().replace_hoard_bundle(sid, by_owner)

        def replace_shipped_bundle(self, sid, by_owner):  # type: ignore[override]
            call_log.append("replace_shipped_bundle")
            super().replace_shipped_bundle(sid, by_owner)

    store = _CountingStore()
    game = GameSession.new(8, 6, seed=42)
    store.save_session(_engine._save_session_row(game))
    call_log.clear()

    _engine.save_agent_rationale(
        store,
        game.session_id,
        day=1,
        agent_id="probe_agent",
        player="p1",
        rationale="held position to bank purity",
    )

    assert "replace_hoard_bundle" not in call_log, (
        "a rationale changes no parcel state — writing the parcel tables "
        "here is pure waste"
    )
    assert "replace_shipped_bundle" not in call_log
    assert call_log.count("save_session") == 1, (
        f"exactly one authoritative write expected, got {call_log}"
    )

    # The line must be durable in the blob, or /status loses its log_tail.
    reloaded = _engine._hydrate_session(store, game.session_id)
    texts = [e.get("text", "") for e in (reloaded.log or [])]
    assert any("held position to bank purity" in t for t in texts), (
        "the rationale must survive the hydrate — get_session_status reads "
        f"log_tail straight off sess.log, got {texts[-3:]}"
    )
