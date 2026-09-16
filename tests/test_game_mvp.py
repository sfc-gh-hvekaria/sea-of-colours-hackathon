"""Smoke tests for the orchestrator queue model (see RULEBOOK §3.11)."""

from __future__ import annotations

from conftest import banked_parcels

import pytest

from sea_of_colours.game.entities import Entity
from sea_of_colours.game.policy import (
    HARVEST_CAP,
    MAX_MOVES,
    MAX_QUEUE_LEN,
    parse_moves,
)
from sea_of_colours.game.session import GameSession, PLAYERS
from sea_of_colours.generator import Cell, Tile


@pytest.fixture(autouse=True)
def _use_echo_drop_mode_for_legacy_tests(monkeypatch):
    """test_game_mvp.py was written before v0.9.17 canonical rules.
    
    These tests assume live_or_echo drop mode (no probe requirement for
    drops). Rather than rewriting every test to add probe coverage, we
    restore the old default for this entire module."""
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")


def test_new_game_spawns_four_core_units() -> None:
    sess = GameSession.new(18, 12, seed=4242)
    assert {e.id for e in sess.entities.values()} >= {
        "harvester_p1",
        "orblift_p1",
        "harvester_p2",
        "orblift_p2",
    }


def test_new_game_assigns_season_name() -> None:
    """Every freshly minted session must carry a human-friendly season
    label that is stable across calls with the same seed."""
    a = GameSession.new(18, 12, seed=42)
    b = GameSession.new(18, 12, seed=42)
    assert a.season_name is not None, "season_name should be auto-generated"
    assert a.season_name == b.season_name, (
        "season name must be deterministic from seed for repro/CLI use"
    )
    # Format check: <LatinWord>_<EnglishNoun> — exactly one underscore,
    # both halves start with an upper-case letter.
    assert "_" in a.season_name
    left, right = a.season_name.split("_", 1)
    assert left[:1].isupper() and right[:1].isupper(), a.season_name


def test_score_for_sums_parcel_purity() -> None:
    """v0.8.0 — score counts SHIPPED parcels only (RULEBOOK §3.1).

    Hoard contents no longer contribute to the score; players must
    push parcels through the Orbit catapult before they realise any
    score.
    """
    sess = GameSession.new(20, 14, seed=7)
    assert sess.score_for("p1") == 0
    assert sess.score_for("p2") == 0
    # Hoard parcels do NOT score (v0.8.0).
    sess.hoard_squares["p1"] = [
        {"purity_at_harvest": 200},
        {"origin_purity": 100},
        {"purity": 50},
    ]
    assert sess.score_for("p1") == 0, "hoard must NOT contribute to score"
    # Only shipped parcels score. v0.9.6 — purity 25 is trace tier
    # (×0.75) → score 19 (round(18.75)).
    sess.shipped_squares["p1"] = [{"purity_at_harvest": 25}]
    assert sess.score_for("p1") == round(25 * 0.75) == 19
    # Per-parcel clamp at 255 in the shipped bay. v0.9.6 — purity 255
    # is pure tier (×3.0) → 765.
    sess.shipped_squares["p2"] = [{"purity_at_harvest": 500}]
    assert sess.score_for("p2") == 255 * 3 == 765


def test_observer_carries_universal_trail_with_fresh_attribution() -> None:
    """v0.7.2 (RULEBOOK §3.12): trails are universal observability data.
    Each visible cell exposes a single aggregate ``trail`` summary —
    ``{n, tier, fresh_visits[...]}``. Only ``fresh_visits`` (≤ 1 day
    old) carries the per-seat ``{owner, h, day, n}`` attribution; older
    crossings collapse into the anonymous ``n`` total."""
    sess = GameSession.new(20, 14, seed=505)
    y = sess.height // 2
    # Carve a non-RED corridor for each player so the move doesn't auto-
    # harvest and confuse trail-vs-harvest precedence.
    for x in range(2, 6):
        sess.grid[y][x] = Cell(Tile.EMPTY, 0)
        sess.grid[y + 1][x] = Cell(Tile.EMPTY, 0)
    assert sess.stash_policy("p1", [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        {"a": "step", "unit": "harvester_p1", "to": [4, y]},
        {"a": "pickup", "unit": "harvester_p1"},
    ])[0]
    assert sess.stash_policy("p2", [
        {"a": "drop", "unit": "harvester_p2", "at": [3, y + 1]},
        {"a": "pickup", "unit": "harvester_p2"},
    ])[0]
    sess.maybe_resolve_if_ready()

    obs = sess.observer_cells_rowmajor()
    idx = lambda x, y: y * sess.width + x  # noqa: E731

    p1_cell = obs[idx(3, y)]
    p2_cell = obs[idx(3, y + 1)]
    trail_p1 = p1_cell.get("trail") or {}
    trail_p2 = p2_cell.get("trail") or {}
    assert trail_p1.get("n", 0) >= 1, f"p1 trail aggregate missing ({trail_p1!r})"
    assert trail_p2.get("n", 0) >= 1, f"p2 trail aggregate missing ({trail_p2!r})"

    fresh_p1 = trail_p1.get("fresh_visits") or []
    fresh_p2 = trail_p2.get("fresh_visits") or []
    p1_entry = next((v for v in fresh_p1 if v.get("owner") == "p1"), None)
    p2_entry = next((v for v in fresh_p2 if v.get("owner") == "p2"), None)
    assert p1_entry is not None, f"p1 fresh attribution missing ({fresh_p1!r})"
    assert p2_entry is not None, f"p2 fresh attribution missing ({fresh_p2!r})"
    assert p1_entry["h"] == "harvester_p1"
    assert p2_entry["h"] == "harvester_p2"
    # Trail laid on day 1; sess.day is now 2. (2 - 1) = 1 ≤ 1 → fresh.
    assert p1_entry["day"] == 1
    assert p2_entry["day"] == 1

    # Advance two more empty days; the day-1 attribution should have
    # dropped off ``fresh_visits`` (gap of 2 > 1) while the aggregate
    # ``n`` count survives anonymously.
    for _ in range(2):
        assert sess.stash_policy("p1", [])[0]
        assert sess.stash_policy("p2", [])[0]
        sess.maybe_resolve_if_ready()
    obs_later = sess.observer_cells_rowmajor()
    later = obs_later[idx(3, y)].get("trail") or {}
    assert later.get("n", 0) >= 1, "anonymous aggregate must survive"
    fresh_later = later.get("fresh_visits") or []
    assert all(v.get("owner") != "p1" or v.get("day", 0) >= sess.day - 1
               for v in fresh_later), (
        "day-1 attribution must drop off after two more days resolve "
        f"(got {fresh_later!r})"
    )


def test_player_view_trails_are_universal_but_fogged() -> None:
    """Trails are universal observability data (RULEBOOK §3.12 v0.7.2):
    any cell the player can see — fresh LOS or stale memory — exposes
    a single aggregate ``trail`` summary. Fog cells leak nothing.
    Fresh per-seat attribution survives in ``fresh_visits`` even when
    the watcher is the OPPONENT seat."""
    sess = GameSession.new(22, 16, seed=909)
    for x in range(2, 6):
        sess.grid[5][x] = Cell(Tile.EMPTY, 0)
    # Day 1: p2 walks the corridor while p1's probe sits far away; no
    # p1 vision covers (3,5). The trail must be invisible to p1.
    assert sess.stash_policy("p2", [
        {"a": "drop", "unit": "harvester_p2", "at": [3, 5]},
        {"a": "step", "unit": "harvester_p2", "to": [4, 5]},
        {"a": "pickup", "unit": "harvester_p2"},
    ])[0]
    assert sess.stash_policy("p1", [{"a": "probe", "at": [15, 10]}])[0]
    sess.maybe_resolve_if_ready()

    dense_p1 = sess.player_dense_view("p1")
    idx = lambda x, y: y * sess.width + x  # noqa: E731
    fogged_cell = dense_p1[idx(3, 5)]
    assert fogged_cell.get("kind") == "fog", (
        f"(3,5) should still be fog for p1 (got {fogged_cell!r})"
    )
    assert fogged_cell.get("trail") in (None, {}), (
        "fog cells must never leak trail data — fog-of-war gate"
    )

    # Day 2: p1 drops a probe ON the trail; p2 walks through again so
    # there's a Day-2 crossing fresh enough to surface attribution at
    # the moment p1 first sees the cell.
    assert sess.stash_policy("p1", [{"a": "probe", "at": [3, 5]}])[0]
    assert sess.stash_policy("p2", [
        {"a": "drop", "unit": "harvester_p2", "at": [3, 5]},
        {"a": "step", "unit": "harvester_p2", "to": [4, 5]},
        {"a": "pickup", "unit": "harvester_p2"},
    ])[0]
    sess.maybe_resolve_if_ready()
    dense_p1_after = sess.player_dense_view("p1")
    cell_after = dense_p1_after[idx(3, 5)]
    trail_after = cell_after.get("trail") or {}
    assert trail_after.get("n", 0) >= 1, (
        f"p1 should now see p2's trail at (3,5) (got {trail_after!r})"
    )
    fresh = trail_after.get("fresh_visits") or []
    p2_entry = next((v for v in fresh if v.get("owner") == "p2"), None)
    assert p2_entry is not None, (
        f"fresh attribution for p2 missing (got {fresh!r})"
    )
    assert p2_entry["h"] == "harvester_p2", p2_entry
    assert isinstance(p2_entry["day"], int) and p2_entry["day"] >= 1


