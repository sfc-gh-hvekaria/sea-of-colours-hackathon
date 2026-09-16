"""v0.7.0 regression tests — Magnetic Cover, orbital publicity, and
probe collision rules.

Pins the four big v0.7.0 changes so future refactors cannot regress
them without lighting up the test suite:

1. ``GameSession.agent_dense_view`` partitions cells into ``live`` /
   ``echo`` with ``fog_count`` for the agent's filter-out-fog
   contract, and surfaces logical fields (square_id, lineage, value).
2. ``last_night`` recap rolls SOC_GAME_LOG day-1 rows into structured
   ``my_orders`` (with ``outcome='illegal'`` + reason for rejected
   items), ``my_assets_destroyed``, and ``my_parcels_banked``.
3. ``hud.hoard`` carries a ``warning`` string naming the §3.14 tier
   ladder when the vault is ≥ 80% full.
4. ``my_assets`` lists every asset (live + destroyed) with lifetime
   stats.
5. §3.15 orbital publicity: probe launches pulse the opponent's
   ``probe_intel`` AND emit a structured ``probe_launch`` log event;
   harvester drops do NOT.
6. §3.16 probe collisions: two probes landing on the same cell are
   both destroyed; both end up in the destroyed-asset ledger with
   ``destroyed_reason = "probe_collision"``.
"""

from __future__ import annotations

import pytest

import os

import pytest

os.environ.setdefault("SOC_BACKEND", "memory")

from sea_of_colours.game.entities import Entity
from sea_of_colours.game.session import GameSession
from sea_of_colours.generator import Cell, Tile
from sea_of_colours.snowpark.view import build_agent_view


@pytest.fixture(autouse=True)
def _use_echo_drop_mode_for_legacy_tests(monkeypatch):
    """test_v0_7_0.py was written before v0.9.17 canonical rules.
    
    These tests assume live_or_echo drop mode (no probe requirement for
    drops). Rather than rewriting every test to add probe coverage, we
    restore the old default for this entire module."""
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")


def _fresh(**kw):
    return GameSession.new(20, 14, seed=kw.pop("seed", 73), **kw)


def test_agent_dense_view_partitions_live_echo_and_fog_count():
    sess = _fresh()
    # Pulse harvester vision (the +5 plus from §3.8) into intel by
    # placing the harvester on the surface for a moment.
    h = sess.entities["harvester_p1"]
    h.x, h.y = 10, 7
    sess._pulse_vision_intel()

    view = sess.agent_dense_view("p1")

    # Top-level keys.
    for k in ("width", "height", "live", "echo", "fog_count"):
        assert k in view, f"agent_dense_view missing `{k}`"
    assert view["width"] == sess.width
    assert view["height"] == sess.height
    total_cells = sess.width * sess.height
    assert (
        len(view["live"]) + len(view["echo"]) + view["fog_count"]
        == total_cells
    ), "live + echo + fog_count must cover the whole grid exactly once"
    # Each live row must carry the logical fields the agent reasons over.
    for row in view["live"]:
        for k in ("x", "y", "tile", "purity", "value"):
            assert k in row, f"live row missing `{k}`: {row!r}"


def test_agent_dense_view_carries_live_ledger_lineage_for_synthetic_green():
    """RED → GREEN conversion should mint a synthetic-lineage ledger row
    whose `lineage='synthetic'` surfaces on the live cell after harvest.
    """
    sess = _fresh()
    sess.grid[7][10] = Cell(Tile.RED, 200)
    # Force-trigger a harvest by dropping onto the RED.
    ok, _msg, harvested = sess.try_drop_unit("p1", "harvester_p1", 10, 7)
    assert ok and harvested
    sess._pulse_vision_intel()
    view = sess.agent_dense_view("p1")
    cell = next(c for c in view["live"] if c["x"] == 10 and c["y"] == 7)
    # The tile should now be GREEN (synthetic) with lineage stamped.
    assert cell["tile"] == "GREEN"
    assert cell.get("lineage") == "synthetic"
    assert cell.get("parent_square_id"), (
        "synthetic green must carry the parent RED square_id"
    )


