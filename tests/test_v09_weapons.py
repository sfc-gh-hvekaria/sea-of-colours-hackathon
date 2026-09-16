"""v0.9 — Weapons + WAIT command unit tests.

Each test exercises one mechanic from RULEBOOK §5 in isolation. The
conftest in :file:`tests/conftest.py` auto-collapses the dawn ORBIT
phase for the legacy test suite, so tests in this module stay
focused on night-phase behaviour. Where a test needs to assert
session state on the catapult / orbit side, it builds the session
fresh and drives ``NightSimulator.run`` against a pre-stamped vault.

All numeric expectations dereference :mod:`sea_of_colours.game.weapons`
constants so balance tweaks (e.g. dropping ``EMP_COST_BLUE_PURITY``
to 150) don't break the suite.
"""

from __future__ import annotations

from conftest import banked_parcels

from typing import Any, Dict, List

from sea_of_colours.game.policy import (
    ChaffFlareMove,
    DropMove,
    EmpLaunchMove,
    OrbitWasteAction,
    PickupMove,
    ProbeMove,
    StepMove,
    WaitMove,
    WasteMove,
    parse_moves,
    parse_orbit_actions,
)
from sea_of_colours.game.session import (
    GameSession,
    PLAYERS,
    Phase,
    _MINE_REFUND_BLUE_EACH,
)
from sea_of_colours.game.simulator import NightSimulator
from sea_of_colours.game.weapons import (
    CHAFF_COST_BLUE_PURITY,
    CHAFF_COST_CREDITS,
    CHAFF_DURATION_HOURS,
    EMP_CLOUD_HOURS,
    EMP_COST_BLUE_PURITY,
    EMP_COST_CREDITS,
    EMP_MISSILES_PER_LAUNCH,
    EMP_RADIUS,
)
from sea_of_colours.game.session import Entity
from sea_of_colours.generator import Cell, Tile


# ── Helpers ─────────────────────────────────────────────────────────


def _stamp_blue_parcels(
    sess: GameSession,
    owner: str,
    purities: List[int],
) -> None:
    """Append blue hoard parcels for ``owner`` at the given purities.

    Carries ``x``/``y`` synthetic coords (the live engine writes
    these from the harvest site) so the v0.7.x replay's hoard
    snapshot has a cell to render.
    """
    extra = [
        {
            "square_id": f"b-{owner}-{i}",
            "site_id": f"b-{owner}-{i}",
            "tile_at_harvest": int(Tile.BLUE),
            "purity_at_harvest": int(p),
            "purity": int(p),
            "origin_purity": int(p),
            "origin_tile": int(Tile.BLUE),
            "lineage": "natural",
            "x": 0,
            "y": 0,
        }
        for i, p in enumerate(purities)
    ]
    sess.hoard_squares[owner] = list(sess.hoard_squares[owner]) + extra


def _blue_parcels_worth(total: int, *, cap: int = 255) -> List[int]:
    """Split ``total`` blue into parcels no richer than the purity cap.

    v1.36 — weapon prices are no longer guaranteed to fit in one parcel
    (chaff is 300, purity clamps at 255 per §3.14). Chunking here rather
    than writing two literals keeps these fixtures correct through the
    next retune as well as this one.
    """
    out: List[int] = []
    rest = int(total)
    while rest > cap:
        out.append(cap)
        rest -= cap
    if rest > 0:
        out.append(rest)
    return out


def _fresh_night_session(seed: int = 17) -> GameSession:
    """Spin up a session ready for an explicit PRAXIS run.

    Tests in this module bypass the parser and call
    ``NightSimulator.run`` directly with already-parsed Move lists.
    The conftest's ``_patched_new`` already lands new sessions in
    PLANNING with the per-day credit award applied — perfect.
    """
    sess = GameSession.new(20, 14, seed=seed)
    assert sess.phase == Phase.PLANNING
    # v0.9.x — zero the 250 starting BLUE bank so these unit tests exercise
    # the vault-parcel debit path in isolation (the bank is covered by its
    # own dedicated tests).
    for p in sess.players:
        sess.blue_bank[p] = 0
    return sess


def _stock_weapon(
    sess: GameSession, owner: str, kind: str, n: int = 1,
) -> None:
    """v0.9.3 — directly pre-fill a seat's weapon stockpile.

    Most night-phase tests want to exercise the LAUNCH side of a
    weapon, not the build pipeline; in v0.9.3 launches drain from a
    stockpile filled during the preceding Orbit. Rather than running
    a full orbit phase for every test, this helper bypasses the
    Build*Action plumbing and writes the count straight into
    ``weapon_stock[owner][kind]``. Tests of the build pipeline (see
    the v0.9.3 build-flow section) still use the public
    ``apply_build_*`` methods.
    """
    slot = sess.weapon_stock.setdefault(
        owner, {"emp": 0, "chaff": 0},
    )
    slot[kind] = int(slot.get(kind, 0)) + int(n)


# ── Parser coverage ─────────────────────────────────────────────────


def test_parse_wait_move() -> None:
    moves, errors = parse_moves([{"a": "wait"}])
    assert errors == []
    assert len(moves) == 1
    assert isinstance(moves[0], WaitMove)


def test_parse_emp_launch_move() -> None:
    moves, errors = parse_moves([{"a": "emp_launch", "at": [5, 6]}])
    assert errors == []
    assert isinstance(moves[0], EmpLaunchMove)
    assert moves[0].at == (5, 6)


def test_parse_chaff_flare_move() -> None:
    moves, errors = parse_moves([{"a": "chaff_flare"}])
    assert errors == []
    assert isinstance(moves[0], ChaffFlareMove)


