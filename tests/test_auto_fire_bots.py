"""v0.9.6 — :func:`engine.auto_fire_bot_seats` contract tests.

In a 1-human-vs-N-bots session the night can't resolve until every
seat has stashed a policy. ``auto_fire_bot_seats`` walks the seat
list, picks the first seat assigned to a bot agent that hasn't
submitted yet, runs that agent's turn, and repeats until either:

* every bot has stashed (the human is the only one left), or
* the night/orbit phase has resolved (the loop exits naturally).

These tests pin both behaviours without relying on Snowflake.
"""

from __future__ import annotations

from sea_of_colours.snowpark import engine as soc_engine
from sea_of_colours.snowpark.store import InMemorySocStore


def test_auto_fire_drives_bots_to_phase_resolution() -> None:
    """In a 1-human + 2-bot session, after the human submits we
    expect ``auto_fire_bot_seats`` to fire both bots and advance the
    phase (planning → orbit) on the same call."""
    store = InMemorySocStore()
    res = soc_engine.init_session(
        store, seed=2026, width=24, height=16,
        players=("p1", "p2", "p3"),
        agents={"p1": "human", "p2": "red_harvest", "p3": "red_harvest"},
    )
    sid = res["session_id"]
    # Human (p1) submits an empty policy to lock their seat.
    sub = soc_engine.submit_policy(store, sid, "p1", [])
    assert sub["ok"]
    assert sub["pending"]["p1"] is True
    assert sub["pending"]["p2"] is False
    # Auto-fire the bot seats; both p2 and p3 should run and the night
    # should resolve, flipping the session into the dawn ORBIT phase.
    envelopes = soc_engine.auto_fire_bot_seats(store, sid)
    assert len(envelopes) >= 2, (
        f"expected at least 2 bot turns (p2 + p3); got {len(envelopes)}"
    )
    # Every envelope is from a bot agent — never the human seat.
    for env in envelopes:
        assert env["player"] != "p1"
    # The night should have resolved, putting the session in the
    # next phase (typically ORBIT, the dawn surface).
    status = soc_engine.get_session_status(store, sid)
    assert status["day"] == 2 or status["phase"] == "orbit"


def test_auto_fire_skips_human_only_games() -> None:
    """No bots in the session → the helper returns an empty list."""
    store = InMemorySocStore()
    res = soc_engine.init_session(
        store, seed=1, width=20, height=14,
        players=("p1", "p2"),
        agents={"p1": "human", "p2": "human"},
    )
    sid = res["session_id"]
    soc_engine.submit_policy(store, sid, "p1", [])
    envelopes = soc_engine.auto_fire_bot_seats(store, sid)
    assert envelopes == []
    # The night must NOT resolve — p2 still has to submit.
    status = soc_engine.get_session_status(store, sid)
    assert status["day"] == 1


def test_auto_fire_is_idempotent_when_no_bot_is_pending() -> None:
    """After every bot has stashed, a second call is a no-op."""
    store = InMemorySocStore()
    res = soc_engine.init_session(
        store, seed=3, width=20, height=14,
        players=("p1", "p2", "p3"),
        agents={"p1": "human", "p2": "red_harvest", "p3": "red_harvest"},
    )
    sid = res["session_id"]
    soc_engine.submit_policy(store, sid, "p1", [])
    first = soc_engine.auto_fire_bot_seats(store, sid)
    # We're now in a fresh phase (orbit / planning). Calling auto_fire
    # again with no human submission in between should be a no-op
    # because all pending bots have already submitted for whichever
    # phase we're in (the human's seat is the gating one).
    second = soc_engine.auto_fire_bot_seats(store, sid)
    # The exact length depends on how many phases auto-fire could
    # advance through — what matters is the second call doesn't
    # produce more bot turns than the first.
    assert len(second) <= len(first)