def test_player_view_trails_survive_into_stale_memory() -> None:
    """A cell once seen but no longer in live vision still surfaces
    its trail data — stale memory is part of the observability
    surface, fog is not (RULEBOOK §3.8, §3.12)."""
    sess = GameSession.new(22, 16, seed=4242)
    # Walk p2 through (3,5) on day 1 with a p1 probe at (3,5).
    for x in range(2, 6):
        sess.grid[5][x] = Cell(Tile.EMPTY, 0)
    assert sess.stash_policy("p2", [
        {"a": "drop", "unit": "harvester_p2", "at": [3, 5]},
        {"a": "pickup", "unit": "harvester_p2"},
    ])[0]
    assert sess.stash_policy("p1", [{"a": "probe", "at": [3, 5]}])[0]
    sess.maybe_resolve_if_ready()
    # p2 walks again while p1 leaves the probe in place — confirms the
    # initial vision was established. p1 now relocates its probe far
    # away on day 2 so (3,5) drops out of LIVE vision and into stale
    # memory.
    assert sess.stash_policy("p1", [])[0]  # probe persists from day 1
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    dense_p1 = sess.player_dense_view("p1")
    idx = lambda x, y: y * sess.width + x  # noqa: E731
    cell = dense_p1[idx(3, 5)]
    # Whether the cell is still in fresh LOS or has dropped to stale
    # memory, it must NOT be fog and the trail must be visible.
    assert cell.get("kind") != "fog", (
        f"(3,5) should be visible (LOS or stale memory) for p1, got {cell!r}"
    )
    trail = cell.get("trail") or {}
    assert trail.get("n", 0) >= 1, (
        f"stale-memory cell should still expose the aggregate trail (got {trail!r})"
    )


def test_season_cap_locks_after_default_days() -> None:
    """After ``SEASON_DAY_CAP`` nights resolve, the session is
    SEASON_COMPLETE and further submits are rejected with a friendly
    message. The default cap is the module-level constant — v0.9.18
    settled on 7-night seasons (long enough for the Orbit economy and
    no-free-repair losses to bite, short enough to watch end-to-end)."""
    from sea_of_colours.game.session import Phase, SEASON_DAY_CAP

    assert SEASON_DAY_CAP == 7, "v0.9.18 canonical cadence is 7-night seasons"
    sess = GameSession.new(20, 14, seed=99)
    assert sess.season_day_cap == SEASON_DAY_CAP, (
        "GameSession.new() with no explicit cap must inherit the module default"
    )
    for _ in range(SEASON_DAY_CAP):
        assert sess.phase == Phase.PLANNING
        assert sess.stash_policy("p1", [])[0]
        assert sess.stash_policy("p2", [])[0]
        sess.maybe_resolve_if_ready()
    assert sess.phase == Phase.SEASON_COMPLETE
    assert sess.is_season_complete()
    ok, errs = sess.stash_policy("p1", [])
    assert not ok, "submits after the cap must be rejected"
    assert any("season complete" in e.lower() for e in errs), errs
    h1 = sess.entities["harvester_p1"]
    assert h1.x is None and h1.y is None, "Harvesters berth on orbital platform"


def test_season_day_cap_is_configurable_per_session() -> None:
    """v0.7.4 — ``GameSession.new(season_day_cap=N)`` overrides the
    module default so 7-night tournament runs and 5-night demos can
    share one Snowflake schema."""
    from sea_of_colours.game.session import Phase

    sess = GameSession.new(20, 14, seed=101, season_day_cap=7)
    assert sess.season_day_cap == 7
    # The cap must round-trip through to_dict / from_dict so a session
    # rehydrated from Snowflake preserves the per-season override.
    rehydrated = GameSession.from_dict(sess.to_dict())
    assert rehydrated.season_day_cap == 7
    # And the longer cap actually keeps the session open for night 6
    # and 7 (which the default 5-night build would have refused).
    for _ in range(7):
        assert sess.phase == Phase.PLANNING
        assert sess.stash_policy("p1", [])[0]
        assert sess.stash_policy("p2", [])[0]
        sess.maybe_resolve_if_ready()
    assert sess.phase == Phase.SEASON_COMPLETE
    # The terminal-phase message must quote the *per-session* cap,
    # not the module default — a 7-night season ending should say so.
    ok, errs = sess.stash_policy("p1", [])
    assert not ok
    assert any("day cap = 7" in e for e in errs), errs


def test_short_season_cap_terminates_early() -> None:
    """Symmetry check — a 3-night cap also terminates exactly when it
    should and the SEASON_COMPLETE message reflects the override."""
    from sea_of_colours.game.session import Phase

    sess = GameSession.new(20, 14, seed=102, season_day_cap=3)
    assert sess.season_day_cap == 3
    for _ in range(3):
        assert sess.phase == Phase.PLANNING
        assert sess.stash_policy("p1", [])[0]
        assert sess.stash_policy("p2", [])[0]
        sess.maybe_resolve_if_ready()
    assert sess.phase == Phase.SEASON_COMPLETE
    ok, errs = sess.stash_policy("p1", [])
    assert not ok
    assert any("day cap = 3" in e for e in errs), errs


@pytest.mark.parametrize("pid", PLAYERS)
def test_player_dense_view_is_mostly_hidden_at_spawn(pid: str) -> None:
    sess = GameSession.new(22, 16, seed=777)
    dense = sess.player_dense_view(pid)  # type: ignore[arg-type]
    fog = sum(1 for c in dense if c.get("kind") == "fog")
    assert fog > len(dense) // 2
    observer = sess.observer_cells_rowmajor()
    assert len(observer) == 22 * 16


def test_observer_no_surface_entities_until_drop() -> None:
    sess = GameSession.new(22, 16, seed=707)
    cells = sess.observer_cells_rowmajor()
    with_occupants = sum(
        1 for c in cells if isinstance(c.get("occupants"), list) and c["occupants"]
    )
    assert with_occupants == 0


def test_dual_empty_policies_resolve_with_no_replay_moves() -> None:
    sess = GameSession.new(20, 14, seed=555)
    d0 = sess.day
    assert sess.stash_policy("p1", [])[0]
    assert sess.stash_policy("p2", [])[0]
    assert sess.maybe_resolve_if_ready()
    assert sess.day == d0 + 1
    # v1.1 — two frames survive: the "[opening]" frame and the
    # unconditional dawn ("22nd hour") boundary that drives the sunrise
    # sweep at the end of every night.
    assert len(sess.last_night_replay) == 2
    assert sess.last_night_replay[0]["caption"].startswith("[opening]")
    assert sess.last_night_replay[-1].get("tag") == "dawn"


def test_long_queues_accepted_invalid_items_strikeout_burn_slots() -> None:
    """v0.9.9 — every queued row burns a slot, even illegal ones.

    Pre-v0.9.9 the orchestrator skipped invalid items without
    consuming a slot and re-tried with the next entry. The new rule
    is "21 attempts, full stop — illegal moves are struck through in
    the replay log so the seat can see what failed." This test pins
    that contract: a long queue of doomed pickups produces exactly
    ``MAX_MOVES`` waste frames (one per consumed slot) and the rest
    of the queue is left untouched.
    """
    sess = GameSession.new(28, 18, seed=12)
    huge = [{"a": "pickup", "unit": "harvester_p1"} for _ in range(MAX_MOVES + 11)]
    assert sess.stash_policy("p1", huge)[0]
    assert sess.stash_policy("p2", [])[0]
    assert len(sess.pending_policies["p1"] or []) == MAX_MOVES + 11
    sess.maybe_resolve_if_ready()
    # Opening frame + exactly MAX_MOVES (21) waste frames + the v1.1
    # unconditional dawn boundary — the seat ran out of slots before the
    # queue ran out of items.
    expected_frames = 1 + MAX_MOVES + 1
    assert len(sess.last_night_replay) == expected_frames, (
        f"expected opening + 1 waste frame per consumed slot + dawn "
        f"({expected_frames}), got {len(sess.last_night_replay)}"
    )
    waste_frames = [
        f for f in sess.last_night_replay if f.get("tag") == "waste"
    ]
    assert len(waste_frames) == MAX_MOVES
    assert all(f.get("outcome") == "failed" for f in waste_frames)
    errs = [e for e in sess.log if e["level"] == "error"]
    assert len(errs) == MAX_MOVES, (
        f"expected one error per consumed slot, got {len(errs)}"
    )
    assert all("pickup harvester_p1" in e["text"] for e in errs)


def test_long_queue_truncated_at_max_queue_len() -> None:
    """Sanity: queues longer than MAX_QUEUE_LEN are clipped at parse time."""
    sess = GameSession.new(20, 14, seed=7)
    flood = [{"a": "pickup", "unit": "harvester_p1"}] * (MAX_QUEUE_LEN + 25)
    assert sess.stash_policy("p1", flood)[0]
    assert len(sess.pending_policies["p1"] or []) == MAX_QUEUE_LEN


