"""v0.9.2 — fog-of-war drop rule + invariants.

* Harvester drops require the landing cell to be live OR echo
  (RULEBOOK §3.10 update).
* GREEN parcels banked into the hoard are always purity 255
  (no fractional green).
* Blue purity readouts (and weapon debits) honour the canonical
  ``purity_at_harvest`` key on hoard parcels.

These tests bypass the legacy ``conftest`` monkey-patch on
``try_drop_unit`` because they exercise the *new* rule directly.
"""

from __future__ import annotations

import pytest

from sea_of_colours.game import session as _session_module
from sea_of_colours.game.session import (
    GameSession,
    PLAYERS,
    Phase,
    _xy_key,
)
from sea_of_colours.generator import Tile


@pytest.fixture(autouse=True)
def _use_echo_drop_mode_for_legacy_tests(monkeypatch):
    """test_v092_landings.py tests v0.9.2 fog-of-war drop rules.
    
    v0.9.2 introduced live_or_echo mode (the "new" rule at that time).
    v0.9.17 changed the canonical default to live_only. These tests
    specifically validate the v0.9.2 behavior, so we restore live_or_echo."""
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")


# Pull the unpatched ``try_drop_unit`` off the GameSession class — the
# conftest stashes it as ``_v092_orig_try_drop`` BEFORE installing
# the legacy fog-stamping shim, so this is the real engine method.
_real_try_drop = _session_module.GameSession._v092_orig_try_drop  # type: ignore[attr-defined]


def _fresh(width: int = 20, height: int = 14, seed: int = 7) -> GameSession:
    sess = GameSession.new(width, height, seed=seed)
    # Clear any conftest-stamped fog and parked-orbit state so the
    # drop rule applies cleanly.
    sess.memory_tiles = {p: {} for p in PLAYERS}
    sess.probe_intel = {p: {} for p in PLAYERS}
    # v0.9.x — zero the 250 starting BLUE bank so the blue-parcel debit
    # tests measure only the stamped parcels.
    for p in sess.players:
        sess.blue_bank[p] = 0
    sess.phase = Phase.PLANNING
    return sess


# ── §3.10 drop rule ─────────────────────────────────────────────────


def test_drop_into_fog_is_rejected() -> None:
    """A pure-fog cell (no live LOS, no probe echo, no memory) refuses
    the drop with a yellow-log reason."""
    sess = _fresh()
    ok, msg, _ = _real_try_drop(sess, "p1", "harvester_p1", 5, 5)
    assert not ok, f"fog-cell drop should fail; got msg={msg!r}"
    assert "fog" in msg.lower()
    assert "live or own-echo" in msg.lower()


def test_drop_into_echo_tile_is_allowed() -> None:
    """A cell with a probe-echo snapshot (visited in the past) is a
    legal landing site even though no harvester / probe is currently
    looking at it."""
    sess = _fresh()
    sess.probe_intel["p1"][_xy_key(8, 6)] = {
        "tile": int(Tile.RED), "purity": 120, "day": 1,
    }
    ok, msg, _ = _real_try_drop(sess, "p1", "harvester_p1", 8, 6)
    assert ok, f"echo cell should land; got: {msg}"


def test_drop_into_live_los_is_allowed() -> None:
    """Drop a probe on (10, 7), which seeds memory_tiles via the
    real ``try_probe`` path; the subsequent harvester drop is
    permitted because (10, 7) now sits inside the probe's vision
    disk."""
    sess = _fresh()
    # Mint a probe via the live engine path so its vision disk gets
    # rolled into tiles_visible_now (and memory_tiles).
    ok, _msg = sess.spawn_probe("p1", 10, 7)
    assert ok
    # Confirm live LOS now covers (10, 7).
    assert (10, 7) in sess.tiles_visible_now("p1")
    ok, msg, _ = _real_try_drop(sess, "p1", "harvester_p1", 10, 7)
    assert ok, f"live LOS cell should land; got: {msg}"


