"""Unit tests for the v12 BOARD PASS + the narrowed source-of-truth clamp.

v11 declared the OPTION MENU the SINGLE source of truth, which worked — a scan
of 28 turns found zero invented coordinates — but it also meant the agent acted
on nothing else, ignoring the ~66 vein-or-better cells per turn that WORLD VIEW
carries and no option surfaces.

v12 narrows the clamp instead of dropping it: the menu stays authoritative for
LEGAL geometry (picks are IDs, never composed coordinates) while step 6 of the
PER-TURN PROCEDURE spends the last of the agent's attention reconciling those
picks against WORLD VIEW / OUT-OF-GRID. These tests pin BOTH halves, because
losing either one is a regression: drop the clamp and hallucination returns,
drop the pass and the board blocks go back to being decorative.
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import agency, doctrine


def _view():
    return {
        "world": {"width": 40, "height": 28, "live": [], "echo": []},
        "entities": {"mine": []},
        "red_tiles": [],
        "my_assets": [],
    }


def _menu_header():
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12.agency import Option

    reg = {"X1": Option(
        option_id="X1", kind="chain", title="t", detail="d",
        execute_lines=["X1: do a thing"], payload={},
    )}
    return agency.format_menu_block(reg, agent_view=_view())


# ── the clamp is narrowed, not removed ──────────────────────────────────
def test_menu_no_longer_claims_to_be_the_single_source_of_truth():
    assert "SINGLE source of truth" not in _menu_header()


def test_menu_still_forbids_composing_coordinates():
    text = _menu_header()
    assert "never coordinates you compose" in text


def test_menu_still_says_geometry_is_pre_validated():
    assert "PRE-VALIDATED" in _menu_header()


def test_menu_points_at_the_two_board_blocks():
    text = _menu_header()
    assert "WORLD VIEW" in text
    assert "OUT-OF-GRID" in text


def test_menu_frames_the_board_blocks_as_choosing_not_authoring():
    assert "not to invent new ones" in _menu_header()


# ── the board pass exists and sits at the END of the pyramid ────────────
def test_board_pass_is_step_six():
    assert "6. BOARD PASS" in doctrine.STRATEGIES_CORE


def test_board_pass_follows_the_safety_pass():
    core = doctrine.STRATEGIES_CORE
    assert core.index("5. SAFETY PASS") < core.index("6. BOARD PASS")


def test_board_pass_asks_for_full_asset_use():
    assert "Is every harvester out" in doctrine.STRATEGIES_CORE


def test_board_pass_asks_about_untrimmed_trace_tails():
    assert "padded with trace" in doctrine.STRATEGIES_CORE


def test_board_pass_names_pure_denial_and_contest_as_deciders():
    core = doctrine.STRATEGIES_CORE
    assert "contesting" in core and "decide seasons" in core
    assert "Never bank trace while a pure" in core


def test_board_pass_permits_reranking_but_not_invention():
    core = doctrine.STRATEGIES_CORE
    assert "re-rank, drop, swap or shorten" in core
    assert "NOT invent a drop cell" in core


def test_step_one_no_longer_calls_the_menu_the_single_source_of_truth():
    assert "single source\n     of truth" not in doctrine.STRATEGIES_CORE
    assert "single source of truth" not in doctrine.STRATEGIES_CORE


def test_step_one_still_requires_ids_not_coordinates():
    assert "never coordinates you\n     compose yourself" in doctrine.STRATEGIES_CORE


# ── and it all reaches the prompt ───────────────────────────────────────
def test_board_pass_renders_in_the_thinker_prompt():
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import (
        prompt as prompt_mod,
    )

    text = prompt_mod.build_prompt(
        mode="thinker", agent_view=_view(), day=3, day_cap=7, vault_score=0,
        memory_replay="(none)\n", chain_hints=[],
    )
    assert "6. BOARD PASS" in text
    assert "SINGLE source of truth" not in text