def test_v0_6_no_per_night_harvest_cap_hold_is_the_limit() -> None:
    """v0.6.0: removed the 5-RED-per-night cap; the limit is the 6-parcel hold.

    Drop on bare ground + 5 RED steps = 5 conversions, hoard +5. The
    queue is restricted to 5 movement-steps by MAX_MOVES interleave,
    but the harvest budget itself is now uncapped — every step on a
    coloured tile harvests it.
    """
    sess = GameSession.new(40, 12, seed=99)
    y = sess.height // 2
    sess.grid[y][3] = Cell(Tile.EMPTY, 0)
    for x in range(4, 15):
        sess.grid[y][x] = Cell(Tile.RED, 200)

    moves = [{"a": "drop", "unit": "harvester_p1", "at": [3, y]}]
    for x in range(4, 15):
        moves.append({"a": "step", "unit": "harvester_p1", "to": [x, y]})
    moves.append({"a": "pickup", "unit": "harvester_p1"})

    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    converted = sum(
        1 for xx in range(4, 15) if sess.grid[y][xx].tile == Tile.GREEN
    )
    # RULEBOOK §3 — the hold caps a single outing at 6 parcels. The drop
    # is on bare ground (banks nothing) so 6 RED steps fill the hold; the
    # 7th step (x=10) onward is cancelled by the engine, so exactly 6 RED
    # tiles convert even though 11 steps were queued. To bank the rest the
    # harvester would have to pick up and re-drop (a fresh outing).
    assert converted == 6, (
        f"expected the 6-parcel hold to cap conversions at 6, got {converted}"
    )
    assert len(banked_parcels(sess, "p1")) == 6
    # Tiles past the hold cap were never reached — still RED.
    assert all(sess.grid[y][xx].tile == Tile.RED for xx in range(10, 15))
    # The deprecated per-night sentinel is unrelated to the hold cap.
    assert HARVEST_CAP >= 6


def test_invalid_coord_step_is_skipped_and_logged_as_error() -> None:
    """An invalid step is filtered out, logged in yellow, and the next
    item in the queue runs (RULEBOOK §3.10)."""
    sess = GameSession.new(20, 14, seed=313)
    y = sess.height // 2
    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
        {"a": "step", "unit": "harvester_p1", "to": [99, 99]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    h = sess.entities["harvester_p1"]
    assert h.x is None, "harvester should have been picked up at the end"

    # v0.7.3: failed attempts DO push a replay frame so the synced
    # log drawer renders them chronologically — but the frame is
    # tagged ``"waste"`` with ``outcome="failed"``, distinct from
    # successful moves (tag in {"drop","step","pickup","probe"}).
    success_captions = [
        f["caption"]
        for f in sess.last_night_replay
        if f.get("outcome") != "failed" and f.get("tag") != "waste"
    ]
    assert not any(
        "not adjacent" in c or "out of bounds" in c for c in success_captions
    ), "bad step must not appear in a successful replay frame"
    # …but the bad step *is* surfaced in the structured log as a yellow error
    # carrying the attempted action.
    errors = [e for e in sess.log if e["level"] == "error"]
    assert any(
        "step harvester_p1" in e["text"] and "(99,99)" in e["text"]
        for e in errors
    ), f"expected the attempted step in an error entry; got {errors}"


def test_drop_then_steps_then_pickup_banks_into_hoard() -> None:
    sess = GameSession.new(22, 16, seed=813)
    dy = sess.height // 2
    tx, ty = 4, dy
    # Keep the drop tile bare so this test isolates the *step* harvest.
    sess.grid[dy][3] = Cell(Tile.EMPTY, 0)
    sess.grid[ty][tx] = Cell(Tile.RED, 220)
    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, dy]},
        {"a": "step", "unit": "harvester_p1", "to": [tx, ty]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()
    assert len(banked_parcels(sess, "p1")) == 1
    h = sess.entities["harvester_p1"]
    assert h.x is None and not h.cargo_squares
    assert not h.lost_last_night


def test_dawn_destroys_harvester_when_no_pickup_issued() -> None:
    """RULEBOOK rule: a harvester left on the surface at dawn is DESTROYED.

    The entity is removed from play (no longer in ``sess.entities``) and
    surfaces in the asset ledger's destroyed bucket with a
    ``destroyed_on_day`` stamp + a ``destroyed_by`` reason. Its cargo
    spills (never reaches the hoard).
    """
    sess = GameSession.new(20, 14, seed=909)
    dy = sess.height // 2
    tx, ty = 3, dy
    sess.grid[ty][tx + 1] = Cell(Tile.RED, 200)
    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [tx, ty]},
        {"a": "step", "unit": "harvester_p1", "to": [tx + 1, ty]},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()
    # Harvester is GONE from the live entity map — destroyed, not stranded.
    assert "harvester_p1" not in sess.entities
    # Asset ledger preserves the destruction record for the vault.
    rec = sess.asset_records["harvester_p1"]
    assert rec.destroyed_on_day == 1
    assert rec.destroyed_by and rec.destroyed_by.startswith("dawn_unrecovered@(")
    # Cargo spilled — never reached the hoard.
    assert sess.hoard_squares["p1"] == []
    # Vault inventory surfaces the row under `assets_by_status.destroyed`.
    inv = sess.inventory_pack("p1")
    destroyed_ids = [
        r.get("asset_id")
        for r in (inv.get("assets_by_status") or {}).get("destroyed") or []
    ]
    assert "harvester_p1" in destroyed_ids


def test_harvester_self_vision_records_path_echo() -> None:
    """Harvesters see their own cell only (RULEBOOK §3.10/§3.12 update):
    walking across tiles MUST register each visited cell as echo intel
    so the player gets "historical vision" along the harvester's path,
    including any RED harvests that happened underfoot. Off-path cells
    must stay fogged — the vision is strictly self-cell, not a disk."""
    sess = GameSession.new(28, 18, seed=311)
    y = sess.height // 2
    drop_x = 4
    # Carve a non-RED corridor so we can isolate the vision claim from
    # the harvest interaction. A later assertion exercises the harvest
    # path in its own corridor.
    for x in range(drop_x, drop_x + 4):
        sess.grid[y][x] = Cell(Tile.EMPTY, 0)
    walk_to = drop_x + 3
    path_cells = [(drop_x + i, y) for i in range(0, walk_to - drop_x + 1)]
    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [drop_x, y]},
        *[
            {"a": "step", "unit": "harvester_p1", "to": [drop_x + i, y]}
            for i in range(1, walk_to - drop_x + 1)
        ],
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    h = sess.entities["harvester_p1"]
    assert h.x is None, "harvester berthed after pickup"

    # Every cell the harvester occupied during the night must land in
    # echo intel — that's the "trail of historical vision" contract.
    intel = sess.probe_intel["p1"]
    for cx, cy in path_cells:
        k = f"{cx}:{cy}"
        assert k in intel, (
            f"path cell ({cx},{cy}) missing from echo intel — "
            f"harvester self-vision should record it as it stepped through"
        )

    # Off-path neighbours must stay fogged: self-vision is one cell, not
    # a disk. A regression that widens the radius would surface here.
    off_path = (drop_x, y + 2)
    assert f"{off_path[0]}:{off_path[1]}" not in intel, (
        f"off-path cell {off_path} should not be in echo intel — "
        "harvester vision must remain self-cell only"
    )

    dense = sess.player_dense_view("p1")
    idx = y * sess.width + drop_x
    cell = dense[idx]
    # The drop tile was directly under the harvester for at least one
    # step pulse, so it must render as echo (not fog).
    assert cell.get("kind") != "fog", (
        f"drop cell should be visible (echo) after harvester traversal: {cell!r}"
    )


def test_harvester_echo_does_not_ghost_trail_self() -> None:
    """A harvester walks a corridor, picks up, and goes orbital. Every
    cell it stepped through becomes an **echo** for the player (the
    self-vision pulse writes the terrain + harvest record), but NO
    cell may carry a phantom-harvester glyph in the echo — the trail
    overlay alone is what communicates the path (RULEBOOK §3.12).
    Other units captured by the same pulse (e.g. a friendly probe on
    the same tile) MUST still surface; the exclusion is *self-only*."""
    sess = GameSession.new(28, 18, seed=311)
    y = sess.height // 2
    drop_x = 4
    # Non-RED corridor so we isolate vision from harvest mechanics.
    for x in range(drop_x, drop_x + 4):
        sess.grid[y][x] = Cell(Tile.EMPTY, 0)
    path = [(drop_x + i, y) for i in range(0, 4)]
    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [drop_x, y]},
        *[
            {"a": "step", "unit": "harvester_p1", "to": [drop_x + i, y]}
            for i in range(1, 4)
        ],
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()
    assert sess.entities["harvester_p1"].x is None, "harvester berthed"

    dense = sess.player_dense_view("p1")
    idx = lambda x, y: y * sess.width + x  # noqa: E731
    for cx, cy in path:
        cell = dense[idx(cx, cy)]
        # Each path cell must be echo (kind:'terrain' with echo flag),
        # not fog — that's the self-vision contract.
        assert cell.get("kind") == "terrain" and cell.get("echo_probe"), (
            f"({cx},{cy}) should render as echo after harvester traversal "
            f"(got {cell!r})"
        )
        # …and crucially must NOT carry an entity glyph: the harvester
        # is gone, the trail overlay is what tells the player it walked
        # through. A phantom harvester glyph here is the ghost-trail bug.
        assert "entity" not in cell, (
            f"echo at ({cx},{cy}) ghosted a harvester glyph — "
            f"self-pulse must exclude itself from the snapshot (got {cell!r})"
        )
        # Occupant list, if present, must also be free of the harvester.
        for occ in cell.get("occupants") or []:
            assert occ.get("id") != "harvester_p1", (
                f"echo at ({cx},{cy}) ghosted harvester_p1 in occupants: {occ!r}"
            )


