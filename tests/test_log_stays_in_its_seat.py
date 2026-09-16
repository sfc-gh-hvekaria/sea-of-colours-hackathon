"""The engine's night log is one shared feed. A seat gets its slice.

Issue 45. ``store.list_log`` takes no seat and log rows carry no owner
column, so ``get_view`` was handing every agent the raw twenty-row
window: the rival's exact drop coordinates, which §3.15 makes private,
and — on a bot seat — the rival's whole rationale line, which is its
plan in prose.

Nothing shipped read it, which is why it survived this long. But
``recent_log`` is a documented key on the agent view, a fork is a
directory anyone can write, and "the percept handed it to me" is not an
argument we want to be adjudicating during a hackathon.

The fix redacts by *mention*: a row naming another seat does not go out,
whoever the row is about. These tests pin the two halves that make that
safe — that rival rows really are gone, and that the lawful channels
which read the *unfiltered* feed upstream (``last_night.my_orders``,
``competitor_intel``) were not starved by the redaction.
"""

from __future__ import annotations

from sea_of_colours.game.session import GameSession
from sea_of_colours.snowpark.view import build_agent_view


def _fresh() -> GameSession:
    return GameSession.new(20, 14, seed=7)


def _rows(*texts: str, day: int = 1) -> list[dict]:
    return [
        {"day": day, "seq": i, "level": "info", "text": t}
        for i, t in enumerate(texts)
    ]


def _log_of(sess: GameSession, seat: str, rows: list[dict]) -> list[str]:
    view = build_agent_view(sess, seat, recent_log=rows)
    return [str(r.get("text")) for r in view["recent_log"]]


def test_a_rivals_night_does_not_arrive_in_your_percept():
    """The breach itself: coordinates §3.15 calls private."""
    sess = _fresh()
    rows = _rows(
        "p1 deployed harvester_p1 at (4,4)",
        "p2 dropped harvester_p2 at (9,6)",
        "[H03] p2 deployed probe_p2_5 at (12,2)",
    )
    got = _log_of(sess, "p1", rows)
    assert got == ["p1 deployed harvester_p1 at (4,4)"], got


def test_a_bots_reasoning_is_not_published_to_the_seat_playing_against_it():
    """Worse than coordinates. The heuristic writes its plan into the
    log in full — target cell, escort probe, the lot — and the shared
    feed put that in the opponent's percept a turn before it happened."""
    sess = _fresh()
    plan = (
        "[RED_HARVEST] [heuristic] (p2) harvester_p2 HOT-DROP: "
        "secure probe@[11, 1] + drop@[11, 2]->RED@[11, 1]"
    )
    assert _log_of(sess, "p1", _rows(plan)) == []
    assert _log_of(sess, "p2", _rows(plan)) == [plan]


def test_the_clock_belongs_to_everybody():
    """Rows naming no seat are phase and settlement bookkeeping. Fogging
    those would leave an agent unable to tell what day it is from its own
    log, which is a cure worse than the disease."""
    sess = _fresh()
    rows = _rows(
        "[orbit] day 5: +1000 credits awarded to every seat.",
        "[dawnComplete] day 5 opens in ORBIT phase",
    )
    assert _log_of(sess, "p1", rows) == [r["text"] for r in rows]


def test_a_row_about_both_of_you_is_treated_as_theirs():
    """Redaction is by mention, not by subject, so a mixed row goes.

    Deliberate: parsing the subject out of free text is the thing that
    was already being done crudely upstream, and getting it wrong here
    fails open. The seat loses one line about its own expired probe and
    learns the same fact from ``my_assets``.
    """
    sess = _fresh()
    mixed = "probe lifetime (3 nights) reached — expired probe_p1_3, probe_p2_5"
    assert _log_of(sess, "p1", _rows(mixed)) == []


def test_renaming_a_seat_does_not_unredact_it():
    """Seasons name their seats, and a season somebody named is exactly
    the one being watched. Matching the slug alone would leak the moment
    the log used the display name instead."""
    sess = _fresh()
    sess.player_profiles["p2"] = {
        "display_name": "Alba_Cipher", "tag": "ALB", "color": "#FFFFFF",
    }
    rows = _rows(
        "Alba_Cipher shipped 3 RED parcel(s)",
        "ALB banked 2 parcels",
        "p1 shipped 1 RED parcel(s)",
    )
    assert _log_of(sess, "p1", rows) == ["p1 shipped 1 RED parcel(s)"]


def test_redacting_the_feed_did_not_blind_the_channels_that_read_it():
    """The two blocks downstream of the raw feed are entitled to it —
    ``last_night`` filters it to this seat itself, and ``competitor_intel``
    lifts only ``probe_launch``, which §3.14 makes a public flare. So the
    filter had to land on the emitted key and nowhere else. If someone
    ever "tidies" it upstream, a seat stops seeing rival launches it is
    lawfully entitled to and this goes red.
    """
    sess = _fresh()
    sess.day = 2  # day_ended = 1, which is what both blocks read
    rows = [
        {"day": 1, "seq": 0, "level": "info",
         "text": "p1 deployed probe_p1_1 at (10,7)"},
        {"day": 1, "seq": 1, "level": "error",
         "text": "p1 step refused: harvester_p1 has already made its outing"},
        {"day": 1, "seq": 2, "level": "info", "kind": "probe_launch",
         "text": "p2 launched probe_p2_9 at (14,9) — visible from orbit",
         "data": {"owner": "p2", "probe_id": "probe_p2_9", "at": [14, 9],
                  "landed_on_tile": "EMPTY", "landed_purity": 0, "day": 1}},
    ]
    view = build_agent_view(sess, "p1", recent_log=rows)

    assert [r["text"] for r in view["recent_log"]] == [
        rows[0]["text"], rows[1]["text"],
    ]
    orders = view["last_night"]["my_orders"]
    assert [o["outcome"] for o in orders] == ["ok", "illegal"], orders
    launches = [
        r for r in view["competitor_intel"]["new_this_day"]
        if r["kind"] == "enemy_probe_launch"
    ]
    assert launches and launches[0]["at"] == [14, 9], (
        "a rival probe launch is public (§3.14) and must survive the "
        "redaction of the feed it was read from"
    )
