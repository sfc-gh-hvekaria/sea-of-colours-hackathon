"""Session persistence policy tests — append-only contract (v0.5).

Prior to v0.5, ``engine.init_session`` called
``store.wipe_all_sessions()`` as its first step. That made it
impossible for the CLI orchestrator and the live UI to coexist: any
"NEW GAME" click in the browser would silently delete every prior
season from Snowflake, including ones the CLI had carefully named and
intended to replay later.

The contract is now:

1. ``init_session`` is **append-only** — minting a new session never
   touches existing rows.
2. ``store.wipe_all_sessions()`` is still public and callable — both
   the CLI's ``--wipe-first`` flag and any future "archive then
   reset" feature can use it explicitly.

These tests pin those two behaviours so a future refactor can't
silently regress to "every new game wipes history" again.
"""

from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "memory")

import pytest

from sea_of_colours.snowpark import backend as soc_backend
from sea_of_colours.snowpark import engine as soc_engine


@pytest.fixture()
def store():
    soc_backend.reset_for_tests()
    return soc_backend.get_store()


def test_init_session_does_not_wipe_prior_sessions(store):
    """The append-only contract: minting a session must preserve prior rows.

    REGRESSION GUARD. Until v0.5 this test would have failed because
    ``init_session`` called ``store.wipe_all_sessions()`` as its first
    step. With the harness reframe + CLI orchestrator, multiple
    seasons (live UI + CLI batches) need to coexist.
    """
    first = soc_engine.init_session(store, seed=11, width=12, height=8)
    second = soc_engine.init_session(store, seed=22, width=12, height=8)
    third = soc_engine.init_session(store, seed=33, width=12, height=8)

    # All three rows must coexist in SOC_GAME_SESSION (or its memory
    # mirror). The store API returns a list of session metadata dicts.
    rows = store.list_sessions()
    session_ids = {row["session_id"] for row in rows}
    assert first["session_id"] in session_ids, "first session was wiped"
    assert second["session_id"] in session_ids, "second session was wiped"
    assert third["session_id"] in session_ids
    # Three distinct session_ids — no accidental reuse of the same row.
    assert len({first["session_id"], second["session_id"], third["session_id"]}) == 3


def test_init_session_preserves_season_names_across_inits(store):
    """Season names from prior runs must survive subsequent init_session calls.

    The watcher frontend keys off ``season_name`` (slug-derived). If a
    future change re-wires init_session to wipe, the very feature
    that makes the watcher useful breaks silently.
    """
    a = soc_engine.init_session(store, seed=100, width=12, height=8)
    b = soc_engine.init_session(store, seed=200, width=12, height=8)

    rows = store.list_sessions()
    by_id = {row["session_id"]: row for row in rows}
    assert by_id[a["session_id"]].get("season_name") == a["season_name"]
    assert by_id[b["session_id"]].get("season_name") == b["season_name"]
    # The two seeds should produce distinct deterministic names.
    assert a["season_name"] != b["season_name"]


def test_explicit_wipe_still_clears_everything(store):
    """``store.wipe_all_sessions()`` remains the supported reset path.

    Append-only is the default but callers (CLI ``--wipe-first``,
    future archive flows) can opt back into the old behaviour by
    calling the wipe directly. This test confirms the API is still
    wired and functional.
    """
    a = soc_engine.init_session(store, seed=1, width=12, height=8)
    b = soc_engine.init_session(store, seed=2, width=12, height=8)
    pre = store.list_sessions()
    assert len(pre) >= 2

    store.wipe_all_sessions()

    post = store.list_sessions()
    post_ids = {row["session_id"] for row in post}
    assert a["session_id"] not in post_ids
    assert b["session_id"] not in post_ids


def test_log_entries_preserve_day_phase_kind_through_hydrate_roundtrip():
    """REGRESSION (v0.9.8): rich log fields survive save→load.

    Pre-v0.9.8 ``_normalize_log_entries`` whitelisted every entry down
    to ``{level, text}``. ``log_info`` stamped ``day`` at write time
    but ``GameSession.from_dict`` immediately stripped it, so the
    frontend LOG / AGENT day filter saw ``day=None`` on every row.
    In multi-seat games (4 bots × N days of accumulated events) the
    LOG visibly collapsed into a single undifferentiated stream —
    the user-reported "log breaks in multiplayer / works in single
    player" symptom (the latter was just less crowded so the
    collapse looked normal).

    This test pins the contract: ``day`` (added by ``log_info``),
    ``phase`` (added by orbit chatter), ``kind`` + ``data`` (added
    by ``log_event``) must round-trip through ``to_dict`` →
    ``from_dict`` unchanged.
    """
    from sea_of_colours.game.session import GameSession

    sess = GameSession.new(12, 8, seed=42)
    sess.day = 3
    sess.log_info("regular event on day 3")
    sess.log_info("[orbit] day 3: settlement chatter")
    sess.log_event("probe_launch", "p1 launched probe_p1_1", probe="p1_1", x=4, y=5)

    snap = sess.to_dict()
    revived = GameSession.from_dict(snap)

    # The three entries we just appended must all carry ``day=3``.
    last_three = revived.log[-3:]
    assert all(int(e["day"]) == 3 for e in last_three), (
        f"day field lost in roundtrip: {last_three}"
    )
    # Orbit line keeps its ``phase`` tag for the LOG filter.
    assert last_three[1].get("phase") == "orbit"
    # Structured event keeps its ``kind`` + ``data`` for the agent
    # competitor-intel surfacing.
    assert last_three[2].get("kind") == "probe_launch"
    assert last_three[2].get("data", {}).get("probe") == "p1_1"