def test_parse_aliases_accept_short_forms() -> None:
    """Short tags (``"emp"`` / ``"chaff"``) are accepted.

    v1.31 — the third short form, ``"mine"``, went with the caltrop; its
    refusal is pinned by the retirement tests below.
    """
    moves, _ = parse_moves([
        {"a": "emp", "at": [1, 1]},
        {"a": "chaff"},
    ])
    assert isinstance(moves[0], EmpLaunchMove)
    assert isinstance(moves[1], ChaffFlareMove)


# ── v1.31 — caltrop MINE retirement ─────────────────────────────────


def test_retired_mine_lay_says_why_rather_than_shrugging() -> None:
    """v1.31 — persisted night queues and stale LLM replies still carry
    ``mine_lay`` (and its ``mine`` short form). They must parse (not
    raise) into a waste marker naming the mechanic that went away, so an
    agent gets a diagnosis it can act on instead of the generic "unknown
    action" shrug. Mirrors the v1.13 ship_catapult retirement in
    :file:`tests/test_orbit_v1.py`.
    """
    raw = [
        {"a": "mine_lay", "at": [10, 4]},
        {"a": "mine", "at": [2, 2]},
        {"a": "mine-lay", "at": [3, 3]},
    ]
    moves, errs = parse_moves(raw)
    assert errs == []
    assert len(moves) == len(raw)
    assert all(isinstance(m, WasteMove) for m in moves)
    assert all("v1.31" in m.reason for m in moves)
    assert all("caltrop" in m.reason for m in moves)


def test_retired_build_mine_says_why_rather_than_shrugging() -> None:
    """v1.31 — the orbit-side mirror: the caltrop's buy button is gone,
    so ``build_mine`` and its aliases waste with a named reason."""
    raw = [
        {"a": "build_mine", "count": 2},
        {"a": "buildmine"},
        {"a": "build_caltrop"},
        {"a": "mine"},
    ]
    actions, errs = parse_orbit_actions(raw)
    assert errs == []
    assert len(actions) == len(raw)
    assert all(isinstance(a, OrbitWasteAction) for a in actions)
    assert all("v1.31" in a.reason for a in actions)
    assert all("caltrop" in a.reason for a in actions)


# ── Blue-purity economy ─────────────────────────────────────────────


def test_blue_purity_available_sums_blue_only() -> None:
    sess = _fresh_night_session()
    _stamp_blue_parcels(sess, "p1", [50, 80, 100])
    # Toss a RED parcel in for noise; must not contribute.
    sess.hoard_squares["p1"].append({
        "tile_at_harvest": int(Tile.RED),
        "purity": 222,
    })
    assert sess.blue_purity_available("p1") == 230


def test_debit_blue_purity_consumes_lowest_first_with_waste() -> None:
    sess = _fresh_night_session()
    _stamp_blue_parcels(sess, "p1", [40, 60, 200])
    ok, consumed, waste = sess.debit_blue_purity("p1", 100)
    assert ok is True
    # 40 + 60 covers 100 exactly → 2 parcels consumed, 0 waste.
    assert len(consumed) == 2
    assert waste == 0
    # The 200-purity parcel survives.
    surviving = [
        int(r.get("purity", 0)) for r in sess.hoard_squares["p1"]
        if int(r.get("tile_at_harvest", -1)) == int(Tile.BLUE)
    ]
    assert surviving == [200]


def test_debit_blue_purity_refunds_overpay_as_residual_parcel() -> None:
    """v0.9.5 — overpay refund. Pre-v0.9.5 the second parcel's
    excess (20 purity in this case) was wasted; now we mint a
    residual BLUE parcel for the leftover so the user's vault
    only loses the actual cost. Surfaced after the user reported
    "building blue uses all the blue, not the amount" — building
    a 100b weapon out of a hoard sitting on a single 255p sink
    parcel was nuking 255 purity from the vault.
    """
    sess = _fresh_night_session()
    _stamp_blue_parcels(sess, "p1", [40, 80])
    ok, consumed, waste = sess.debit_blue_purity("p1", 100)
    assert ok is True
    assert len(consumed) == 2
    # ``waste`` is preserved in the return tuple for back-compat with
    # callers that log the value, but it's now ALWAYS zero — overpay
    # is refunded, never burned.
    assert waste == 0
    # The leftover 20 purity from the second parcel returns as a
    # single residual BLUE parcel with the canonical
    # ``purity_at_harvest`` shape so the next hoard read sees it.
    surviving_blue = [
        int(r.get("purity_at_harvest", 0))
        for r in sess.hoard_squares["p1"]
        if int(r.get("tile_at_harvest", -1)) == int(Tile.BLUE)
    ]
    assert surviving_blue == [20]
    # And the total blue available drops by exactly the cost.
    assert sess.blue_purity_available("p1") == 20


def test_debit_blue_purity_no_residual_when_exact() -> None:
    """v0.9.5 — when the consumed parcels exactly cover the cost
    (overpay == 0) no residual parcel is minted. The hoard simply
    shrinks by the number of consumed parcels."""
    sess = _fresh_night_session()
    _stamp_blue_parcels(sess, "p1", [40, 60, 200])
    ok, consumed, waste = sess.debit_blue_purity("p1", 100)
    assert ok is True
    assert waste == 0
    blue_left = [
        r for r in sess.hoard_squares["p1"]
        if int(r.get("tile_at_harvest", -1)) == int(Tile.BLUE)
    ]
    assert len(blue_left) == 1
    assert sess.blue_purity_available("p1") == 200