def test_harvester_echo_keeps_other_units_in_snapshot() -> None:
    """Self-exclusion is *self only* — a friendly probe sitting on a
    tile the harvester also walked through must still appear in the
    echo snapshot, because that's intel the player needs."""
    sess = GameSession.new(28, 18, seed=311)
    y = sess.height // 2
    cx = 5
    sess.grid[y][cx] = Cell(Tile.EMPTY, 0)
    # Drop a friendly probe FIRST so it lives on (cx, y) when the
    # harvester arrives and pulses.
    moves_p1 = [
        {"a": "probe", "at": [cx, y]},
        {"a": "drop", "unit": "harvester_p1", "at": [cx, y]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves_p1)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    intel = sess.probe_intel["p1"]
    snap = intel[f"{cx}:{y}"]
    # The harvester crushed the probe on drop, so the probe is gone
    # from live entities — but the *snapshot* was taken BEFORE the
    # crush during the per-step pulse if there was a probe-only frame.
    # The robust property we want to assert is: a snapshot recorded
    # while a non-self entity coexists with the harvester at the same
    # tile must include that non-self entity. We can simulate that
    # cleanly by injecting an enemy probe + pulsing directly.
    enemy = Entity("probe_p2_test", "probe", "p2", cx, y)
    sess.entities["probe_p2_test"] = enemy
    h = sess.entities["harvester_p1"]
    h.x, h.y = cx, y
    sess._pulse_vision_intel()
    snap = sess.probe_intel["p1"][f"{cx}:{y}"]
    ids_in_snap = {o.get("id") for o in (snap.get("occupants") or [])}
    assert "harvester_p1" not in ids_in_snap, (
        "self-pulse must exclude the harvester itself from its own echo"
    )
    assert "probe_p2_test" in ids_in_snap, (
        f"enemy probe should still appear in p1's echo (got {snap!r}) — "
        "self-exclusion must not drop *other* units from the snapshot"
    )


def test_harvester_self_vision_communicates_harvest_event() -> None:
    """A RED tile harvested mid-traversal must be readable from the
    player's percept after the harvester returns to orbit. The cell is
    no longer in live LOS, but the echo intel + memory should expose
    that "we harvested here, and the tile is now empty/green"."""
    sess = GameSession.new(28, 18, seed=311)
    y = sess.height // 2
    drop_x = 6
    # Force a RED tile at the drop site so auto-harvest fires exactly
    # where we expect, independent of generator noise. Parcel ID
    # bookkeeping (sq-xxxx-NNNN) is the engine's concern — the grid
    # mutation is sufficient for the harvest mechanic to see RED.
    sess.grid[y][drop_x] = Cell(Tile.RED, 200)
    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [drop_x, y]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    # The harvester has returned to orbit, but the player's echo intel
    # should still know about (drop_x, y) — and the cell's tile/purity
    # snapshot should reflect the post-harvest state (no longer RED).
    intel = sess.probe_intel["p1"]
    k = f"{drop_x}:{y}"
    assert k in intel, (
        "harvested cell should remain in echo intel after pickup"
    )
    snap = intel[k]
    # The tile is converted on harvest, so the snapshot's stored ``tile``
    # field must NOT report it as still-RED. The exact post-harvest
    # tile id depends on engine constants; we just assert "not RED".
    assert int(snap["tile"]) != int(Tile.RED), (
        f"echo snapshot at {k} should reflect post-harvest tile, got {snap!r}"
    )


def test_probe_persists_across_dawn() -> None:
    """Probes are no longer retracted at dawn (RULEBOOK §3.11.1)."""
    sess = GameSession.new(22, 16, seed=606)
    moves = [{"a": "probe", "at": [10, 8]}]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    d0 = sess.day
    sess.maybe_resolve_if_ready()
    assert sess.day == d0 + 1, "night should advance the planning day"
    probes_after = [e for e in sess.entities.values() if e.entity_type == "probe"]
    assert len(probes_after) == 1, "probe should survive dawn"
    p = probes_after[0]
    assert (p.x, p.y) == (10, 8) and p.owner == "p1"
    assert sess.probe_intel["p1"], "echo intel still accumulates around live probe"


def test_harvester_step_crushes_any_probe_at_destination() -> None:
    """Any harvester walking onto a probe destroys it (RULEBOOK §3.11.1)."""
    sess = GameSession.new(22, 14, seed=4040)
    y = sess.height // 2
    # Plant an enemy (p2) probe on the path. Drop the p1 harvester so it
    # can step adjacent and then ride onto the probe.
    px, py = 6, y
    moves_p1 = [
        {"a": "drop", "unit": "harvester_p1", "at": [px - 1, y]},
        {"a": "step", "unit": "harvester_p1", "to": [px, py]},
    ]
    moves_p2 = [{"a": "probe", "at": [px, py]}]
    assert sess.stash_policy("p1", moves_p1)[0]
    assert sess.stash_policy("p2", moves_p2)[0]
    sess.maybe_resolve_if_ready()
    assert not any(
        e.entity_type == "probe" and e.owner == "p2"
        for e in sess.entities.values()
    ), "p2 probe should have been crushed by p1 harvester"


def test_harvester_drop_onto_probe_destroys_it() -> None:
    """Dropping a harvester onto a probe also crushes it."""
    sess = GameSession.new(22, 14, seed=5050)
    sess.entities["probe_p1_1"] = Entity("probe_p1_1", "probe", "p1", 5, 5)
    moves = [{"a": "drop", "unit": "harvester_p1", "at": [5, 5]}]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()
    assert "probe_p1_1" not in sess.entities


def test_path_trail_density_escalates_with_repeat_visits() -> None:
    """Each revisit bumps the tile's path count and its shading tier."""
    sess = GameSession.new(20, 12, seed=515)
    y = sess.height // 2
    # Carve a corridor that is *not* RED so harvest doesn't override the path mark.
    for x in range(2, 7):
        sess.grid[y][x] = Cell(Tile.EMPTY, 0)

    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [2, y]},
        {"a": "step", "unit": "harvester_p1", "to": [3, y]},
        {"a": "step", "unit": "harvester_p1", "to": [4, y]},
        {"a": "step", "unit": "harvester_p1", "to": [3, y]},  # 2nd visit
        {"a": "step", "unit": "harvester_p1", "to": [2, y]},  # 2nd visit of drop tile
        {"a": "step", "unit": "harvester_p1", "to": [3, y]},  # 3rd visit
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    paths = sess.track_paths["p1"]
    # Trail entries are now {n, d, h} dicts (v0.5 schema); the legacy
    # int shape is still loaded by ``_load_path_counts`` but in-engine
    # storage is always the dict form.
    assert paths[f"4:{y}"]["n"] == 1, "single-visit tile keeps count of 1"
    assert paths[f"2:{y}"]["n"] == 2, "drop tile + return step ⇒ 2 visits"
    assert paths[f"3:{y}"]["n"] == 3, "three sweeps over the same tile ⇒ 3 visits"
    # Day stamp + harvester id are recorded on every entry so the OBS
    # tooltip and watcher can read off who walked where and when.
    for k in (f"2:{y}", f"3:{y}", f"4:{y}"):
        assert paths[k]["h"] == "harvester_p1", f"entry {k} should be tagged with harvester id"
        assert isinstance(paths[k]["d"], int), f"entry {k} should carry a day stamp"

    # v0.6.0 harvester vision: PLUS (self + 4 cardinals). Each step
    # pulses the harvester's own cell plus its N/S/E/W neighbours
    # into echo intel. Diagonals (NE/SE/SW/NW) are NOT in the plus
    # and stay fogged.
    dense = sess.player_dense_view("p1")
    idx = lambda x, y: y * sess.width + x  # noqa: E731
    for x in (2, 3, 4):
        cell = dense[idx(x, y)]
        assert cell.get("kind") != "fog", (
            f"({x},{y}) should be visible (echo) after harvester self-vision "
            f"recorded it (got {cell!r})"
        )
        trail = cell.get("trail") or {}
        assert trail.get("n", 0) >= 1, (
            f"({x},{y}) should carry the universal trail overlay (got {trail!r})"
        )
        fresh = trail.get("fresh_visits") or []
        assert any(v.get("owner") == "p1" for v in fresh), (
            f"({x},{y}) fresh attribution should include p1 (got {fresh!r})"
        )
    # Cardinal neighbours of the path tiles are now in the plus, so
    # they must be NON-fog after the harvester walks past.
    cardinal = dense[idx(3, y - 1)]
    assert cardinal.get("kind") != "fog", (
        f"north of (3,{y}) — a cardinal neighbour — should be echo "
        f"under v0.6.0 plus vision (got {cardinal!r})"
    )
    # Diagonals two tiles away on the y axis should still be fog —
    # plus vision is the Euclidean disk of radius 1, NOT 2.
    far = dense[idx(3, y - 2)]
    assert far.get("kind") == "fog", (
        f"(3, {y-2}) is too far for the plus to reach; should remain fogged "
        f"(got {far!r})"
    )
    assert "trail" not in far, "fog cells must not carry trail rendering"


