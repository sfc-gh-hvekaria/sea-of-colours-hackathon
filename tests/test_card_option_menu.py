"""The option menu has to survive the trip onto a card.

Two bugs conspired to hide it, and both are pinned here.

The menu is what the agent was *offered*; the rationale is what it
*said*. Without the first, a card cannot answer the only question worth
asking of a fork — did it choose differently, or was it simply shown
something different? — so it is worth a test of its own.
"""

from __future__ import annotations

import pytest

from sea_of_colours.evals import cards
from sea_of_colours.orchestrator_2 import audit

MENU = (
    'OPTION MENU (SELECT by ID — put the IDs you choose, in execution '
    'order, into "plan"; the geometry is pre-filled for you).\n'
    "  [GRAB1] SMASH the pure (34,19) — LIVE pure (red p255)  · no probe\n"
    "       walk: (34,19) -> (33,19) (33,18)  (3 cells)\n"
    "       yield: red ~+1454 · blue 0 · green 0\n"
    "  [PR2] probe the fog at (37,16)  · 1 probe\n"
)
TASK = "=== YOUR TASK (THINK — reasoning only) ===\nGO\n"


_DOCTRINE = "STANDING DOCTRINE line\n"


def _prompt(doctrine_lines: int = 0) -> str:
    """A prompt shaped like a real one: doctrine, then the menu last.

    Sized off the cap rather than a fixed line count. The count used to
    be hardcoded at 2,200 lines, which overflowed the 32,000-character
    cap of the day and stopped overflowing anything the moment the cap
    was raised — so both truncation tests quietly became tests of a
    prompt that fits, which is not what they are for.
    """
    if not doctrine_lines:
        doctrine_lines = (audit.PROMPT_EXCERPT_CHAR_CAP // len(_DOCTRINE)) + 500
    return (_DOCTRINE * doctrine_lines) + MENU + TASK


# ── bug 1: head-truncation decapitated the menu ─────────────────────

def test_the_excerpt_keeps_the_menu_when_it_has_to_truncate():
    """V12 renders the menu LAST, so a head slice always lost it."""
    full = _prompt()
    assert len(full) > audit.PROMPT_EXCERPT_CHAR_CAP, "test needs an overflow"

    assert "OPTION MENU" not in full[: audit.PROMPT_EXCERPT_CHAR_CAP], (
        "the old head slice should demonstrably lose the menu"
    )
    kept = audit._truncate_prompt(full)
    assert "OPTION MENU" in kept
    assert "[GRAB1]" in kept


def test_truncating_does_not_exceed_the_column_budget():
    """Keeping both ends must not cost more room than the head did."""
    kept = audit._truncate_prompt(_prompt(), audit.PROMPT_EXCERPT_CHAR_CAP)
    assert len(kept) <= audit.PROMPT_EXCERPT_CHAR_CAP


def test_a_short_prompt_is_left_exactly_alone():
    short = MENU + TASK
    assert audit._truncate_prompt(short) == short


def test_the_elision_says_it_happened():
    """A silent gap in the middle would read as the agent's own text."""
    kept = audit._truncate_prompt(_prompt())
    assert "CHARACTERS CUT" in kept
    assert "NOT the whole prompt" in kept


def test_a_real_prompt_is_stored_whole():
    """The cap exists for runaway forks, not for V12.

    At 32,000 characters it was cutting roughly 80KB out of the middle
    of every single card — a card that cannot show the prompt cannot
    answer the one question it is for.
    """
    real = "x" * 115_000  # a measured V12 think prompt
    assert audit._truncate_prompt(real) == real
    assert audit.PROMPT_EXCERPT_CHAR_CAP > 2 * len(real), (
        "the cap should sit well clear of a real prompt, not just above it"
    )


# ── bug 2: extras never exist on a card rebuilt from the database ───

def test_the_menu_is_recovered_from_a_stored_prompt():
    """``extras`` only exists on a live envelope.

    The invocation row has no column for it, so "Options offered" was
    empty for every persisted season. The menu is in the prompt, which
    IS stored, so it gets carved back out.
    """
    got = cards.menu_from_prompt(_prompt())
    assert got.startswith("OPTION MENU")
    assert "[GRAB1]" in got
    assert "YOUR TASK" not in got, "the menu must stop at the next block"


def test_recovery_is_honest_when_there_is_no_menu():
    """A heuristic seat has no menu, and inventing one would be worse."""
    assert cards.menu_from_prompt("just some prose") == ""
    assert cards.menu_from_prompt("") == ""


def test_a_card_from_stored_rows_carries_the_menu():
    card = cards.normalise({"prompt_excerpt": _prompt(), "player": "p1", "day": 2})
    assert "[GRAB1]" in card["extras"]["option_menu_block"]


@pytest.fixture
def three_pass_card():
    """A card folded from the pass rows a real V12 turn leaves behind."""
    rows = [
        {
            "session_id": "s", "day": 2, "player": "p1",
            "agent_id": "TABULA_V12_THINK",
            "prompt_excerpt": audit._truncate_prompt(_prompt()),
            "response_text": "I will smash the pure.",
            "rationale": "[think pass — bounded reasoning]",
            "ms_elapsed": 900, "status": "ok",
        },
        {
            "session_id": "s", "day": 2, "player": "p1",
            "agent_id": "TABULA_V12",
            "prompt_excerpt": "short packager prompt, no menu here",
            "response_text": '{"plan":["GRAB1"]}',
            "rationale": "plan=[GRAB1]",
            "ms_elapsed": 700, "status": "ok",
        },
    ]
    return cards.normalise_group(
        cards.group_rows(rows)[0], season="T", session_id="s",
    )


def test_the_menu_is_found_on_whichever_pass_carried_it(three_pass_card):
    """The packager runs last and usually has no menu.

    ``normalise_group`` normalises the LAST row, so searching only that
    one would miss a menu that lived on the think pass.
    """
    assert "[GRAB1]" in three_pass_card["extras"]["option_menu_block"]


# ── and it reaches both renderers ───────────────────────────────────

def test_both_formats_show_the_menu(three_pass_card):
    """HTML to read, Markdown to paste into a coding agent.

    Both come off the same normalised card, so a fix that only reached
    one of them would be half a fix.
    """
    card = three_pass_card

    assert "## Options offered" in cards.render(card)

    titles = [s["title"] for s in cards._html_sections(card)]
    assert "Options offered" in titles
    assert "Options offered" in cards.render_html([card], title="t")