def test_debit_blue_purity_refuses_when_short() -> None:
    sess = _fresh_night_session()
    _stamp_blue_parcels(sess, "p1", [10, 10])
    ok, consumed, waste = sess.debit_blue_purity("p1", 100)
    assert ok is False
    assert consumed == []
    assert waste == 0
    # Bucket untouched.
    assert len(sess.hoard_squares["p1"]) == 2


# ── v0.9.3 build-first pipeline ─────────────────────────────────────


def test_build_emp_debits_and_increments_stock() -> None:
    """``apply_build_emp`` pays once + adds to stock atomically."""
    sess = _fresh_night_session()
    _stamp_blue_parcels(sess, "p1", [EMP_COST_BLUE_PURITY])
    sess.credits["p1"] = EMP_COST_CREDITS + 10
    ok, _ = sess.apply_build_emp("p1", count=1)
    assert ok is True
    assert sess.weapon_stock["p1"]["emp"] == 1
    assert sess.credits["p1"] == 10


def test_build_emp_refuses_when_short_blue_or_credits() -> None:
    sess = _fresh_night_session()
    _stamp_blue_parcels(sess, "p1", [10])
    sess.credits["p1"] = 9999
    ok, _ = sess.apply_build_emp("p1", count=1)
    assert ok is False
    assert sess.weapon_stock["p1"]["emp"] == 0


def test_build_emp_batch_is_atomic() -> None:
    """``count=3`` succeeds iff full batch is affordable in one go."""
    sess = _fresh_night_session()
    # Just enough for two but not three.
    _stamp_blue_parcels(sess, "p1", [EMP_COST_BLUE_PURITY * 2])
    sess.credits["p1"] = EMP_COST_CREDITS * 3
    ok, _ = sess.apply_build_emp("p1", count=3)
    assert ok is False
    # Nothing debited on failure.
    assert sess.weapon_stock["p1"]["emp"] == 0
    assert sess.credits["p1"] == EMP_COST_CREDITS * 3


def test_build_chaff_round_trip() -> None:
    sess = _fresh_night_session()
    # v1.36 — a chaff now costs 300 and a parcel's purity is clamped at
    # 255 (§3.14), so no single parcel can fund one. That is a real
    # consequence of the retune and not a fixture wart: chaff is the
    # first thing in the game you cannot buy out of one good harvest.
    _stamp_blue_parcels(sess, "p1", _blue_parcels_worth(CHAFF_COST_BLUE_PURITY))
    sess.credits["p1"] = CHAFF_COST_CREDITS
    ok, _ = sess.apply_build_chaff("p1", count=1)
    assert ok
    assert sess.weapon_stock["p1"]["chaff"] == 1


# ── EMP launch ──────────────────────────────────────────────────────


def test_emp_launch_consumes_stock_and_mints_cloud() -> None:
    """v0.9.3 — emp_launch drains weapon_stock['emp'] (no live debit)."""
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    pre_credits = sess.credits["p1"]
    ok, msg = sess.apply_emp_launch("p1", 5, 5, hour=3)
    assert ok is True, msg
    assert len(sess.emp_clouds) == 1
    c = sess.emp_clouds[0]
    assert c["cx"] == 5 and c["cy"] == 5
    assert c["radius"] == EMP_RADIUS
    assert c["hours_remaining"] == EMP_CLOUD_HOURS
    # Stockpile drained, credits untouched (cost was paid at build time).
    assert sess.weapon_stock["p1"]["emp"] == 0
    assert sess.credits["p1"] == pre_credits


def test_emp_launch_refuses_when_no_stockpile() -> None:
    """An empty EMP magazine wastes the launch with no side-effects."""
    sess = _fresh_night_session()
    sess.weapon_stock["p1"] = {"emp": 0, "chaff": 0}
    ok, _ = sess.apply_emp_launch("p1", 5, 5, hour=3)
    assert ok is False
    assert sess.emp_clouds == []


def test_weapons_used_counter_bumps_on_every_successful_fire() -> None:
    """v0.9.5 — ``weapons_used[player][kind]`` is a lifetime
    counter bumped by 1 every time a fire path SUCCEEDS. Failed
    fires (empty stockpile) must NOT bump it. The counter drives
    the VAULT "USED" bay so the seat can see what they've burned
    across the season.
    """
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=2)
    _stock_weapon(sess, "p1", "chaff", n=1)

    # Two successful EMP launches → counter at 2.
    ok1, _ = sess.apply_emp_launch("p1", 4, 4, hour=1)
    ok2, _ = sess.apply_emp_launch("p1", 5, 5, hour=2)
    assert ok1 and ok2
    # Stockpile empty now — the next launch should refuse AND
    # leave the counter at 2.
    refused, _ = sess.apply_emp_launch("p1", 6, 6, hour=3)
    assert refused is False
    assert sess.weapons_used["p1"]["emp"] == 2

    # Chaff bumps the chaff counter, but the EMP counter stays put.
    ok_c, _ = sess.apply_chaff_flare("p1", hour=5)
    assert ok_c
    assert sess.weapons_used["p1"]["chaff"] == 1
    assert sess.weapons_used["p1"]["emp"] == 2

    # Opposing seat must remain at zero — counters are per-seat. Spelled
    # against the game's own price list rather than a literal pair, so
    # adding or retiring a weapon does not fail a test about seats.
    assert sess.weapons_used["p2"] == dict.fromkeys(sess.weapon_prices(), 0)