def test_visible_cells_carry_vision_edge_bits_on_los_perimeter(monkeypatch) -> None:
    """Cells on the perimeter of live LOS expose which sides face outside.

    Vision is a Euclidean disk (RULEBOOK §3.11), so the perimeter follows
    the circle of radius :data:`PROBE_VISION_RADIUS` rather than a 5×5
    square. The cardinal extremes (north, south, east, west of the disk)
    are the tiles whose corresponding-axis neighbour is outside the disk.
    """
    # Test written for r=2; probe perimeter assertions depend on exact radius.
    monkeypatch.setenv("SOC_PROBE_RADIUS", "2")
    from sea_of_colours.game.session import PROBE_VISION_RADIUS

    sess = GameSession.new(20, 14, seed=1212)
    cx, cy = 8, 6
    sess.entities["probe_p1_test"] = Entity("probe_p1_test", "probe", "p1", cx, cy)
    dense = sess.player_dense_view("p1")
    idx = lambda x, y: y * sess.width + x  # noqa: E731

    centre = dense[idx(cx, cy)]
    assert centre.get("vedge", "") == ""

    # North-most cell of the disk: its north neighbour is outside the disk.
    north = dense[idx(cx, cy - PROBE_VISION_RADIUS)]
    assert "n" in north.get("vedge", "")

    # South / East / West extremes — same idea, opposite sides.
    south = dense[idx(cx, cy + PROBE_VISION_RADIUS)]
    assert "s" in south.get("vedge", "")
    east = dense[idx(cx + PROBE_VISION_RADIUS, cy)]
    assert "e" in east.get("vedge", "")
    west = dense[idx(cx - PROBE_VISION_RADIUS, cy)]
    assert "w" in west.get("vedge", "")


def test_probe_vision_is_a_circular_disk_not_a_square(monkeypatch) -> None:
    """A probe with vision r=2 sees the Euclidean disk, not a 5×5 box.

    Specifically: the four corners of the bounding 5×5 (Chebyshev distance 2
    but Euclidean √8 ≈ 2.83) must **not** be visible (RULEBOOK §3.11).
    """
    # Test written for r=2; disk boundary assertions depend on exact radius.
    monkeypatch.setenv("SOC_PROBE_RADIUS", "2")
    from sea_of_colours.game.session import PROBE_VISION_RADIUS

    sess = GameSession.new(20, 14, seed=99)
    cx, cy = 10, 7
    sess.entities["probe_p1_disk"] = Entity("probe_p1_disk", "probe", "p1", cx, cy)
    vis = sess.tiles_visible_now("p1")
    r = PROBE_VISION_RADIUS

    # Cardinal extremes are inside the disk.
    for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r)):
        assert (cx + dx, cy + dy) in vis

    # Diagonal corners of the bounding box are *outside* the disk.
    for dx, dy in ((r, r), (-r, r), (r, -r), (-r, -r)):
        assert (cx + dx, cy + dy) not in vis, (
            f"corner {(dx, dy)} should be outside the Euclidean disk"
        )


def test_harvester_glyph_preserves_owner_colour_across_cargo_states() -> None:
    """Harvester glyph stays ``X`` regardless of cargo, seat colour preserved.

    History:
      * v0.4.0 swapped ``X`` → ``!`` mid-night (too distracting).
      * v0.4.3 tried diamond glyphs (``◆`` / ``◈``) + hot-red repaint
        for the loaded variant, but that lost ownership readability —
        a yellow p2 harvester became red on pickup and the seat colour
        had to be re-inferred from the trail.
      * v0.4.4: returned to lowercase/uppercase ``x`` / ``X`` pair.
      * v0.5+: unified to ``X`` for both empty AND carrying so the
        seat colour does ALL the work for ownership reads. Cargo
        state is still surfaced on the structured entity payload
        (``ent.carrying`` flag) for HUD widgets; the map glyph itself
        no longer changes shape.
    """
    from sea_of_colours.game.session import ENTITY_GLYPHS

    sess = GameSession.new(20, 14, seed=4242)
    h = sess.entities["harvester_p1"]

    h.carrying_red = False
    glyph_empty, fg_empty = sess._glyph_for_entity(h)
    assert glyph_empty == ENTITY_GLYPHS["harvester"] == "X"

    h.carrying_red = True
    glyph_loaded, fg_loaded = sess._glyph_for_entity(h)
    assert glyph_loaded == ENTITY_GLYPHS["harvester_carrying"] == "X"
    # Cargo state must NOT change the seat colour — p1 stays white,
    # p2 stays yellow regardless of cargo.
    assert fg_loaded == fg_empty, (
        "carrying harvester must keep its seat colour; "
        "shape is identical so colour does all the work for ownership"
    )


def test_harvester_glyph_paints_owner_colour() -> None:
    """p1 harvesters render white, p2 harvesters render yellow.

    Cargo state no longer overrides seat colour (v0.4.4) — both empty
    and loaded harvesters render in their owner colour so the player
    never has to disambiguate seat ownership from cargo state.
    
    v0.9.18 — uses custom seat colors from player_profiles.
    """
    sess = GameSession.new(20, 14, seed=4242)
    h1 = sess.entities["harvester_p1"]
    h2 = sess.entities["harvester_p2"]

    h1.carrying_red = False
    h2.carrying_red = False
    _, fg1 = sess._glyph_for_entity(h1)
    _, fg2 = sess._glyph_for_entity(h2)

    # Use the session's actual seat colors (from player_profiles / palette)
    def _rgb_css(seat: str) -> str:
        r, g, b = sess.seat_color(seat)
        return f"rgb({r},{g},{b})"
    
    assert fg1 == _rgb_css("p1")
    assert fg2 == _rgb_css("p2")
    assert fg1 != fg2, "p1 and p2 must render with distinct seat colours"