def test_build_agent_view_emits_v070_top_level_keys():
    sess = _fresh()
    payload = build_agent_view(sess, "p1")
    for key in (
        "meta", "hud", "last_night", "competitor_intel",
        "world", "navigation", "my_assets",
    ):
        assert key in payload, f"build_agent_view missing top-level `{key}`"
    # meta.policy_actions_max reflects the new fleet-wide cap.
    assert payload["meta"]["policy_actions_max"] == 21
    # hud.hoard carries the new free / pct_full / warning fields.
    hoard = payload["hud"]["hoard"]
    for k in ("free", "pct_full", "warning"):
        assert k in hoard, f"hud.hoard missing `{k}`"
    # navigation surfaces the sorted RED lists.
    assert "best_red_visible" in payload["navigation"]
    assert "best_red_echo" in payload["navigation"]
    assert "fog_clusters" in payload["navigation"]


def test_hud_hoard_warning_fires_at_80_percent_full():
    sess = _fresh()
    # Cram 41 parcels into p1's hoard (50-slot capacity → 82% full).
    sess.hoard_squares["p1"] = [
        {
            "tile_at_harvest": int(Tile.RED),
            "purity_at_harvest": 50 + (i % 200),
            "square_id": f"hsq_{i}",
        }
        for i in range(41)
    ]
    payload = build_agent_view(sess, "p1")
    warning = payload["hud"]["hoard"]["warning"]
    assert warning is not None and "%" in warning and "vault" in warning, warning
    # Warning must name the next jettison tier — for an all-RED hoard
    # the next overflow displaces RED (BLUE absent, GREEN absent).
    assert "RED" in warning


def test_my_assets_includes_destroyed_assets_with_lifetime_stats():
    sess = _fresh()
    # Spawn + immediately destroy a probe so the asset ledger has a
    # destroyed-row to surface.
    ok, _ = sess.spawn_probe("p1", 4, 4)
    assert ok
    rec = sess.asset_records.get("probe_p1_1")
    assert rec is not None
    sess._note_asset_destroyed("probe_p1_1", reason="test_destroy")
    sess.entities.pop("probe_p1_1", None)

    payload = build_agent_view(sess, "p1")
    destroyed = [a for a in payload["my_assets"] if a["state"] == "destroyed"]
    assert any(a["id"] == "probe_p1_1" for a in destroyed), (
        "destroyed probe must appear in my_assets with state='destroyed'"
    )
    row = next(a for a in destroyed if a["id"] == "probe_p1_1")
    assert row["kind"] == "probe"
    assert row["lifetime"]["destroyed_reason"] == "test_destroy"


def test_probe_launch_pulses_opponent_echo_with_via_marker():
    """§3.15: deploying a probe must pulse the OPPONENT's probe_intel
    so their world.echo immediately surfaces the landing cell with
    via='probe_launch'.
    """
    sess = _fresh()
    sess.grid[3][9] = Cell(Tile.RED, 120)
    ok, _ = sess.spawn_probe("p1", 9, 3)
    assert ok
    # p2 (the opponent) should now have an echo entry at (9, 3).
    key = "9:3"
    assert key in sess.probe_intel["p2"], (
        "opponent probe_intel must carry the launch pulse cell"
    )
    snap = sess.probe_intel["p2"][key]
    assert snap.get("via") == "probe_launch"
    assert snap.get("day_seen") == sess.day
    # p1 (the owner) does NOT get a self-pulse — their own probe
    # disk handles current LOS for them.
    assert key not in sess.probe_intel["p1"] or (
        sess.probe_intel["p1"][key].get("via") != "probe_launch"
    )


def test_probe_launch_snapshot_omits_terrain_paint_purity_tile():
    """v0.9.7 + RULEBOOK §3.15: the opposing seat's probe-launch marker
    must carry the probe occupant ONLY. Terrain fields (``paint``,
    ``tile``, ``purity``) stay absent so the cell renders as fog
    underneath the probe glyph. Pre-v0.9.7 the broadcast included
    a full tile snapshot, which let opponents inspect terrain they
    had never legitimately observed.
    """
    sess = _fresh()
    sess.grid[3][9] = Cell(Tile.RED, 120)  # RED so a leak would be obvious
    ok, _ = sess.spawn_probe("p1", 9, 3)
    assert ok
    snap = sess.probe_intel["p2"]["9:3"]
    assert snap.get("via") == "probe_launch"
    # Probe occupant must be there (so the frontend can render the
    # glyph "floating in fog").
    occ = snap.get("occupants") or []
    assert occ and any(
        isinstance(o, dict) and o.get("type") == "probe"
        for o in occ
    ), f"probe-launch marker must include the probe occupant: {snap!r}"
    # Terrain reveal fields must NOT leak.
    assert "paint" not in snap, "probe-launch marker leaks paint (terrain)"
    assert "tile" not in snap, "probe-launch marker leaks tile (terrain)"
    assert "purity" not in snap, "probe-launch marker leaks purity (terrain)"