def test_weapons_used_counter_round_trips_via_to_from_dict() -> None:
    """v0.9.5 — ``weapons_used`` must survive a snapshot round-trip
    so the VAULT "USED" bay persists across server restarts. Pre-
    v0.9.5 snapshots (without the field) deserialise as zeros, no
    crash.
    """
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    sess.apply_emp_launch("p1", 3, 3, hour=1)
    assert sess.weapons_used["p1"]["emp"] == 1

    snap = sess.to_dict()
    revived = type(sess).from_dict(snap)
    assert revived.weapons_used["p1"]["emp"] == 1
    assert revived.weapons_used["p2"]["emp"] == 0

    # Legacy snapshot path: strip the field, ensure from_dict
    # still loads with zero counters rather than KeyError-ing.
    legacy = {k: v for k, v in snap.items() if k != "weapons_used"}
    legacy_revived = type(sess).from_dict(legacy)
    assert legacy_revived.weapons_used["p1"] == dict.fromkeys(
        legacy_revived.weapon_prices(), 0
    )


def test_inventory_pack_surfaces_both_weapon_bays() -> None:
    """v0.9.5 — ``inventory_pack`` carries both ``weapon_stock``
    (AVAILABLE) and ``weapons_used`` (USED, lifetime). The VAULT
    panel reads both from a single payload to render the two bays.
    """
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=3)
    _stock_weapon(sess, "p1", "chaff", n=2)
    sess.apply_emp_launch("p1", 4, 4, hour=1)

    pack = sess.inventory_pack("p1")
    assert "weapon_stock" in pack
    assert "weapons_used" in pack
    # After the fire: AVAILABLE drops by one (3 → 2), USED grows
    # by one (0 → 1). The chaff bay is untouched by an EMP fire.
    assert pack["weapon_stock"]["emp"] == 2
    assert pack["weapon_stock"]["chaff"] == 2
    assert pack["weapons_used"]["emp"] == 1
    assert pack["weapons_used"]["chaff"] == 0


def test_emp_cloud_decays_each_hour() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    sess.apply_emp_launch("p1", 3, 3, hour=1)
    initial = sess.emp_clouds[0]["hours_remaining"]
    for _ in range(EMP_CLOUD_HOURS - 1):
        sess.tick_emp_clouds()
    assert sess.emp_clouds[0]["hours_remaining"] == initial - (EMP_CLOUD_HOURS - 1)
    sess.tick_emp_clouds()
    assert sess.emp_clouds == []


def test_emp_cloud_cells_manhattan_disk() -> None:
    """v0.9.4 — EMP AoE is a rhombus (Manhattan disk), not a square."""
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    sess.apply_emp_launch("p1", 5, 5, hour=1)
    cells = sess.cells_in_any_emp_cloud()
    # Manhattan disk size for radius r: 2r²+2r+1.
    expected = 2 * EMP_RADIUS * EMP_RADIUS + 2 * EMP_RADIUS + 1
    assert len(cells) == expected
    assert (5, 5) in cells
    # East tip of the diamond (on the edge): still inside.
    assert (5 + EMP_RADIUS, 5) in cells
    # One step past the east tip: outside.
    assert (5 + EMP_RADIUS + 1, 5) not in cells
    # Diagonal corner of the Chebyshev square sits OUTSIDE the rhombus.
    assert (5 + EMP_RADIUS, 5 + EMP_RADIUS) not in cells
    # Midway diagonal: |dx|+|dy| = r → exactly on the diamond.
    assert (5 + 1, 5 + (EMP_RADIUS - 1)) in cells


# ── Chaff ───────────────────────────────────────────────────────────


def test_chaff_flare_consumes_stock() -> None:
    """v0.9.3 — chaff_flare drains weapon_stock['chaff']."""
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "chaff", n=1)
    pre_credits = sess.credits["p1"]
    ok, _ = sess.apply_chaff_flare("p1", hour=4)
    assert ok is True
    assert sess.weapon_stock["p1"]["chaff"] == 0
    assert sess.credits["p1"] == pre_credits
    chaff_events = [
        e for e in sess.pending_chaff_events
        if e.get("kind") == "chaff_flare"
    ]
    assert chaff_events
    assert chaff_events[0]["from_hour"] == 4
    assert chaff_events[0]["until_hour"] == 4 + CHAFF_DURATION_HOURS - 1


def test_chaff_flare_refuses_when_no_stockpile() -> None:
    sess = _fresh_night_session()
    sess.weapon_stock["p1"] = {"emp": 0, "chaff": 0}
    ok, _ = sess.apply_chaff_flare("p1", hour=4)
    assert ok is False
    assert sess.pending_chaff_events == []


# ── PRAXIS integration ──────────────────────────────────────────────


def test_night_emp_disables_harvester_in_cloud_next_hour() -> None:
    """An EMP launched at hour 1 disables an in-cloud harvester at hour 2."""
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 8, 8
    h2.damaged = False

    queues = {
        "p1": [EmpLaunchMove(at=(8, 8))],
        # p2 tries to step at hour 1 — but its harvester is at (8,8),
        # which sits in the cloud the moment p1's hour-1 EMP resolves.
        # p2's step at hour 1 must be smothered (``empd``).
        "p2": [StepMove(unit="harvester_p2", to=(7, 8))],
    }
    NightSimulator().run(sess, queues)
    # The harvester must still be at (8,8) — its step was smothered.
    assert (h2.x, h2.y) == (8, 8)
    # Replay should contain an empd frame.
    empd_frames = [
        f for f in sess.last_night_replay if f.get("tag") == "empd"
    ]
    assert empd_frames


# ── v1.10 — EMP denies auto-harvest on landing (§4.9.3) ─────────────


