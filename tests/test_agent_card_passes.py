"""A turn is one card, however many passes it took to play it.

V12 and its forks write one ``SOC_AGENT_INVOCATION`` row per pass —
bounded reasoning, then a structured decision, then the deterministic
packager. Rendered a row per card (which is what the server did until
v1.41) the same turn appears three times in the rail, identically
labelled, with the reasoning on one card and the option ids it chose on
the next.

These pin the fold: the grouping rule, what survives it, and the fact
that the sections a reader wants are actually on the card.
"""

from __future__ import annotations

import json

from sea_of_colours.evals import cards


def _row(agent, seq, *, day=1, player="p1", reply="", prompt="", rationale=""):
    return {
        "session_id": "s1", "day": day, "player": player, "seq": seq,
        "agent_id": agent, "response_text": reply, "prompt_excerpt": prompt,
        "rationale": rationale, "status": "ok", "ms_elapsed": 100,
    }


def _night(agent="TABULA_V12", day=1, player="p1", start=1):
    """The three rows one LLM night leaves behind."""
    return [
        _row(f"{agent}_THINK", start, day=day, player=player,
             reply="# THINK PASS\n\n**Reflection:** none yet.",
             prompt="GOAL: bank RED.", rationale="[think pass]"),
        _row(f"{agent}_THINKER", start + 1, day=day, player=player,
             reply='{"posture": "aggressive", "plan": ["HD1L", "PR1"]}',
             prompt="GOAL: bank RED.", rationale="[think pass]"),
        _row(agent, start + 2, day=day, player=player,
             reply="[deterministic packager — recipe compiled]",
             prompt="GOAL: bank RED.",
             rationale="[plan=aggressive: HD1L, PR1] [fallback=False]"),
    ]


def test_three_pass_rows_fold_into_one_turn():
    groups = cards.group_rows(_night())
    assert len(groups) == 1
    assert [r["_pass"] for r in groups[0]] == [
        "Reasoning", "Decision", "Packager",
    ]


def test_the_packager_closes_a_turn_so_orbit_and_night_stay_apart():
    # An orbit turn has no LLM passes at all — just the packager. Folded
    # on "same day, same seat" it would swallow the night that follows.
    rows = [_row("TABULA_V12", 1, reply="[orbit]")] + _night(start=2)
    groups = cards.group_rows(rows)
    assert len(groups) == 2
    assert len(groups[0]) == 1 and len(groups[1]) == 3


def test_a_turn_that_never_reached_the_packager_is_still_a_card():
    # The model spoke and something threw before the packager ran. That
    # is exactly the turn someone is opening the card to look at.
    groups = cards.group_rows(_night()[:2])
    assert len(groups) == 1 and len(groups[0]) == 2


def test_the_card_carries_the_thinking_and_the_choice():
    card = cards.normalise_group(cards.group_rows(_night())[0])
    assert [p["label"] for p in card["passes"]] == ["Reasoning", "Decision"]
    # The packager's fixed note is not a pass anyone wants to read.
    assert all("deterministic packager" not in p["body"]
               for p in card["passes"])
    assert card["plan_ids"] == ["HD1L", "PR1"]
    assert card["agent"] == "TABULA_V12"


def test_the_rationale_skips_the_pass_markers():
    card = cards.normalise_group(cards.group_rows(_night())[0])
    assert card["rationale"].startswith("[plan=aggressive")


def test_the_decision_json_is_reindented_but_never_swallowed():
    good = cards.normalise_group(cards.group_rows(_night())[0])
    decision = next(p for p in good["passes"] if p["syntax"] == "json")
    assert decision["body"].count("\n") > 0, "one-line JSON is unreadable"

    rows = _night()
    rows[1]["response_text"] = '{"posture": "aggressive", "plan": ['  # truncated
    broken = cards.normalise_group(cards.group_rows(rows)[0])
    kept = next(p for p in broken["passes"] if p["syntax"] == "json")
    assert kept["body"] == rows[1]["response_text"], (
        "a malformed decision is the most interesting thing on the card"
    )


def test_html_sections_are_ordered_and_typed():
    card = cards.normalise_group(cards.group_rows(_night())[0])
    secs = cards._html_sections(card)
    titles = [s["title"] for s in secs]
    assert titles.index("Reasoning") < titles.index("Decision")
    assert titles[-1].startswith("Prompt"), "the prompt goes last"
    kinds = {s["title"]: s["syntax"] for s in secs}
    assert kinds["Decision"] == "json"
    assert kinds["Reasoning"] == "md"
    assert all(s["lines"] >= 1 for s in secs)


def test_identical_prompts_are_not_shown_three_times():
    card = cards.normalise_group(cards.group_rows(_night())[0])
    assert len(card["prompts"]) == 1


def test_the_page_embeds_the_turn_and_its_plan():
    card = cards.normalise_group(cards.group_rows(_night())[0])
    page = cards.render_html([card], title="T")
    blob = page.split('type="application/json">')[1].split("</script>")[0]
    data = json.loads(blob.replace("<\\/", "</"))
    assert len(data["turns"]) == 1
    assert data["turns"][0]["plan"] == ["HD1L", "PR1"]
    # A fetch cannot work over file://, which is how these are opened.
    assert "fetch(" not in page


def test_markdown_still_carries_every_pass():
    # Markdown is what an attendee pastes into a coding agent, so the
    # fold must not quietly drop the reasoning from it.
    card = cards.normalise_group(cards.group_rows(_night())[0])
    md = cards.render(card)
    assert "## Reasoning" in md and "## Decision" in md
    assert "## Plan chosen" in md and "HD1L" in md
    assert "THINK PASS" in md