def test_probe_launch_echo_does_not_unlock_harvester_drop():
    """v0.9.7: a cell whose ONLY echo source is an enemy probe-launch
    marker must NOT count as drop-valid (RULEBOOK §3.10). The engine
    used to allow it (pre-v0.9.7), letting bots opportunistically
    land harvesters on terrain they had never observed just because
    the opponent flagged the cell with a probe.

    This test calls the ORIGINAL ``try_drop_unit`` directly because
    the conftest legacy shim stamps memory_tiles on the destination
    cell BEFORE delegating — which would defeat the §3.10 check.
    """
    from sea_of_colours.game import session as _session_module
    _real_try_drop = _session_module.GameSession._v092_orig_try_drop  # type: ignore[attr-defined]
    sess = _fresh()
    # Clear any conftest-stamped state so the rule applies cleanly.
    sess.memory_tiles = {p: {} for p in sess.players}
    # Player p1 launches a probe at (9,3); this seeds a probe-launch
    # marker into p2.probe_intel.
    ok, _ = sess.spawn_probe("p1", 9, 3)
    assert ok
    # Confirm p2's echo carries the marker but no terrain.
    assert "9:3" in sess.probe_intel["p2"]
    assert sess.probe_intel["p2"]["9:3"].get("via") == "probe_launch"
    # p2 attempting to drop a harvester at (9,3) must FAIL — terrain
    # is still fog from p2's perspective, the probe-launch marker
    # only reveals the probe entity.
    ok, msg, _ = _real_try_drop(sess, "p2", "harvester_p2", 9, 3)
    assert not ok, f"drop on probe-launch echo should fail; msg={msg!r}"
    assert "fog" in msg.lower()


def test_probe_launch_emits_structured_log_event():
    sess = _fresh()
    sess.grid[6][5] = Cell(Tile.RED, 200)
    ok, _ = sess.spawn_probe("p1", 5, 6)
    assert ok
    launches = [
        e for e in sess.log
        if isinstance(e, dict) and e.get("kind") == "probe_launch"
    ]
    assert launches, "spawn_probe must emit a probe_launch log event"
    data = launches[-1]["data"]
    assert data["owner"] == "p1"
    assert data["at"] == [5, 6]
    assert data["probe_id"].startswith("probe_p1_")
    assert data["landed_on_tile"] == "RED"
    assert data["landed_purity"] == 200


def test_harvester_drop_does_NOT_pulse_opponent_echo():
    """§3.15 + §0.4: the orblift bends through the magnetic cover, so a
    harvester drop must NOT show up in the opponent's echo and must
    NOT emit a probe_launch event.
    """
    sess = _fresh()
    pre_keys = set(sess.probe_intel["p2"].keys())
    pre_events = sum(
        1 for e in sess.log
        if isinstance(e, dict) and e.get("kind") == "probe_launch"
    )
    ok, _msg, _harvested = sess.try_drop_unit("p1", "harvester_p1", 12, 8)
    assert ok
    post_keys = set(sess.probe_intel["p2"].keys())
    new_keys = post_keys - pre_keys
    # The opponent must not learn about the drop cell from orbit.
    # (They can still learn about it later if their own LOS covers
    # the cell, but a fresh drop alone produces no enemy-visible
    # event.)
    assert "12:8" not in new_keys, (
        "harvester drop leaked to opponent echo — magnetic cover broken"
    )
    post_events = sum(
        1 for e in sess.log
        if isinstance(e, dict) and e.get("kind") == "probe_launch"
    )
    assert post_events == pre_events, (
        "harvester drop must not emit a probe_launch event"
    )