def test_try_step_unit_denies_harvest_on_established_emp_cell() -> None:
    """A step into a cell already inside an active EMP cloud still
    lands, but the auto-harvest is denied (RULEBOOK §4.9.3, v1.10)."""
    sess = _fresh_night_session()
    h = sess.entities["harvester_p1"]
    h.x, h.y = 4, 5
    h.damaged = False
    sess.grid[5][5] = Cell(Tile.RED, 200)

    ok, msg, harvested = sess.try_step_unit(
        "p1", "harvester_p1", 5, 5, emp_blocked_cells={(5, 5)},
    )
    assert ok is True
    assert harvested is False
    assert "EMP cloud denies auto-harvest" in msg
    assert (h.x, h.y) == (5, 5)  # the step itself still lands
    assert sess.grid[5][5].tile == Tile.RED  # no colour conversion
    assert h.cargo_squares == []  # nothing banked


def test_try_step_unit_harvests_normally_outside_emp_blocked_cells() -> None:
    """The new gate is opt-in per-cell: a step landing outside the
    blocked set (or with no EMP at all) harvests exactly as before."""
    sess = _fresh_night_session()
    h = sess.entities["harvester_p1"]
    h.x, h.y = 4, 5
    h.damaged = False
    sess.grid[5][5] = Cell(Tile.RED, 200)

    ok, msg, harvested = sess.try_step_unit(
        "p1", "harvester_p1", 5, 5, emp_blocked_cells={(9, 9)},
    )
    assert ok is True
    assert harvested is True
    assert sess.grid[5][5].tile == Tile.GREEN
    assert len(h.cargo_squares) == 1


def test_try_drop_unit_denies_harvest_on_established_emp_cell(monkeypatch) -> None:
    """A drop onto a cell already inside an active EMP cloud still
    lands, but the auto-harvest is denied (RULEBOOK §4.9.3, v1.10)."""
    # Drop legality (live/echo, §3.9.7) is orthogonal to the EMP gate
    # under test — relax to live_or_echo so the conftest memory-stamp
    # shim (which only satisfies the echo/memory path) covers it.
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")
    sess = _fresh_night_session()
    sess.grid[6][7] = Cell(Tile.RED, 200)

    ok, msg, harvested = sess.try_drop_unit(
        "p1", "harvester_p1", 7, 6, emp_blocked_cells={(7, 6)},
    )
    assert ok is True
    assert harvested is False
    assert "EMP cloud denies auto-harvest" in msg
    h = sess.entities["harvester_p1"]
    assert (h.x, h.y) == (7, 6)  # the drop itself still lands
    assert sess.grid[6][7].tile == Tile.RED  # no colour conversion


def test_night_step_into_established_emp_cloud_does_not_harvest() -> None:
    """Integration: a harvester that steps into a cloud which was
    already standing before the hour began does not auto-harvest,
    even though the step lands normally (RULEBOOK §4.9.3, v1.10).

    ``harvester_p2`` starts at Manhattan distance 3 from the EMP
    centre (8,8) — OUTSIDE the radius-2 blast — so it is never itself
    disabled by the pre-existing "current cell inside cloud" rule; it
    steps onto (6,8), which sits exactly on the blast boundary
    (distance 2), well after the cloud (formed hour 1) is established.
    """
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 5, 8
    h2.damaged = False
    sess.grid[8][6] = Cell(Tile.RED, 200)

    queues = {
        # EMP launched hour 1; p1 has nothing else queued.
        "p1": [EmpLaunchMove(at=(8, 8))],
        # p2 waits two hours so its step into the cloud lands at
        # hour 3 — well after the cloud (formed hour 1) is established.
        "p2": [
            WaitMove(),
            WaitMove(),
            StepMove(unit="harvester_p2", to=(6, 8)),
        ],
    }
    NightSimulator().run(sess, queues)

    assert (h2.x, h2.y) == (6, 8)  # the step still landed
    assert sess.grid[8][6].tile == Tile.RED  # never converted
    assert h2.cargo_squares == []  # nothing banked
    denied = [
        f for f in sess.last_night_replay
        if "EMP cloud denies auto-harvest" in (f.get("caption") or "")
    ]
    assert denied


def test_night_same_hour_emp_launch_and_landing_still_harvests_once(
    monkeypatch,
) -> None:
    """Grace case: a cloud freshly spawned by a launch resolved THIS
    hour does not block that same hour's landing — only a cloud that
    was already established beforehand does (RULEBOOK §4.9.3, v1.10).

    Uses a DROP (not a step) so the landing harvester starts orbital
    (no "current cell") and is never itself pre-disabled by the
    existing rule — this isolates the new landing-harvest gate,
    mirroring the exact "EMP launched + harvester descends same hour"
    scenario from the rulebook note.
    """
    # Drop legality (live/echo) is orthogonal to the EMP gate under
    # test — relax to live_or_echo so the conftest memory-stamp shim
    # covers the landing cell without a dedicated probe setup.
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    sess.grid[8][8] = Cell(Tile.RED, 200)

    queues = {
        # Both p1's launch and p2's drop resolve during hour 1.
        "p1": [EmpLaunchMove(at=(8, 8))],
        # Pickup is EXEMPT from the EMP disable (§3.9.13) even though
        # the harvester is now standing in the established cloud, so
        # it safely extracts at hour 2 — this also keeps the unit from
        # being destroyed as "abandoned on the surface" at Aurora,
        # which would otherwise remove it from ``sess.entities``.
        "p2": [
            DropMove(unit="harvester_p2", at=(8, 8)),
            PickupMove(unit="harvester_p2"),
        ],
    }
    NightSimulator().run(sess, queues)

    # The same-hour coincidence still banked the parcel...
    assert sess.grid[8][8].tile == Tile.GREEN
    assert len(banked_parcels(sess, "p2")) == 1
    drop_frames = [
        f for f in sess.last_night_replay
        if f.get("owner") == "p2" and f.get("tag") == "drop"
    ]
    assert drop_frames and "auto-harvested" in drop_frames[0]["caption"]
    # ...but the harvester was standing in the cloud from hour 1 on, so
    # any further NON-pickup action would have gone empd (covered by
    # the existing next-hour-disable test above) — only pickup is
    # exempt, which is exactly what let it come home safely here.
    h2 = sess.entities["harvester_p2"]
    assert h2.x is None and h2.y is None  # safely extracted, not stranded


