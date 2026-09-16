"""R2.4 — a night that banks NOTHING must be recorded as a night that banked nothing.

Anchored on `V12_HEUR3_GO_s69` day 4 (session `4d63cf63…`): the seat dropped on a
visible pure at (16,6), auto-harvested it, and was hit by a rival harvester
stepping onto the same cell in the same hour. Engine truth was
``banked 0 parcel(s) (DAMAGED)`` and ``hoard still 1/15``; the journal recorded
``banked red +765 (1 parcel(s))`` and the next night's reflection opened "executed
perfectly … no failures; clean execution".

The cause was a guard that inverted: an empty hoard delta was read as "no data"
and the caller fell through to the view's harvest-time channel, which reports at
pickup rather than at berth. Closing a false zero made a TRUE zero unrepresentable.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import last_night as ln


_PURE = {
    "id": "903c8e1599ab95ea",
    "tile": "RED",
    "purity": 255,
    "from": [16, 6],
}


def _frames(*, seat: str = "p1", dawn_ids: List[str]) -> List[Dict[str, Any]]:
    """Replay frames whose hoard readout DID happen, ending on ``dawn_ids``."""
    def sites(ids: List[str]) -> List[Dict[str, Any]]:
        return [{"id": i, "tile_at_harvest": "RED", "purity_at_harvest": 100} for i in ids]

    return [
        {"tag": "open", "hoard": {seat: {"sites": sites(["carried-over"])}}},
        {"tag": "dawn", "hoard": {seat: {"sites": sites(["carried-over"] + dawn_ids)}}},
    ]


def _view_with_harvest() -> Dict[str, Any]:
    """The view reports the pure on its harvest-time channel — it was picked up."""
    return {"last_night": {"my_parcels_banked": [_PURE]}}


# ── the bug ──────────────────────────────────────────────────────────────────
def test_an_empty_hoard_delta_is_a_real_zero_not_missing_data() -> None:
    """Harvested the pure, banked nothing: YIELD must read zero."""
    frames = _frames(dawn_ids=[])          # hoard did not grow
    act = ln.actual_yield(_view_with_harvest(), frames, "p1")
    assert act["parcels"] == 0
    assert act["red_pts"] == 0


def test_the_loss_is_named_rather_than_silently_dropped() -> None:
    """Zeroing the yield is only half of it — the card has to say what was lost."""
    frames = _frames(dawn_ids=[])
    lost = ln.lost_in_transit(_view_with_harvest(), frames, "p1")
    assert lost["parcels"] == 1
    assert lost["red_pts"] == 765          # v1.13: 255 x 3.0, no transit charge


def test_the_rendered_block_refuses_to_call_lost_cargo_yield() -> None:
    memory = {
        "day": 4,
        "orders": [],
        "execution_log": [],
        "actual": ln.actual_yield(_view_with_harvest(), _frames(dawn_ids=[]), "p1"),
        "lost": ln.lost_in_transit(_view_with_harvest(), _frames(dawn_ids=[]), "p1"),
        "expected": {"red_pts": 765},
    }
    block = ln.format_block(memory)
    assert "red +0" in block
    assert "LOST IN TRANSIT" in block
    assert "765" in block


# ── the thing the old guard was protecting, which must still work ───────────
def test_a_real_delta_still_wins() -> None:
    frames = _frames(dawn_ids=["fresh-1", "fresh-2"])
    act = ln.actual_yield({"last_night": {}}, frames, "p1")
    assert act["parcels"] == 2


def test_missing_frames_still_fall_back_to_the_view() -> None:
    """The original defect: the hoard grew but the view's filter came back empty.

    With no frames at all there is no readout to trust, so the harvest channel is
    still the best available answer — that path must survive.
    """
    act = ln.actual_yield(_view_with_harvest(), [], "p1")
    assert act["parcels"] == 1
    assert act["red_pts"] == 765


def test_frames_without_a_hoard_readout_are_treated_as_missing() -> None:
    """Frames exist but carry no hoard for this seat — not evidence of a zero."""
    frames = [{"tag": "open"}, {"tag": "dawn", "hoard": {"p2": {"sites": []}}}]
    act = ln.actual_yield(_view_with_harvest(), frames, "p1")
    assert act["parcels"] == 1


def test_no_loss_line_when_everything_banked() -> None:
    frames = _frames(dawn_ids=["903c8e1599ab95ea"])
    assert ln.lost_in_transit(_view_with_harvest(), frames, "p1") == {}