def test_drop_into_memory_only_tile_is_allowed() -> None:
    """A cell with a memory_tiles entry (revealed at some point but
    no longer in current LOS) is also a legal landing site — the
    rule's wording is ``live OR echo`` and ``memory_tiles`` carries
    the same "previously revealed" signal as probe_intel."""
    sess = _fresh()
    sess.memory_tiles["p1"][_xy_key(3, 4)] = {
        "paint": {}, "stale": True,
    }
    ok, msg, _ = _real_try_drop(sess, "p1", "harvester_p1", 3, 4)
    assert ok, f"memory cell should land; got: {msg}"


# ── GREEN purity invariant ──────────────────────────────────────────


def test_green_pickup_banks_at_255_even_if_tile_was_lower() -> None:
    """Even if a scripted scenario seeds GREEN at a fractional purity,
    the harvester's parcel must store ``purity_at_harvest=255`` so the
    vault invariant "every green is 255" holds."""
    from sea_of_colours.render import Cell
    sess = _fresh()
    sess.grid[6][9] = Cell(Tile.GREEN, 87)  # fractional green seed
    # Stamp memory so drop rule passes.
    sess.memory_tiles["p1"][_xy_key(9, 6)] = {"paint": {}, "stale": True}
    ok, _msg, _h = _real_try_drop(sess, "p1", "harvester_p1", 9, 6)
    assert ok
    harv = sess.entities["harvester_p1"]
    # The cargo parcel should be a GREEN at 255, not 87.
    greens = [
        c for c in harv.cargo_squares
        if int(c.get("tile_at_harvest", 0)) == int(Tile.GREEN)
    ]
    assert greens, "harvester should have banked the GREEN tile"
    for g in greens:
        assert int(g["purity_at_harvest"]) == 255, (
            f"green parcel must be 255, got {g['purity_at_harvest']}"
        )


# ── Blue purity bug regression (v0.9.2) ─────────────────────────────


def test_blue_purity_available_reads_purity_at_harvest() -> None:
    """The v0.9 weapons readout had a bug — it read ``r['purity']``
    instead of ``r['purity_at_harvest']``, so every blue parcel
    in the vault reported 0 purity and no weapon was affordable.
    This test pins the fix: ``blue_purity_available`` sums the
    canonical key."""
    sess = _fresh()
    sess.hoard_squares["p1"] = [
        {
            "square_id": "b-1",
            "site_id": "b-1",
            "tile_at_harvest": int(Tile.BLUE),
            "purity_at_harvest": 80,
            "origin_purity": 80,
        },
        {
            "square_id": "b-2",
            "site_id": "b-2",
            "tile_at_harvest": int(Tile.BLUE),
            "purity_at_harvest": 130,
            "origin_purity": 130,
        },
    ]
    assert sess.blue_purity_available("p1") == 210


def test_debit_blue_purity_consumes_lowest_first() -> None:
    """A 100-purity debit should burn the 30-purity parcel whole and
okay h    then the 90-purity parcel whole. Pre-v0.9.5 the overpay (20p)
    was wasted; v0.9.5 refunds it as a residual BLUE parcel so the
    vault only loses the actual cost."""
    sess = _fresh()
    sess.hoard_squares["p1"] = [
        {"square_id": f"b-{i}", "site_id": f"b-{i}",
         "tile_at_harvest": int(Tile.BLUE),
         "purity_at_harvest": p, "origin_purity": p}
        for i, p in enumerate([90, 30, 200])
    ]
    ok, consumed_ids, waste = sess.debit_blue_purity("p1", 100)
    assert ok
    assert "b-1" in consumed_ids  # the 30-purity parcel
    assert "b-0" in consumed_ids  # the 90-purity parcel
    assert "b-2" not in consumed_ids  # 200 stays untouched
    assert waste == 0  # v0.9.5 — overpay refunded, never burned
    # The 20p leftover from the 90-purity parcel is minted back as
    # a residual BLUE parcel so the user only loses 100, not 120.
    residuals = [
        r for r in sess.hoard_squares["p1"]
        if str(r.get("lineage", "")) == "blue_residual"
    ]
    assert len(residuals) == 1
    assert int(residuals[0]["purity_at_harvest"]) == 20