def test_night_chaff_cancels_other_seat_same_hour() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "chaff", n=1)
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 5, 5
    h2.damaged = False

    queues = {
        "p1": [ChaffFlareMove()],
        "p2": [StepMove(unit="harvester_p2", to=(5, 6))],
    }
    NightSimulator().run(sess, queues)
    # p2's step must have been chaffed: harvester stayed put.
    assert (h2.x, h2.y) == (5, 5)
    chaffed_frames = [
        f for f in sess.last_night_replay if f.get("tag") == "chaffed"
    ]
    assert chaffed_frames


def test_night_wait_consumes_a_slot_without_acting() -> None:
    sess = _fresh_night_session()
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 4, 4
    h2.damaged = False

    queues = {
        "p1": [WaitMove(), WaitMove()],
        "p2": [WaitMove(), StepMove(unit="harvester_p2", to=(4, 5))],
    }
    NightSimulator().run(sess, queues)
    # p2's step at hour 2 must have applied (waited hour 1).
    assert (h2.x, h2.y) == (4, 5)
    wait_frames = [
        f for f in sess.last_night_replay if f.get("tag") == "wait"
    ]
    assert len(wait_frames) >= 2  # at least both p1's + p2's first hour


# ── Persistence ─────────────────────────────────────────────────────


def test_emp_clouds_round_trip_via_to_dict() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    ok_emp, msg_emp = sess.apply_emp_launch("p1", 9, 9, hour=1)
    assert ok_emp, msg_emp
    blob = sess.to_dict()
    restored = GameSession.from_dict(blob)
    assert len(restored.emp_clouds) == 1
    assert restored.emp_clouds[0]["cx"] == 9


def test_weapon_stock_round_trips_via_to_dict() -> None:
    """v0.9.3 — built weapon stockpile survives JSON round-trip.

    v1.36 — the counters are dense over the kinds the game prices, so
    what comes back carries a zero for anything this seat did not set.
    The round trip is about the numbers surviving, not about which
    weapons exist, so it asks only about the ones it wrote.
    """
    sess = _fresh_night_session()
    sess.weapon_stock["p1"] = {"emp": 3, "chaff": 1}
    sess.weapon_stock["p2"] = {"emp": 0, "chaff": 4}
    restored = GameSession.from_dict(sess.to_dict())
    assert restored.weapon_stock["p1"]["emp"] == 3
    assert restored.weapon_stock["p1"]["chaff"] == 1
    assert restored.weapon_stock["p2"]["emp"] == 0
    assert restored.weapon_stock["p2"]["chaff"] == 4
    for seat in ("p1", "p2"):
        assert set(restored.weapon_stock[seat]) == set(restored.weapon_prices())


def test_stale_mine_stock_refunds_as_blue_on_load() -> None:
    """v1.31 — a pre-retirement save can hold armed caltrops and unspent
    mine stock. Load must disarm the board and hand the blue back rather
    than stranding the spend, and the surviving bays must be the two
    live weapons only.
    """
    sess = _fresh_night_session()
    blob = sess.to_dict()
    blob["weapon_stock"]["p1"]["mine"] = 2
    blob["mines"] = {"5:5": {"owner": "p1", "laid_at_day": 1,
                             "laid_at_hour": 7, "witnesses": []}}
    pre_blue = int(sess.blue_bank["p1"])

    restored = GameSession.from_dict(blob)
    assert restored.mines == {}
    # The dead bay is gone, and what survives is exactly what this game
    # prices — asked that way round so the test keeps saying "no retired
    # weapon leaks through" when a live weapon is added beside it.
    assert "mine" not in restored.weapon_stock["p1"]
    assert set(restored.weapon_stock["p1"]) == set(restored.weapon_prices())
    assert restored.blue_bank["p1"] == pre_blue + 2 * _MINE_REFUND_BLUE_EACH


def test_player_dense_view_surfaces_active_emp_cloud_on_cell() -> None:
    """v0.9.5 — EMP clouds are public (RULEBOOK §5.1, "not gated by
    fog"), so the per-cell payload returned by
    :meth:`player_dense_view` MUST carry an ``emp_cloud`` blob on
    every visible cell that sits inside the cloud's Manhattan disk.
    The watcher's hover tooltip reads this directly — without it the
    UI has to cross-reference the frame-level ``emp_clouds`` list,
    which the old build skipped (hence the "EMP not in mouseover"
    report from v0.9.4).
    """
    sess = _fresh_night_session()
    # Park the harvester so the cloud centre sits on a live LOS
    # tile — fog cells are intentionally skipped by the
    # emp_cloud_at stamp (the frontend already handles fog via the
    # separate cloud overlay).
    h = sess.entities["harvester_p1"]
    cx, cy = 6, 6
    h.x, h.y = cx, cy
    sess.emp_clouds.append({
        "owner": "p1", "cx": cx, "cy": cy,
        "radius": 2, "hours_remaining": 3,
    })
    cells = sess.player_dense_view("p1")
    centre = cells[cy * sess.width + cx]
    assert "emp_cloud" in centre, "centre tile must carry emp_cloud payload"
    emp = centre["emp_cloud"]
    assert emp["owner"] == "p1"
    assert emp["hours_remaining"] == 3
    assert emp["cx"] == cx and emp["cy"] == cy