def test_probe_on_probe_collision_destroys_both():
    """§3.16: two probes landing on the same cell are both destroyed."""
    sess = _fresh()
    ok1, _ = sess.spawn_probe("p1", 10, 5)
    assert ok1
    ok2, msg = sess.spawn_probe("p2", 10, 5)
    assert ok2, msg
    # Both probes must be GONE from sess.entities.
    surviving_at_cell = [
        e for e in sess.entities.values()
        if e.entity_type == "probe" and e.x == 10 and e.y == 5
    ]
    assert not surviving_at_cell, (
        f"both colliding probes must be destroyed; "
        f"got {surviving_at_cell!r}"
    )
    # Both must be recorded in the asset ledger with the canonical
    # destroyed_reason.
    p1_probe = sess.asset_records.get("probe_p1_1")
    p2_probe = sess.asset_records.get("probe_p2_1")
    assert p1_probe is not None and p1_probe.destroyed_on_day is not None
    assert p2_probe is not None and p2_probe.destroyed_on_day is not None
    assert p1_probe.destroyed_by == "probe_collision"
    assert p2_probe.destroyed_by == "probe_collision"
    # The collision must emit a structured log event.
    collisions = [
        e for e in sess.log
        if isinstance(e, dict) and e.get("kind") == "probe_collision"
    ]
    assert collisions, "spawn_probe must emit a probe_collision event"
    data = collisions[-1]["data"]
    assert data["at"] == [10, 5]
    assert set(data["destroyed_ids"]) >= {"probe_p1_1", "probe_p2_1"}


def test_probe_on_prior_probe_supersedes_it():
    """§3.16: a probe landing on an OLDER probe (earlier hour / night)
    destroys + supersedes it — the newcomer survives, the old one is
    ledgered as ``probe_superseded`` (NOT a mutual collision)."""
    sess = _fresh()
    # First probe lands clean (turn 1).
    ok1, _ = sess.spawn_probe("p1", 10, 5, hour=1)
    assert ok1
    # Second probe, SAME cell but a LATER hour — supersedes the first.
    ok2, msg = sess.spawn_probe("p1", 10, 5, hour=8)
    assert ok2, msg
    # Exactly one probe survives on the cell: the newcomer.
    survivors = [
        e for e in sess.entities.values()
        if e.entity_type == "probe" and e.x == 10 and e.y == 5
    ]
    assert len(survivors) == 1, survivors
    assert survivors[0].id == "probe_p1_2"
    # The old probe is destroyed and ledgered with the supersede reason.
    old = sess.asset_records.get("probe_p1_1")
    assert old is not None and old.destroyed_on_day is not None
    assert old.destroyed_by == "probe_superseded"
    # A structured supersede event fires (distinct from probe_collision).
    sup = [
        e for e in sess.log
        if isinstance(e, dict) and e.get("kind") == "probe_superseded"
    ]
    assert sup, "spawn_probe must emit a probe_superseded event"
    assert sup[-1]["data"]["at"] == [10, 5]
    assert sup[-1]["data"]["new_probe"] == "probe_p1_2"
    assert "probe_p1_1" in sup[-1]["data"]["destroyed_ids"]


def test_two_probes_same_turn_still_mutually_destroy():
    """§3.16: probes deployed on the SAME turn (same day + hour) at one
    cell still mutually annihilate — supersession only applies to an
    older occupant."""
    sess = _fresh()
    ok1, _ = sess.spawn_probe("p1", 6, 6, hour=4)
    ok2, _ = sess.spawn_probe("p2", 6, 6, hour=4)
    assert ok1 and ok2
    survivors = [
        e for e in sess.entities.values()
        if e.entity_type == "probe" and e.x == 6 and e.y == 6
    ]
    assert not survivors, survivors
    col = [
        e for e in sess.log
        if isinstance(e, dict) and e.get("kind") == "probe_collision"
    ]
    assert col, "same-turn co-location must emit a probe_collision event"


def test_three_probes_same_turn_all_destroyed_no_crater_survivor():
    """§3.16 E4: a THIRD probe launched onto the SAME cell in the SAME turn
    must ALSO die. The first pair mutually annihilate and clear the cell, so a
    naive pairwise check would let the latecomer survive on the empty crater —
    the seed-69 day-1 pile-up bug where a stacked probe stole the seam cell."""
    sess = _fresh()
    ok1, _ = sess.spawn_probe("p1", 7, 7, hour=4)
    ok2, _ = sess.spawn_probe("p2", 7, 7, hour=4)  # mutual annihilation
    ok3, _ = sess.spawn_probe("p3", 7, 7, hour=4)  # lands on the crater
    assert ok1 and ok2 and ok3
    survivors = [
        e for e in sess.entities.values()
        if e.entity_type == "probe" and e.x == 7 and e.y == 7
    ]
    assert not survivors, f"third same-turn probe must not survive: {survivors!r}"
    third = sess.asset_records.get("probe_p3_1")
    assert third is not None and third.destroyed_on_day is not None
    assert third.destroyed_by == "probe_collision"


