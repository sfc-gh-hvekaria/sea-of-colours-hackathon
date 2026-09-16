"""An empty journal is not the same claim as an empty game.

The STRATEGY JOURNAL block used to open with "no prior nights — this is
your first turn" whenever it had no entries, regardless of the day. On
day 1 that is true. On day 6 it is simply false, and it is the kind of
false that changes play: an agent told it has just arrived opens the
game — spreads probes, holds the harvester back, banks nothing — when
what it is actually being asked for is a sixth night on a board with a
spent seam and a rival who is ahead.

It is not a lab-only problem. Any V12 seat whose memory rows are
missing gets the same sentence in a live season.
"""

from __future__ import annotations

import pytest

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import journal


def test_day_one_with_no_journal_still_says_first_turn():
    out = journal.render_journal([], 1)
    assert "first turn" in out
    assert "night 1" not in out


def test_a_later_night_with_no_journal_does_not_claim_to_be_the_first():
    out = journal.render_journal([], 6)
    assert "is your first turn" not in out, (
        "told an agent on night 6 that it had just arrived"
    )
    assert "night 6" in out
    assert "5 night(s)" in out, "should say how much history it is missing"


def test_the_day_is_optional_and_defaults_to_the_old_wording():
    """Callers that cannot supply a day must not crash or lie loudly."""
    out = journal.render_journal([])
    assert "first turn" in out


def test_a_real_journal_is_unaffected_by_the_day():
    entries = [{"day": 1, "intent": "probe wide", "actual_banked": 3}]
    with_day = journal.render_journal(entries, 4)
    without = journal.render_journal(entries)
    assert with_day == without
    assert "probe wide" in with_day
    assert "first turn" not in with_day


@pytest.mark.parametrize("fork", ["tabula_v12", "emp_harvest_test"])
def test_every_fork_inherits_the_fix(fork):
    """journal.py is copied wholesale into a fork, so the fix has to be
    in each copy or a fork silently keeps the old lie."""
    mod = __import__(
        f"sea_of_colours.orchestrator_2.harnesses.{fork}.journal",
        fromlist=["_"],
    )
    assert "is your first turn" not in mod.render_journal([], 6)


@pytest.mark.parametrize("fork", ["tabula_v12", "emp_harvest_test"])
def test_the_harness_actually_passes_the_day_in(fork):
    """The wording is only worth anything if the day reaches it."""
    import inspect

    src = inspect.getsource(__import__(
        f"sea_of_colours.orchestrator_2.harnesses.{fork}.harness",
        fromlist=["_"],
    ))
    assert "render_journal(prior_entries, day)" in src, (
        f"{fork} calls render_journal without a day, so an empty journal "
        "reverts to claiming it is night one"
    )