def test_observer_cells_surface_emp_cloud() -> None:
    """v0.9.5 — observer view is omniscient by definition. The
    "obs" replay seat had no per-cell EMP cloud payload, so the
    watcher's hover tooltip was blank for a cloud when the user
    toggled the omniscient pane. It must ride on the cell payload
    returned by :meth:`observer_cells_rowmajor`.
    """
    sess = _fresh_night_session()
    sess.emp_clouds.append({
        "owner": "p1", "cx": 7, "cy": 7,
        "radius": 1, "hours_remaining": 2,
    })
    cells = sess.observer_cells_rowmajor()
    emp_centre = cells[7 * sess.width + 7]
    assert emp_centre.get("emp_cloud", {}).get("owner") == "p1"


# ── v0.9.x EMP salvo + cross-system destruction ─────────────────────


def test_parse_emp_salvo_move() -> None:
    """A list-of-cells ``at`` parses into a multi-target salvo."""
    moves, errors = parse_moves(
        [{"a": "emp_launch", "at": [[1, 1], [2, 2], [3, 3]]}]
    )
    assert errors == []
    assert isinstance(moves[0], EmpLaunchMove)
    assert moves[0].at == (1, 1)
    assert moves[0].extra_ats == ((2, 2), (3, 3))
    assert moves[0].ats == ((1, 1), (2, 2), (3, 3))


def test_emp_single_at_still_parses_as_one_missile() -> None:
    moves, _ = parse_moves([{"a": "emp_launch", "at": [5, 6]}])
    assert isinstance(moves[0], EmpLaunchMove)
    assert moves[0].at == (5, 6)
    assert moves[0].extra_ats == ()


def test_emp_salvo_spawns_cloud_per_target_for_one_stock() -> None:
    """One launch with N targets spawns N clouds and drains ONE stock."""
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    ok, msg = sess.apply_emp_launch(
        "p1", 4, 4, hour=1, extra_targets=[(10, 4), (4, 10)],
    )
    assert ok is True, msg
    assert len(sess.emp_clouds) == 3
    assert sess.weapon_stock["p1"]["emp"] == 0
    centres = {(c["cx"], c["cy"]) for c in sess.emp_clouds}
    assert centres == {(4, 4), (10, 4), (4, 10)}


def test_emp_salvo_caps_at_missiles_per_launch() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    # Offer more targets than the salvo allows; only the cap spawns.
    extras = [(10, 4), (4, 10), (12, 12), (1, 1)]
    sess.apply_emp_launch("p1", 4, 4, hour=1, extra_targets=extras)
    assert len(sess.emp_clouds) == EMP_MISSILES_PER_LAUNCH


def test_emp_destroys_probes_in_blast() -> None:
    """EMP frying — probes caught in the cloud are destroyed."""
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    # A rival probe sitting at the blast centre.
    sess.entities["probe_p2_1"] = Entity(
        id="probe_p2_1", entity_type="probe", owner="p2", x=5, y=5,
    )
    ok, _ = sess.apply_emp_launch("p1", 5, 5, hour=2)
    assert ok is True
    assert "probe_p2_1" not in sess.entities
    launch_ev = [
        e for e in sess.pending_emp_events if e.get("kind") == "emp_launch"
    ][-1]
    assert len(launch_ev["destroyed_probes"]) == 1


def test_emp_field_sweep_kills_unit_entering_standing_cloud() -> None:
    """A probe that appears inside a live cloud is swept on tick."""
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    sess.apply_emp_launch("p1", 5, 5, hour=1)
    # Drop a fresh probe into the standing cloud AFTER formation.
    sess.entities["probe_p2_2"] = Entity(
        id="probe_p2_2", entity_type="probe", owner="p2", x=5, y=5,
    )
    sess.tick_emp_clouds()
    assert "probe_p2_2" not in sess.entities


# ── v0.9.x chaff multi-hour window ──────────────────────────────────


def test_chaff_until_hour_reflects_three_hour_window() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "chaff", n=1)
    sess.apply_chaff_flare("p1", hour=4)
    ev = [
        e for e in sess.pending_chaff_events if e.get("kind") == "chaff_flare"
    ][-1]
    assert ev["from_hour"] == 4
    assert ev["until_hour"] == 4 + CHAFF_DURATION_HOURS - 1


def test_night_chaff_cancels_exactly_duration_hours_no_overstretch() -> None:
    """A single chaff at hour 1 smothers the rival for exactly
    CHAFF_DURATION_HOURS hours — not the doubled window the old
    carry-forward bug produced when DURATION > 1."""
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "chaff", n=1)
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 5, 5
    h2.damaged = False
    dur = int(CHAFF_DURATION_HOURS)
    # p1 fires chaff at hour 1, then waits. p2 tries to step to the
    # adjacent (5,6) every hour; while chaffed it never moves (so the
    # target stays adjacent). The first ``dur`` steps are smothered,
    # the next one lands.
    p2_steps = [
        StepMove(unit="harvester_p2", to=(5, 6)) for _ in range(dur + 1)
    ]
    queues = {
        "p1": [ChaffFlareMove()] + [WaitMove() for _ in range(dur + 1)],
        "p2": p2_steps,
    }
    NightSimulator().run(sess, queues)
    chaffed = [
        f for f in sess.last_night_replay if f.get("tag") == "chaffed"
        and f.get("owner") == "p2"
    ]
    # Exactly ``dur`` of p2's hours were chaffed (not 2*dur-1).
    assert len(chaffed) == dur
    # And p2 finally moved on the hour after the window closed.
    assert (h2.x, h2.y) == (5, 6)