def test_later_hour_probe_on_collision_crater_still_supersedes_normally():
    """The crater sweep is turn-scoped: a probe landing on the SAME cell on a
    LATER hour (a different turn stamp) is NOT swept as a crater latecomer — it
    resolves by the normal rules (empty cell -> lands clean)."""
    sess = _fresh()
    sess.spawn_probe("p1", 8, 8, hour=2)
    sess.spawn_probe("p2", 8, 8, hour=2)  # crater at (8,8), stamp (day,2)
    ok3, _ = sess.spawn_probe("p3", 8, 8, hour=9)  # different stamp -> clean land
    assert ok3
    survivors = [
        e for e in sess.entities.values()
        if e.entity_type == "probe" and e.x == 8 and e.y == 8
    ]
    assert [s.owner for s in survivors] == ["p3"], survivors


def test_competitor_intel_surfaces_enemy_probe_launch_via_recent_log():
    """build_agent_view must project structured probe_launch events
    from recent_log into competitor_intel.new_this_day for the
    NON-OWNING seat (the rival who can see the launch from orbit).
    """
    sess = _fresh()
    sess.day = 2  # so day_ended=1 matches the synthetic recent_log row
    fake_log = [
        {
            "day": 1,
            "seq": 1,
            "level": "info",
            "text": "p2 launched probe_p2_3 at (14,9) — visible from orbit",
            "kind": "probe_launch",
            "data": {
                "owner": "p2",
                "probe_id": "probe_p2_3",
                "at": [14, 9],
                "landed_on_tile": "EMPTY",
                "landed_purity": 0,
                "day": 1,
            },
        },
    ]
    payload = build_agent_view(sess, "p1", recent_log=fake_log)
    rows = payload["competitor_intel"]["new_this_day"]
    launch_rows = [r for r in rows if r["kind"] == "enemy_probe_launch"]
    assert launch_rows, (
        "p1's competitor_intel must surface p2's probe launch from recent_log"
    )
    assert launch_rows[0]["owner"] == "p2"
    assert launch_rows[0]["at"] == [14, 9]


def test_competitor_intel_surfaces_launch_without_recent_log_window():
    """§3.15 (seed-69 E2 regression). A probe launched on a BUSY night can
    scroll out of the recent-log window before the next planning read. The
    launch is public and retained, so it must still surface in
    ``new_this_day`` sourced from the authoritative ``probe_intel`` markers —
    even with an EMPTY recent_log.
    """
    sess = _fresh()
    sess.day = 2
    ok, _ = sess.spawn_probe("p1", 9, 3)  # pulses p2 a via=probe_launch marker
    assert ok
    sess.day = 3  # day_ended = 2 == the launch day
    payload = build_agent_view(sess, "p2", recent_log=[])  # window is empty
    launches = [
        r for r in payload["competitor_intel"]["new_this_day"]
        if r["kind"] == "enemy_probe_launch"
    ]
    assert any(r["at"] == [9, 3] and r["owner"] == "p1" for r in launches), (
        f"launch must surface from probe_intel, not the log window: {launches!r}"
    )


def test_launch_onto_opponent_echo_still_surfaces_as_new_launch():
    """§3.15 (seed-69 E2 regression). When a probe lands on a cell the
    opponent already has an OLDER terrain echo for, the launch must not be
    swallowed by the stale echo: it is stamped with the launch day and still
    surfaces as a fresh ``enemy_probe_launch`` the next day. This is exactly
    how the redsign-discovering probe went invisible to the rival.
    """
    sess = _fresh()
    # p2 already holds a rich (non-launch) terrain echo at (9,3) from day 1.
    sess.probe_intel["p2"]["9:3"] = {
        "day_seen": 1, "tile": 0, "purity": 0,
        "paint": {"bg": "x", "ch": "..", "fg": "y"}, "occupants": [],
    }
    sess.day = 2
    ok, _ = sess.spawn_probe("p1", 9, 3)
    assert ok
    merged = sess.probe_intel["p2"]["9:3"]
    # FIX A — terrain snapshot preserved, but the launch is stamped.
    assert merged.get("via") != "probe_launch"
    assert merged.get("probe_launch_day") == 2
    assert merged.get("launched_by") == "p1"
    assert any(
        isinstance(o, dict) and o.get("type") == "probe"
        for o in merged.get("occupants") or []
    ), "merged marker must still carry the probe occupant"
    # FIX B — surfaces as new the next planning day even with an empty window.
    sess.day = 3  # day_ended = 2
    payload = build_agent_view(sess, "p2", recent_log=[])
    launches = [
        r for r in payload["competitor_intel"]["new_this_day"]
        if r["kind"] == "enemy_probe_launch"
    ]
    assert any(r["at"] == [9, 3] and r["owner"] == "p1" for r in launches), (
        f"merged-onto-echo launch must still surface: {launches!r}"
    )