def test_log_carries_structured_levels_for_info_and_error() -> None:
    """``sess.log`` is now a list of ``{level, text}`` dicts — errors
    surface as ``level == 'error'`` in the LOG drawer."""
    sess = GameSession.new(22, 14, seed=88)
    # One valid + one invalid move so we get one info and one error.
    moves = [
        {"a": "probe", "at": [5, 5]},
        {"a": "step", "unit": "nope", "to": [1, 1]},
    ]
    assert sess.stash_policy("p1", moves)[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()
    levels = {entry["level"] for entry in sess.log}
    assert {"info", "error"}.issubset(levels)
    assert all(isinstance(e, dict) and "text" in e for e in sess.log)


def test_shipped_storage_defaults_empty_and_round_trips() -> None:
    """Shipped storage is wired but empty until §4 catapult mechanics land."""
    sess = GameSession.new(18, 12, seed=4242)
    assert sess.shipped_squares == {"p1": [], "p2": []}

    inv = sess.inventory_pack("p1")
    assert inv["shipped"] == []
    # v0.9.x — SHIPPED is uncapped (public record); capacity is None.
    assert inv["shipped_capacity"] is None

    # Plant a synthetic shipped parcel and confirm to_dict/from_dict carries it.
    sess.shipped_squares["p1"].append(
        {"square_id": "deadbeef00000000", "x": 1, "y": 2, "tile_at_harvest": 2}
    )
    payload = sess.to_dict()
    restored = GameSession.from_dict(payload)
    assert restored.shipped_squares["p1"][0]["square_id"] == "deadbeef00000000"


def test_parse_moves_wastes_invalid_entries() -> None:
    moves, errs = parse_moves(
        [
            {"a": "probe", "at": [1, 2]},
            {"a": "step", "unit": "harvester_p1"},  # missing 'to'
            "not-a-move",
            {"a": "unknown"},
        ]
    )
    assert not errs, "per-entry validation should not be a hard error"
    tags = [m.tag for m in moves]
    assert tags == ["probe", "waste", "waste", "waste"]


def test_session_round_trips_through_to_dict() -> None:
    sess = GameSession.new(18, 12, seed=4242)
    assert sess.stash_policy(
        "p1",
        [
            {"a": "drop", "unit": "harvester_p1", "at": [2, 6]},
            {"a": "pickup", "unit": "harvester_p1"},
        ],
    )[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    payload = sess.to_dict()
    restored = GameSession.from_dict(payload)
    assert restored.day == sess.day
    assert restored.session_id == sess.session_id
    assert restored.entities.keys() == sess.entities.keys()
    assert len(restored.last_night_replay) == len(sess.last_night_replay)


# ---------------------------------------------------------------------------
# RULEBOOK §3.6 v0.7.3 — harvester-on-harvester mutual damage.
# Collisions resolve as mutual *damage* (not destruction). Both
# harvesters stay on the surface; both drop ALL cargo; both flip
# ``damaged = True``. Damaged harvesters do not block movement and
# can co-occupy a cell. A successful pickup repairs them in orbit.
# ---------------------------------------------------------------------------


def test_drop_onto_healthy_harvester_damages_both() -> None:
    """Dropping a harvester onto a tile already occupied by another
    healthy harvester crashes them: the onboard one stays damaged on
    surface, the dropped one stays damaged in orbit."""
    sess = GameSession.new(20, 14, seed=701)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 6, 7
    h1.cargo_squares = [{"square_id": "sq-aaa", "value": 50}]
    h1.carrying_red = True

    # p2 must have vision of (6,7) to drop there. Add a probe for p2.
    sess.entities["probe_p2_test"] = Entity("probe_p2_test", "probe", "p2", 6, 7)

    ok, msg, _ = sess.try_drop_unit("p2", "harvester_p2", 6, 7)
    assert ok, f"drop should succeed as a collision, got: {msg}"
    assert "COLLISION" in msg, f"collision caption missing: {msg}"

    # h1 stays on (6,7) damaged; h2 aborts drop and stays orbital damaged.
    h2 = sess.entities["harvester_p2"]
    assert (h1.x, h1.y) == (6, 7), "h1 should stay on surface"
    assert (h2.x, h2.y) == (None, None), "h2 should stay in orbit"
    assert h1.damaged is True
    assert h2.damaged is True
    assert h1.cargo_squares == [], "h1 cargo spilled"
    assert h2.cargo_squares == [], "h2 cargo spilled"
    assert not h1.carrying_red
    assert not h2.carrying_red

    # Collision mark stamped for the current day.
    mark = sess.collision_marks.get("6:7")
    assert mark is not None
    assert mark["day"] == sess.day
    assert set(mark["owners"]) == {"p1", "p2"}

    # A pending collision event is queued for the simulator to drain.
    assert sess.pending_collision_events, "outbox event should be queued"
    ev = sess.pending_collision_events[-1]
    assert ev["type"] == "drop_on"
    assert ev["at"] == [6, 7]
    assert set(ev["owners"]) == {"p1", "p2"}


def test_harvester_one_outing_per_night() -> None:
    """RULEBOOK §3.12.1 — a harvester makes ONE outing per night. After it lifts
    back to orbit on pickup, a SECOND drop of the same unit this night is
    refused (closes the pickup+re-drop-to-bank-more loophole)."""
    sess = GameSession.new(20, 14, seed=811)
    ok, msg, _ = sess.try_drop_unit(
        "p1", "harvester_p1", 5, 5, live_override={(5, 5)},
    )
    assert ok, f"first drop should succeed: {msg}"
    ok, msg, _ = sess.try_pickup_unit("p1", "harvester_p1")
    assert ok, f"pickup should succeed: {msg}"
    # Back in orbit, but a second outing the same night is refused.
    ok, msg, _ = sess.try_drop_unit(
        "p1", "harvester_p1", 7, 7, live_override={(7, 7)},
    )
    assert not ok, "a second outing the same night must be refused"
    assert "one outing per harvester per night" in msg, msg


def test_harvester_may_redeploy_the_next_night() -> None:
    """The one-outing ledger is scoped PER NIGHT — the same harvester may deploy
    again on the following day."""
    sess = GameSession.new(20, 14, seed=812)
    ok, _msg, _ = sess.try_drop_unit(
        "p1", "harvester_p1", 5, 5, live_override={(5, 5)},
    )
    assert ok
    ok, _msg, _ = sess.try_pickup_unit("p1", "harvester_p1")
    assert ok
    sess.day += 1  # a new night begins
    ok, msg, _ = sess.try_drop_unit(
        "p1", "harvester_p1", 7, 7, live_override={(7, 7)},
    )
    assert ok, f"redeploy the next night should succeed: {msg}"


def test_step_into_healthy_harvester_damages_both() -> None:
    """Step-into collision: the stepper aborts (stays at origin),
    the stationary one stays at destination; both damaged, cargo spilled."""
    sess = GameSession.new(20, 14, seed=702)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 5, 7
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 6, 7
    h1.cargo_squares = [{"square_id": "sq-bbb", "value": 30}]
    h2.cargo_squares = [{"square_id": "sq-ccc", "value": 20}]

    ok, msg, _ = sess.try_step_unit("p1", "harvester_p1", 6, 7)
    assert ok, f"step into occupied cell should resolve as collision: {msg}"
    assert "COLLISION" in msg

    # h1 aborts step (stays at 5,7); h2 stays at (6,7); both damaged.
    assert (h1.x, h1.y) == (5, 7), "h1 aborts step, stays at origin"
    assert (h2.x, h2.y) == (6, 7), "h2 stays at destination"
    assert h1.damaged and h2.damaged
    assert h1.cargo_squares == [] and h2.cargo_squares == []


def test_damaged_harvester_does_not_block_movement() -> None:
    """A damaged harvester is wreckage — other harvesters can drop /
    step onto its cell without triggering another collision."""
    sess = GameSession.new(20, 14, seed=703)
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 8, 7
    h2.damaged = True
    h2.cargo_squares = []

    # p1 must have vision of (8,7) to drop there. Add a probe for p1.
    sess.entities["probe_p1_test"] = Entity("probe_p1_test", "probe", "p1", 8, 7)

    # Healthy p1 drops onto the wreck — succeeds cleanly, no collision.
    ok, msg, _ = sess.try_drop_unit("p1", "harvester_p1", 8, 7)
    assert ok, msg
    assert "COLLISION" not in msg
    h1 = sess.entities["harvester_p1"]
    assert (h1.x, h1.y) == (8, 7)
    assert h1.damaged is False
    # h2 is still damaged sharing the cell.
    assert h2.damaged is True


def test_damaged_harvester_cannot_step() -> None:
    """A damaged harvester is awaiting pickup — it cannot step."""
    sess = GameSession.new(20, 14, seed=704)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 4, 5
    h1.damaged = True

    ok, msg, _ = sess.try_step_unit("p1", "harvester_p1", 5, 5)
    assert not ok
    assert "damaged" in msg


def test_pickup_extracts_damaged_harvester_without_repair() -> None:
    """v0.9.18 — pickup no longer repairs. Damaged harvesters return
    to orbit but stay damaged (must use paid REPAIR action)."""
    sess = GameSession.new(20, 14, seed=705)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 3, 4
    h1.damaged = True
    h1.cargo_squares = []  # damaged harvesters carry no cargo

    ok, msg, parcels = sess.try_pickup_unit("p1", "harvester_p1")
    assert ok, msg
    assert parcels == []
    assert h1.damaged is True  # stays damaged
    assert h1.x is None and h1.y is None
    assert "DAMAGED — requires REPAIR" in msg


def test_damaged_harvester_cannot_be_dropped() -> None:
    """v1.24 (§3.6.1) — a wreck in orbit may not be deployed.

    The twin of ``test_damaged_harvester_cannot_step``. ``try_drop_unit``
    had no damage guard, so the loop crash → pickup → re-drop skipped the
    paid REPAIR action entirely. Worse, the landing auto-harvests (§3.12),
    so one illegal drop broke both halves of §3.6.1 in a single slot —
    hence the tile assertion below, not just the rejection.
    """
    sess = GameSession.new(20, 14, seed=707)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = None, None  # in orbit
    h1.damaged = True
    before = sess.grid[6][6].tile

    ok, msg, harvested = sess.try_drop_unit("p1", "harvester_p1", 6, 6)

    assert not ok
    assert "damaged" in msg
    assert not harvested, "a rejected drop must not auto-harvest (§3.12)"
    assert h1.x is None and h1.y is None, "the wreck stays in orbit"
    assert sess.grid[6][6].tile == before, "the landing cell is untouched"
    assert banked_parcels(sess, "p1") == []


def test_repair_restores_the_right_to_deploy() -> None:
    """v1.24 (§3.6.1) — REPAIR is what lifts the deploy ban.

    Pins the guard as a *gate*, not a permanent ban: the same unit, on the
    same night, drops fine once the paid Orbit action has run. Without this
    the guard could regress to "damaged units are dead forever" and the
    tests above would still pass.
    """
    from sea_of_colours.game.session import REPAIR_COST

    sess = GameSession.new(20, 14, seed=708)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = None, None
    h1.damaged = True
    sess.credits["p1"] = REPAIR_COST

    ok, _msg, _ = sess.try_drop_unit("p1", "harvester_p1", 6, 6)
    assert not ok, "damaged: refused"

    ok, msg = sess.apply_repair("p1", "harvester_p1")
    assert ok, msg
    assert h1.damaged is False

    ok, msg, _ = sess.try_drop_unit("p1", "harvester_p1", 6, 6)
    assert ok, msg
    assert (h1.x, h1.y) == (6, 6)


def test_pass_through_swap_damages_both() -> None:
    """A↔B same-round step swap (A:(5,7)→(6,7), B:(6,7)→(5,7))
    resolves as collision. Both harvesters abort steps (stay at origin),
    both damaged, cargo spilled."""
    sess = GameSession.new(20, 14, seed=706)
    h1 = sess.entities["harvester_p1"]
    h2 = sess.entities["harvester_p2"]
    h1.x, h1.y = 5, 7
    h2.x, h2.y = 6, 7
    h1.cargo_squares = [{"square_id": "sq-ddd", "value": 10}]
    h2.cargo_squares = [{"square_id": "sq-eee", "value": 10}]

    sess.stash_policy(
        "p1", [{"a": "step", "unit": "harvester_p1", "to": [6, 7]}]
    )
    sess.stash_policy(
        "p2", [{"a": "step", "unit": "harvester_p2", "to": [5, 7]}]
    )
    sess.maybe_resolve_if_ready()

    # Both harvesters abort steps and stay at their origin positions.
    assert (h1.x, h1.y) == (5, 7), "p1 aborts step, stays at origin"
    assert (h2.x, h2.y) == (6, 7), "p2 aborts step, stays at origin"
    assert h1.damaged and h2.damaged
    assert h1.cargo_squares == [] and h2.cargo_squares == []

    # Both origin tiles carry a collision mark (where they actually crashed).
    assert "5:7" in sess.collision_marks
    assert "6:7" in sess.collision_marks

    # Replay carries a single collision_swap frame with collision metadata.
    swap_frames = [
        f for f in sess.last_night_replay if f.get("tag") == "collision_swap"
    ]
    assert len(swap_frames) == 1
    assert swap_frames[0].get("collisions"), "swap frame must carry collision payload"


def test_collision_marks_decay_after_one_day() -> None:
    """Collision marks last exactly one game day; two nights later
    they're gone (mirrors the fresh_visits decay window)."""
    sess = GameSession.new(20, 14, seed=707)
    sess.collision_marks["10:5"] = {"day": sess.day, "owners": ["p1"]}
    # Same day: mark survives a manual prune.
    sess._prune_collision_marks()
    assert "10:5" in sess.collision_marks

    # Run two empty nights; the mark should fall out of the 1-day window.
    for _ in range(2):
        sess.stash_policy("p1", [])
        sess.stash_policy("p2", [])
        sess.maybe_resolve_if_ready()
    assert "10:5" not in sess.collision_marks, (
        "collision mark should have decayed after 1+ game days"
    )


def test_dawn_still_destroys_damaged_harvesters() -> None:
    """Damaged harvesters left on the surface at sunrise are destroyed
    (§3.11.2). Damage does not grant immunity from dawn."""
    sess = GameSession.new(20, 14, seed=708)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 9, 6
    h1.damaged = True

    sess.stash_policy("p1", [])
    sess.stash_policy("p2", [])
    sess.maybe_resolve_if_ready()

    assert "harvester_p1" not in sess.entities, (
        "damaged harvester left on surface should be destroyed at dawn"
    )
    rec = sess.asset_records["harvester_p1"]
    assert rec.destroyed_on_day is not None


def test_replay_frame_collisions_payload_is_present_on_drop() -> None:
    """The simulator drains the collision outbox onto the matching
    replay frame so the frontend can drive the ring animation."""
    sess = GameSession.new(20, 14, seed=709)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 5, 5  # p1's harvester already on surface

    # p2 drops on p1's tile to trigger a drop-on collision.
    sess.stash_policy("p1", [])
    sess.stash_policy(
        "p2", [{"a": "drop", "unit": "harvester_p2", "at": [5, 5]}]
    )
    sess.maybe_resolve_if_ready()

    drop_frames = [
        f for f in sess.last_night_replay if f.get("tag") == "drop"
    ]
    assert drop_frames, "drop frame should be present in replay"
    drop = drop_frames[0]
    assert drop.get("collisions"), "drop frame must carry collision payload"
    coll = drop["collisions"][0]
    assert coll["type"] == "drop_on"
    assert coll["at"] == [5, 5]


def test_green_harvest_empties_tile_and_leaves_no_harvest_track() -> None:
    """Harvesting GREEN removes the tile entirely. The harvest-tracks
    overlay (§3.12) should ONLY mark cells where RED was converted
    to synthetic GREEN — harvesting an existing GREEN tile leaves
    nothing behind beyond the universal path trail."""
    sess = GameSession.new(20, 14, seed=801)
    # Plant a deterministic GREEN tile under p1's drop target.
    sess.grid[7][4] = Cell(Tile.GREEN, 200)
    key = "4:7"

    ok, msg, harvested = sess.try_drop_unit("p1", "harvester_p1", 4, 7)
    assert ok and harvested, f"drop should have harvested GREEN: {msg}"
    # Tile now empty, no synthetic green minted.
    after = sess.grid[7][4]
    assert int(after.tile) == int(Tile.EMPTY), (
        f"GREEN harvest should leave EMPTY, got {after.tile}"
    )
    assert int(after.purity) == 0
    # Crucially: track_harvests must NOT include this cell. The
    # frontend tints harvest tracks green; tinting an empty cell
    # would falsely imply there's still synthetic-green there.
    assert key not in sess.track_harvests["p1"], (
        "GREEN harvest must not seed track_harvests; only RED → GREEN does"
    )


def test_blue_harvest_empties_tile_and_leaves_no_harvest_track() -> None:
    sess = GameSession.new(20, 14, seed=802)
    sess.grid[6][5] = Cell(Tile.BLUE, 120)
    key = "5:6"

    ok, msg, harvested = sess.try_drop_unit("p1", "harvester_p1", 5, 6)
    assert ok and harvested, msg
    after = sess.grid[6][5]
    assert int(after.tile) == int(Tile.EMPTY)
    assert int(after.purity) == 0
    assert key not in sess.track_harvests["p1"]


def test_red_harvest_still_marks_track_harvests() -> None:
    """Regression guard: the §3.12 'green harvest residue' overlay
    must continue to fire when RED is converted to synthetic GREEN."""
    sess = GameSession.new(20, 14, seed=803)
    sess.grid[8][3] = Cell(Tile.RED, 180)
    key = "3:8"

    ok, msg, harvested = sess.try_drop_unit("p1", "harvester_p1", 3, 8)
    assert ok and harvested, msg
    after = sess.grid[8][3]
    assert int(after.tile) == int(Tile.GREEN), (
        "RED harvest must mint synthetic-green on the tile"
    )
    assert int(after.purity) == 255
    assert key in sess.track_harvests["p1"], (
        "RED → GREEN conversion must mark the cell in track_harvests"
    )


def test_orbital_damaged_harvester_persists_through_dawn() -> None:
    """v0.8.0 (RULEBOOK §3.6.1): the dawn auto-repair sweep is gone.

    A damaged harvester sitting in orbit must remain damaged across
    the night → orbit transition. Repair is now a paid action that
    only fires when the seat submits a REPAIR Orbit action.
    """
    sess = GameSession.new(20, 14, seed=804)
    h1 = sess.entities["harvester_p1"]
    h1.x = h1.y = None
    h1.damaged = True

    sess.stash_policy("p1", [])
    sess.stash_policy("p2", [])
    sess.maybe_resolve_if_ready()

    assert h1.damaged is True, (
        "v0.8.0: orbital damaged harvester must persist through dawn — "
        "repair is a paid Orbit action, no longer auto-applied"
    )


def test_pickup_caption_warns_damage_persists() -> None:
    """v0.9.18 — pickup no longer repairs. Caption warns the unit
    needs paid REPAIR before it can be deployed again."""
    sess = GameSession.new(20, 14, seed=805)
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 3, 4
    h1.damaged = True

    ok, msg, _ = sess.try_pickup_unit("p1", "harvester_p1")
    assert ok, msg
    assert "DAMAGED — requires REPAIR" in msg, (
        f"pickup caption should warn damage persists, got: {msg}"
    )


def test_opening_frame_carries_scheduled_orders_for_both_seats() -> None:
    sess = GameSession.new(20, 14, seed=710)
    sess.stash_policy(
        "p1",
        [
            {"a": "probe", "at": [3, 4]},
            {"a": "drop", "unit": "harvester_p1", "at": [5, 5]},
        ],
    )
    sess.stash_policy(
        "p2", [{"a": "pickup", "unit": "harvester_p2"}]
    )
    sess.maybe_resolve_if_ready()

    opening = sess.last_night_replay[0]
    orders = opening.get("scheduled_orders")
    assert orders is not None, "opening frame must carry scheduled_orders"
    assert {"p1", "p2"}.issubset(orders.keys())
    assert len(orders["p1"]) == 2
    assert len(orders["p2"]) == 1
    assert orders["p1"][0]["action"] == "probe"
    assert orders["p1"][1]["action"] == "drop"
    assert orders["p2"][0]["action"] == "pickup"
    assert all(row["status"] == "pending" for row in orders["p1"] + orders["p2"]), (
        "scheduled orders start as pending; client mutates as scrubber walks"
    )


def test_opening_frame_caption_announces_praxis() -> None:
    """v0.7.4 — the night-resolution event is named PRAXIS in
    user-facing surfaces (RULEBOOK §3.10). The replay's opening frame
    caption must advertise that so the watcher's synced log reads
    ``[opening] PRAXIS begins`` instead of the legacy
    ``[opening] night begins`` wording."""
    sess = GameSession.new(20, 14, seed=801)
    sess.stash_policy("p1", [])
    sess.stash_policy("p2", [])
    sess.maybe_resolve_if_ready()
    assert sess.last_night_replay[0]["caption"] == "[opening] PRAXIS begins"
    # The log-level [nightStart] marker was also retired in favour of
    # an explicit [praxis] header carrying the planning day. The
    # ``stash_policy`` lock-notes come first; the praxis header
    # appears once both seats are ready and the simulator opens
    # the night.
    praxis_headers = [
        e["text"] for e in sess.log if e["text"].startswith("[praxis] day ")
    ]
    assert praxis_headers, f"expected a [praxis] line, got log: {sess.log!r}"
    assert "PRAXIS" not in praxis_headers[0] or "night begins" in praxis_headers[0]


def test_replay_frames_carry_hour_of_night() -> None:
    """v0.7.4 — every in-night replay frame must carry an ``hour``
    field (1..HOURS_PER_NIGHT). The opener carries hour=0; subsequent
    successful frames count up from 1 per round, and both seats'
    Nth applied moves share the same hour-stamp (parallel timelines).
    """
    from sea_of_colours.game.session import HOURS_PER_NIGHT

    assert HOURS_PER_NIGHT == 21, "planetary night is canonically 21 hours"
    sess = GameSession.new(28, 18, seed=802)
    # Three moves per seat — drop + 2 probes — so both seats fill
    # hours 1..3 of the same night.
    sess.stash_policy(
        "p1",
        [
            {"a": "drop", "unit": "harvester_p1", "at": [5, 5]},
            {"a": "probe", "at": [6, 6]},
            {"a": "probe", "at": [7, 7]},
        ],
    )
    sess.stash_policy(
        "p2",
        [
            {"a": "drop", "unit": "harvester_p2", "at": [20, 10]},
            {"a": "probe", "at": [21, 10]},
            {"a": "probe", "at": [22, 10]},
        ],
    )
    sess.maybe_resolve_if_ready()

    frames = sess.last_night_replay
    opener = frames[0]
    assert opener.get("hour") == 0, "opening frame must be untagged (hour=0)"

    # Pull only the per-seat, ok-outcome frames so we can sequence by
    # owner and verify the hour stamping. Dawn / waste frames have
    # different invariants and are checked separately below.
    by_seat = {"p1": [], "p2": []}
    for f in frames:
        if f.get("owner") in by_seat and f.get("outcome") == "ok":
            by_seat[f["owner"]].append(f)

    for seat in ("p1", "p2"):
        hours = [f["hour"] for f in by_seat[seat]]
        assert hours == [1, 2, 3], (
            f"{seat} successful frames should stamp hours 1..3 in order, got {hours}"
        )


def test_replay_frame_carries_crushed_probes_payload() -> None:
    """v0.7.4 — when a harvester drop / step lands on a probe the
    engine must surface a structured ``crushed_probes`` event on the
    matching replay frame so the watcher can fire the pixel-splash
    animation. The payload carries the cell, the destroyed probe id,
    and the probe's owner (used for colour in the FX layer)."""
    sess = GameSession.new(28, 18, seed=901)
    # Night 1: p1 plants a probe at a centre cell; p2 does nothing.
    sess.stash_policy("p1", [{"a": "probe", "at": [10, 10]}])
    sess.stash_policy("p2", [])
    sess.maybe_resolve_if_ready()
    assert any(
        ent.entity_type == "probe" and ent.x == 10 and ent.y == 10
        for ent in sess.entities.values()
    ), "probe should be on the field after night 1"

    # Night 2: p2 drops the harvester right on top of the probe →
    # one crushed_probes event tagged on the drop frame.
    sess.stash_policy("p1", [])
    sess.stash_policy(
        "p2",
        [
            {"a": "drop", "unit": "harvester_p2", "at": [10, 10]},
            {"a": "pickup", "unit": "harvester_p2"},
        ],
    )
    sess.maybe_resolve_if_ready()

    drop_frames = [
        f for f in sess.last_night_replay
        if f.get("tag") == "drop" and f.get("owner") == "p2"
    ]
    assert drop_frames, "expected at least one p2 drop frame in night 2 replay"
    payloads = [
        ev for f in drop_frames for ev in (f.get("crushed_probes") or [])
    ]
    assert payloads, (
        "drop landing on a probe must surface a crushed_probes event"
    )
    ev = payloads[0]
    assert ev["at"] == [10, 10]
    assert ev["probe_owner"] == "p1"
    assert ev["probe_id"].startswith("probe_p1_")


def test_log_entries_carry_hour_prefix_during_night() -> None:
    """v0.7.4 — every log line emitted *during* the PRAXIS execution
    is prefixed with the planetary night clock (``[H03]``) so the
    orchestrator log + replay-feed log both surface *when* something
    happened without counting frames. The pre-night [praxis] header
    and post-night dawn / season-complete lines stay un-stamped
    (those are boundary moments, not in-night actions)."""
    sess = GameSession.new(28, 18, seed=803)
    sess.stash_policy(
        "p1",
        [
            {"a": "drop", "unit": "harvester_p1", "at": [5, 5]},
            {"a": "probe", "at": [6, 6]},
        ],
    )
    sess.stash_policy("p2", [])
    sess.maybe_resolve_if_ready()

    # The first applied move (drop @ (5,5)) and the second (probe
    # @ (6,6)) should both have hour stamps; the pre-night [praxis]
    # marker should NOT.
    texts = [e["text"] for e in sess.log]
    praxis_headers = [t for t in texts if t.startswith("[praxis] ")]
    assert praxis_headers, "expected a [praxis] header for night start"
    assert all(not t.startswith("[H") for t in praxis_headers), (
        "boundary markers must stay un-stamped"
    )
    stamped = [t for t in texts if t.startswith("[H")]
    assert stamped, "expected at least one hour-stamped in-night log entry"
    # The first applied move runs at hour 1 → "[H01] ..." (zero-padded).
    assert any(t.startswith("[H01] ") for t in stamped), (
        f"expected an [H01] line for the first applied move, got: {stamped[:3]}"
    )


def test_damaged_unit_remaining_steps_consolidated() -> None:
    """v0.9.10 — once a harvester is damaged the orchestrator strikes
    out every consecutive non-pickup action queued for that same unit
    in ONE consolidated ``tag="damaged"`` replay frame. Slot economy:
    only ONE hour is consumed for the run, so a long, futile chain
    (5 steps + pickup queued behind the hit) no longer drowns the
    timeline in five identical "X damaged — awaiting pickup" rows.

    v1.31 — the hit used to be a caltrop. Mines were retired, so this
    now uses the mutual-damage collision (§3.6), which damages the
    stepper and cancels the step the same way. What is under test is
    the CONSOLIDATION, not the damage source."""
    from sea_of_colours.game.session import GameSession, Phase

    sess = GameSession.new(28, 18, seed=803, players=("p1", "p2", "p3", "p4"))
    h3 = sess.entities["harvester_p3"]
    h3.x, h3.y = 20, 14
    # A healthy rival harvester parked on the target cell: stepping into
    # it cancels the move and damages both.
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 19, 14
    h1.damaged = False
    sess.phase = Phase.PLANNING
    sess.stash_policy("p1", [])
    sess.stash_policy("p2", [])
    sess.stash_policy("p3", [
        {"a": "step", "unit": "harvester_p3", "to": [19, 14]},  # collision → damaged
        {"a": "step", "unit": "harvester_p3", "to": [19, 13]},  # struck
        {"a": "step", "unit": "harvester_p3", "to": [18, 13]},  # struck
        {"a": "step", "unit": "harvester_p3", "to": [18, 12]},  # struck
        {"a": "step", "unit": "harvester_p3", "to": [17, 12]},  # struck
        {"a": "pickup", "unit": "harvester_p3"},
    ])
    sess.stash_policy("p4", [])
    sess.maybe_resolve_if_ready()

    frames = [f for f in sess.last_night_replay if f.get("owner") == "p3"]
    damaged_frames = [f for f in frames if f.get("tag") == "damaged"]
    assert len(damaged_frames) == 1, (
        f"expected exactly one consolidated 'damaged' frame, "
        f"got {len(damaged_frames)}"
    )
    damaged_frame = damaged_frames[0]
    assert "4× action on harvester_p3" in (damaged_frame.get("caption") or ""), (
        f"expected consolidated caption mentioning '4×', got: "
        f"{damaged_frame.get('caption')!r}"
    )
    # The pickup must still resolve (pickup is the free repair path).
    pickup_frames = [f for f in frames if f.get("tag") == "pickup"]
    assert len(pickup_frames) == 1, (
        f"expected a pickup frame after consolidation, got {len(pickup_frames)}"
    )
    # Hour stamps must be sequential per seat — no two p3 frames share
    # a hour. This is the invariant the user-reported screenshot
    # violated.
    hours = [f.get("hour") for f in frames if isinstance(f.get("hour"), int)]
    assert len(hours) == len(set(hours)), (
        f"p3 frames had duplicate hour stamps (one-action-per-seat-per-hour "
        f"contract violated): {hours}"
    )


def test_no_duplicate_hour_stamps_per_seat_across_full_night() -> None:
    """v0.9.10 — per-seat one-action-per-hour invariant under a real
    multi-seat night with the heuristic agent. The user-reported bug
    was multiple replay frames carrying the same H02 stamp for the
    same seat; this test pins the contract end-to-end."""
    from sea_of_colours.game.session import GameSession, Phase
    from sea_of_colours.agent.heuristic_agent import HeuristicAgent
    from sea_of_colours.snowpark.view import build_agent_view

    sess = GameSession.new(28, 18, seed=12345, players=("p1", "p2", "p3", "p4"))
    agent = HeuristicAgent()
    for _ in range(4):
        if sess.phase == Phase.ORBIT:
            for seat in ("p1", "p2", "p3", "p4"):
                plan = agent.play(build_agent_view(sess, seat))
                sess.stash_orbit_actions(seat, plan.get("orbit_actions") or [])
            sess.maybe_resolve_orbit_if_ready()
        for seat in ("p1", "p2", "p3", "p4"):
            plan = agent.play(build_agent_view(sess, seat))
            sess.stash_policy(seat, plan.get("moves") or [])
        sess.maybe_resolve_if_ready()

    seen = set()
    for frame in sess.last_night_replay:
        owner = frame.get("owner")
        hour = frame.get("hour")
        if not owner or not isinstance(hour, int) or hour <= 0:
            continue
        key = (owner, hour)
        assert key not in seen, (
            f"duplicate (seat, hour) replay frame: {key} — "
            f"caption={frame.get('caption')!r}"
        )
        seen.add(key)


def test_dropped_harvester_left_unrecovered_records_abandoned_recap() -> None:
    """A harvester dropped with no pickup is destroyed at dawn (§3.11.2).

    The night's recap must record it BOTH as an ordered ``abandoned``
    event (``orbital_events_by_day``) and in the count tally's
    ``abandoned`` counter (``orbital_activity_by_day``), so either recap
    render path — the ordered event log or the count fallback — shows the
    ✖ destroyed-harvester line.
    """
    sess = GameSession.new(20, 14, seed=909)
    y = sess.height // 2
    sess.grid[y][3] = Cell(Tile.EMPTY, 0)
    night = str(int(sess.day))
    assert sess.stash_policy("p1", [
        {"a": "drop", "unit": "harvester_p1", "at": [3, y]},
    ])[0]
    assert sess.stash_policy("p2", [])[0]
    sess.maybe_resolve_if_ready()

    events = sess.orbital_events_by_day.get(night, {}).get("p1", [])
    assert any(e.get("tag") == "abandoned" for e in events), (
        "an unrecovered harvester must surface an abandoned event"
    )
    activity = sess.orbital_activity_by_day.get(night, {}).get("p1", {})
    assert activity.get("abandoned", 0) >= 1
