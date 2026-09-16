"""R2.3 — the orbital blue request reads the right bands and stops shouting.

The shared wishlist tests the vault against a band called ``"empty"``. The engine
grades summed purity as ``none / low / medium / high``
(`session.STATION_PURITY_BANDS`), so the test never matched an empty vault and
always matched a vault holding 80 — the request fired backwards. It then arrived
under "ORBIT DIRECTIVES (act on these tonight)" as an imperative, level with a
live redsign, which is how `V12_HEUR3_GO_s69` night 3 came to spend its first
harvester on blue.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import prompt as pr
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.orbit_wishlist import (
    Wishlist,
)

_MENU_WITH_BLUE = "HIGH-YIELD BLUE GRABS — ids BL* (rich blue …)\n  BL1: drop (36,20)"
_MENU_NO_BLUE = "PRIORITY RED GRABS — ids GRAB*\n  GRAB1: drop (20,20)"


def _view(
    *, grade: str, harvesters: int = 2, blue: bool = True, redsign: bool = False,
) -> Dict[str, Any]:
    return {
        "station_intel": {"self": {"blue": {"grade": grade}}},
        "blue_tiles": ([{"x": 36, "y": 20, "purity": 255}] if blue else []),
        "my_assets": [
            {"kind": "harvester", "state": "orbit", "id": f"harvester_p1_{i}"}
            for i in range(harvesters)
        ],
        "redsign": ([{"center": [16, 6], "mine": True}] if redsign else []),
    }


# ── the bands ───────────────────────────────────────────────────────────────
def test_an_empty_vault_is_the_one_that_asks():
    assert pr.blue_vault_is_short(_view(grade="none"))
    assert pr.blue_is_requested(_view(grade="none"))


def test_a_low_vault_still_asks():
    assert pr.blue_is_requested(_view(grade="low"))


def test_a_medium_vault_does_not():
    """The old test said medium was short enough to claim a harvester."""
    assert not pr.blue_is_requested(_view(grade="medium"))


def test_a_high_vault_does_not():
    assert not pr.blue_is_requested(_view(grade="high"))


def test_the_band_the_engine_never_emits_is_not_special_cased():
    assert not pr.blue_is_requested(_view(grade="empty"))


# ── the gates ───────────────────────────────────────────────────────────────
def test_no_request_without_a_spare_unit():
    assert not pr.blue_is_requested(_view(grade="none", harvesters=1))


def test_no_request_without_blue_on_the_board():
    assert not pr.blue_is_requested(_view(grade="none", blue=False))


# ── the framing ─────────────────────────────────────────────────────────────
def _block(view: Dict[str, Any], menu: str, directives: List[str]) -> str:
    wl = Wishlist()
    wl.directives = list(directives)
    return pr.format_orbit_directives_block(wl, view, menu)


def test_the_inherited_imperative_is_dropped():
    block = _block(
        _view(grade="medium"), _MENU_NO_BLUE,
        ["NEED BLUE: your blue vault is low and blue is reachable — send a "
         "harvester to blue tonight unless a pressing RED chain outranks it."],
    )
    assert "NEED BLUE" not in block
    assert "send a harvester to blue tonight" not in block


def test_nothing_is_said_when_the_menu_has_no_blue_to_take():
    assert _block(_view(grade="none"), _MENU_NO_BLUE, []) == ""


def test_the_replacement_reads_as_a_condition_not_an_order():
    block = _block(_view(grade="none"), _MENU_WITH_BLUE, [])
    assert "BLUE VAULT NONE" in block
    assert "background condition, NOT a call to action" in block


def test_a_live_redsign_is_named_as_outranking_it():
    block = _block(_view(grade="low", redsign=True), _MENU_WITH_BLUE, [])
    assert "outranks blue outright" in block


def test_other_directives_pass_through_untouched():
    block = _block(
        _view(grade="high"), _MENU_NO_BLUE,
        ["VAULT NEARLY FULL: ship on the next orbit turn."],
    )
    assert "VAULT NEARLY FULL" in block