def test_launch_onto_opponent_echo_stamps_a_visible_map_glyph():
    """v1.11 (RULEBOOK §3.15) regression. The v0.9.7 FIX above made a
    probe merged onto a richer terrain echo surface in the agent-facing
    intel feed, but never gave the MAP a drawable glyph — the general
    ghost-glyph path is gated on the terrain echo's own (deliberately
    un-bumped) ``day_seen``, so the probe sat in ``occupants`` only.
    Found while diagnosing missing rival probes in a live replay
    (season V12_HEUR3_SNAP2_s69, seed 69 — the SAME seed as the
    original v0.9.7 E2 incident this merge branch was named after).
    """
    sess = _fresh()
    # p2 already holds a rich (non-launch) terrain echo at (9,3), just
    # like the v0.9.7 regression test above.
    sess.probe_intel["p2"]["9:3"] = {
        "day_seen": 1, "tile": 0, "purity": 0,
        "paint": {"bg": "x", "ch": "..", "fg": "y"}, "occupants": [],
    }
    sess.day = 5  # well past day_seen=1, so glyph_fresh would be False
    ok, _ = sess.spawn_probe("p1", 9, 3)
    assert ok

    merged = sess.probe_intel["p2"]["9:3"]
    glyph = merged.get("probe_launch_glyph")
    assert isinstance(glyph, dict) and glyph.get("ch"), (
        "merge must stamp a dedicated, day_seen-independent glyph marker"
    )
    probe_id = glyph["probe_id"]

    # End-to-end: the rendered per-seat dense view must show a drawable
    # ``entity`` on this cell for p2, even though the terrain echo is
    # 4 days stale (glyph_fresh would otherwise be False).
    dense = sess.player_dense_view("p2")
    cell = dense[3 * sess.width + 9]  # (x=9, y=3)
    assert cell.get("kind") == "terrain" and cell.get("stale") is True
    assert cell.get("entity", {}).get("ch") == glyph["ch"], (
        f"rival probe on a stale echo must still render a glyph: {cell!r}"
    )

    # Cleanup on death (v1.11): the merged occupant + glyph must be
    # pruned like any other probe-death site, or they'd be permanent
    # ghosts (unlike the day_seen-decayed general echo glyph path).
    sess._clear_probe_launch_markers(probe_id, 9, 3)
    merged_after = sess.probe_intel["p2"]["9:3"]
    assert merged_after.get("probe_launch_glyph") is None
    assert not any(
        isinstance(o, dict) and o.get("id") == probe_id
        for o in merged_after.get("occupants") or []
    )
    # The terrain snapshot itself is untouched by the cleanup.
    assert merged_after.get("paint") == {"bg": "x", "ch": "..", "fg": "y"}
    dense_after = sess.player_dense_view("p2")
    cell_after = dense_after[3 * sess.width + 9]
    assert "entity" not in cell_after


def test_last_night_my_orders_round_trips_illegal_outcome():
    """An error-level recent_log row attributed to this player must end
    up in last_night.my_orders with outcome='illegal' + reason."""
    sess = _fresh()
    sess.day = 2  # day_ended = 1
    fake_log = [
        {
            "day": 1,
            "seq": 1,
            "level": "error",
            "text": "p1: step harvester_p1 (5,5)->(8,8) — not adjacent",
            "kind": None,
        },
        {
            "day": 1,
            "seq": 2,
            "level": "info",
            "text": "p1 deployed probe_p1_1 at (10,7)",
            "kind": None,
        },
    ]
    payload = build_agent_view(sess, "p1", recent_log=fake_log)
    orders = payload["last_night"]["my_orders"]
    illegal = [o for o in orders if o["outcome"] == "illegal"]
    ok = [o for o in orders if o["outcome"] == "ok"]
    assert illegal, "illegal order must surface in last_night.my_orders"
    assert "not adjacent" in (illegal[0].get("reason") or "")
    assert ok, "ok order must also surface in last_night.my_orders"
