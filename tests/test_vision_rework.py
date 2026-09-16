"""Phase 2 vision-rework knobs (RULEBOOK §3.9.7 / §3.11.1).

All three knobs are runtime-read from the environment (so the balance
sweep can flip them in-process) and default to pre-rework behaviour.
These tests pin both the default (off) path and the enabled path.

They bypass the conftest legacy ``try_drop_unit`` shim by calling the
stashed original (``_v092_orig_try_drop``), the same trick
``test_v092_landings`` uses, because the shim pre-stamps memory tiles —
which is exactly the echo path live-only mode is meant to reject.
"""

from __future__ import annotations

import pytest

from sea_of_colours.game import session as _session_module
from sea_of_colours.game.session import GameSession, Phase, _xy_key
from sea_of_colours.game import tuning
from sea_of_colours.generator import Tile

PLAYERS = ("p1", "p2")
_real_try_drop = _session_module.GameSession._v092_orig_try_drop  # type: ignore[attr-defined]


def _fresh(width: int = 20, height: int = 14, seed: int = 7) -> GameSession:
    sess = GameSession.new(width, height, seed=seed)
    sess.memory_tiles = {p: {} for p in PLAYERS}
    sess.probe_intel = {p: {} for p in PLAYERS}
    sess.phase = Phase.PLANNING
    return sess


# ── SOC_DROP_MODE=live_only (§3.9.7) ────────────────────────────────


def test_default_mode_is_live_only(monkeypatch):
    # v0.9.17 canonical: live_only is the default (§3.9.7).
    monkeypatch.delenv("SOC_DROP_MODE", raising=False)
    assert tuning.live_only_drops()


def test_echo_mode_allows_memory_only_drop(monkeypatch):
    # Override back to echo mode for back-compat testing.
    monkeypatch.setenv("SOC_DROP_MODE", "live_or_echo")
    assert not tuning.live_only_drops()
    sess = _fresh()
    sess.memory_tiles["p1"][_xy_key(3, 4)] = {"paint": {}, "stale": True}
    ok, msg, _ = _real_try_drop(sess, "p1", "harvester_p1", 3, 4)
    assert ok, f"echo mode must still allow memory-only drops: {msg}"


def test_live_only_rejects_memory_only_drop(monkeypatch):
    monkeypatch.setenv("SOC_DROP_MODE", "live_only")
    assert tuning.live_only_drops()
    sess = _fresh()
    # Echo + memory present, but NO live coverage of (3,4).
    sess.memory_tiles["p1"][_xy_key(3, 4)] = {"paint": {}, "stale": True}
    sess.probe_intel["p1"][_xy_key(3, 4)] = {"tile": int(Tile.RED), "purity": 99}
    ok, msg, _ = _real_try_drop(sess, "p1", "harvester_p1", 3, 4)
    assert not ok, "live-only must reject a stale echo/memory landing"
    assert "live" in msg.lower()


def test_live_only_allows_live_probe_disk_drop(monkeypatch):
    monkeypatch.setenv("SOC_DROP_MODE", "live_only")
    sess = _fresh()
    ok, _ = sess.spawn_probe("p1", 10, 7)
    assert ok
    assert (10, 7) in sess.tiles_visible_now("p1")
    ok, msg, _ = _real_try_drop(sess, "p1", "harvester_p1", 10, 7)
    assert ok, f"live-only must allow a drop under a live probe disk: {msg}"


def test_live_only_honours_hour_start_override(monkeypatch):
    """The simulator passes an hour-start snapshot; a cell in that
    snapshot lands even if it isn't in the resolve-time LOS (a beacon
    killed the same hour still validates the landing, §3.10)."""
    monkeypatch.setenv("SOC_DROP_MODE", "live_only")
    sess = _fresh()
    # No live coverage of (12,9) right now...
    assert (12, 9) not in sess.tiles_visible_now("p1")
    # ...but it was lit at hour start (override snapshot includes it).
    ok, msg, _ = _real_try_drop(
        sess, "p1", "harvester_p1", 12, 9, live_override={(12, 9)},
    )
    assert ok, f"hour-start beacon snapshot must validate the drop: {msg}"


# ── SOC_PROBE_RADIUS (§3.11.1) ──────────────────────────────────────


def test_probe_radius_default_is_four(monkeypatch):
    # v0.9.17 canonical: default radius is now 4 (41-cell Euclidean disk).
    monkeypatch.delenv("SOC_PROBE_RADIUS", raising=False)
    assert tuning.probe_vision_radius() == 4
    sess = _fresh()
    sess.spawn_probe("p1", 10, 7)
    live = sess.tiles_visible_now("p1")
    # At radius 4 a cell 3 away IS visible (Euclidean 3 < 4).
    assert (13, 7) in live
    # A cell 5 away (Euclidean > 4) is NOT visible at the default radius.
    assert (15, 7) not in live


def test_probe_radius_env_narrows_disk(monkeypatch):
    # Env override can revert to the old r=2 for balance experiments.
    monkeypatch.setenv("SOC_PROBE_RADIUS", "2")
    assert tuning.probe_vision_radius() == 2
    sess = _fresh()
    sess.spawn_probe("p1", 10, 7)
    live = sess.tiles_visible_now("p1")
    # At radius 2 a cell 3 away falls outside.
    assert (13, 7) not in live


# ── SOC_PROBE_LIFETIME_NIGHTS decay (§3.11.1) ───────────────────────


def test_probes_persist_when_lifetime_disabled(monkeypatch):
    # Lifetime disabled by setting to 0 (env override).
    monkeypatch.setenv("SOC_PROBE_LIFETIME_NIGHTS", "0")
    sess = _fresh()
    sess.spawn_probe("p1", 10, 7)
    sess.day = 9
    assert sess.decay_probes() == []
    assert any(e.entity_type == "probe" for e in sess.entities.values())


def test_probe_expires_after_lifetime(monkeypatch):
    monkeypatch.setenv("SOC_PROBE_LIFETIME_NIGHTS", "2")
    sess = _fresh()
    ok, _ = sess.spawn_probe("p1", 10, 7)
    assert ok
    pid = next(
        eid for eid, e in sess.entities.items() if e.entity_type == "probe"
    )
    rec = sess.asset_records[pid]
    rec.first_deployed_day = 1
    # Night 1: present 1 night (< 2) — survives.
    sess.day = 1
    assert sess.decay_probes() == []
    assert pid in sess.entities
    # Night 2: present 2 nights (>= 2) — expires, disk drops to echo.
    sess.day = 2
    expired = sess.decay_probes()
    assert pid in expired
    assert pid not in sess.entities
    assert sess.asset_records[pid].destroyed_by == "probe_expired"