def test_night_chaff_jams_its_own_launcher_for_full_window() -> None:
    """Chaff jams the HOUSE THAT FIRED IT too: it is immune only for the
    launch hour (it spent that slot firing), then self-jammed for the
    carry-over hours, so a flare costs the launcher the full
    CHAFF_DURATION_HOURS-turn window (launch + self-jam)."""
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "chaff", n=1)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 10, 10
    h1.damaged = False
    dur = int(CHAFF_DURATION_HOURS)
    # Fire chaff, then try to step every hour. The launcher should be
    # jammed for the (dur - 1) carry-over hours, then move once the
    # window closes.
    p1_steps = [StepMove(unit="harvester_p1", to=(10, 11)) for _ in range(dur + 1)]
    queues = {
        "p1": [ChaffFlareMove()] + p1_steps,
        "p2": [],
    }
    NightSimulator().run(sess, queues)
    self_chaffed = [
        f for f in sess.last_night_replay
        if f.get("tag") == "chaffed" and f.get("owner") == "p1"
    ]
    # Launch hour is immune; the carry-over hours (dur - 1) jam the launcher.
    assert len(self_chaffed) == dur - 1
    # The launcher's first surviving step lands the hour the window closes.
    assert (h1.x, h1.y) == (10, 11)


# ── Tuning-tweak smoke ──────────────────────────────────────────────


def test_tuning_constants_are_imported_singletons(monkeypatch) -> None:
    """Bumping the constants module changes engine behaviour.

    Monkey-patches the EMP radius to 1 and re-runs the cloud-cell
    computation to confirm the engine reads the value at apply time
    (no captured constants in the engine).
    """
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    import sea_of_colours.game.weapons as weapons_mod
    monkeypatch.setattr(weapons_mod, "EMP_RADIUS", 1)
    sess.apply_emp_launch("p1", 5, 5, hour=1)
    cells = sess.cells_in_any_emp_cloud()
    # v0.9.4 — rhombus footprint. Manhattan disk for r=1: 2*1²+2*1+1 = 5.
    assert len(cells) == 5


# ── v0.9.15 — recap surfaces abandoned / emp'd / damaged harvesters ────


def test_recap_flags_emped_abandoned_harvester() -> None:
    """A harvester emp'd then stranded must read ✖ abandoned · emp'd in
    BOTH the ordered event log and the count-fallback tally."""
    from sea_of_colours.game.session import _xy_key

    sess = _fresh_night_session()
    sess.day = 2
    sess.destroyed_harvester_markers[_xy_key(5, 5)] = {
        "owner": "p2", "harvester_id": "harvester_p2", "day": 2,
    }
    frames = [
        {"owner": "p2", "tag": "drop",
         "caption": "p2 dropped harvester_p2 at (5,5)"},
        {"owner": "p2", "tag": "empd",
         "caption": "p2: harvester_p2 disabled by EMP cloud @ hour 3"},
    ]
    events = sess.tally_orbital_events(frames)
    abandoned = [e for e in events["p2"] if e["tag"] == "abandoned"]
    assert abandoned, "stranded harvester must produce an abandoned event"
    assert abandoned[0].get("emped") is True

    activity = sess.tally_orbital_activity(frames)
    assert activity["p2"]["abandoned"] == 1
    assert activity["p2"]["abandoned_emped"] == 1


def test_recap_surfaces_damaged_survivor_harvester() -> None:
    """A harvester damaged in the field that limps back to orbit (no clean
    pickup, not stranded) must still surface a damaged line."""
    sess = _fresh_night_session()
    sess.day = 3
    frames = [
        {"owner": "p3", "tag": "damaged",
         "caption": "p3: 5× action on harvester_p3 struck "
                    "— unit damaged, awaiting pickup"},
    ]
    events = sess.tally_orbital_events(frames)
    dmg = [e for e in events["p3"] if e["tag"] == "damaged"]
    assert dmg, "a damaged-but-orbital harvester must surface a damaged line"

    activity = sess.tally_orbital_activity(frames)
    assert activity["p3"]["damaged"] == 1


def test_recap_damaged_repaired_by_pickup_has_no_survivor_line() -> None:
    """A pickup that repairs field damage flags the pickup line and must
    NOT also emit a redundant survivor 'damaged' line."""
    sess = _fresh_night_session()
    sess.day = 1
    frames = [
        {"owner": "p1", "tag": "damaged",
         "caption": "p1: harvester_p1 damaged, awaiting pickup"},
        {"owner": "p1", "tag": "pickup",
         "caption": "harvester_p1 lifted to berth — banked 0 parcel(s) "
                    "(DAMAGED — requires REPAIR)"},
    ]
    events = sess.tally_orbital_events(frames)
    # v0.9.18: pickup no longer repairs. The damaged harvester was picked
    # up (orbital now), so it doesn't appear in the "still on surface
    # damaged" survivors list.
    assert not [e for e in events["p1"] if e["tag"] == "damaged"]
    pickup = [e for e in events["p1"] if e["tag"] == "pickup"]
    assert pickup and pickup[0].get("damaged") is True

    activity = sess.tally_orbital_activity(frames)
    assert activity["p1"]["damaged"] == 0
    assert activity["p1"]["recovered_damaged"] == 1
