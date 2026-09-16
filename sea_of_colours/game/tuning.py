"""Runtime-tunable rule knobs for the vision rework (Phase 2).

These are read from the environment **at call time** (not import time) so
the offline balance sweep can flip them per-config in-process, and a live
server can be launched with a variant without code edits.

v0.9.17 — canonical ruleset committed:
  SOC_DROP_MODE=live_only  (probe coverage required to land)
  SOC_PROBE_RADIUS=4       (~49-cell Euclidean disk per probe)
  SOC_PROBE_LIFETIME_NIGHTS=3  (probes expire after 3 nights)

Env vars still override code defaults for local experiments and CI sweeps.

Knobs:

* ``SOC_DROP_MODE`` — ``"live_only"`` (canonical) or ``"live_or_echo"``.
  In live-only, a harvester may only be dropped onto a cell the seat sees
  *right now* (probe disk or a friendly harvester's plus); stale own-echo /
  memory no longer qualifies. Landing becomes a public, contested act
  (probe launches are broadcast, §3.15).
* ``SOC_PROBE_RADIUS`` — Euclidean probe vision radius (default 4 = a
  ~49-cell disk). Overridable for balance experiments.
* ``SOC_PROBE_LIFETIME_NIGHTS`` — nights a probe survives before dawn
  expiry. Default 3. Set to 0 to disable expiry.
"""

from __future__ import annotations

import os
from typing import Optional

DROP_MODE_LIVE_ONLY = "live_only"
DROP_MODE_LIVE_OR_ECHO = "live_or_echo"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def drop_mode() -> str:
    """Return the active drop-legality mode (canonical: live_only)."""
    raw = (os.environ.get("SOC_DROP_MODE") or "").strip().lower()
    if raw in (DROP_MODE_LIVE_OR_ECHO, "liveorecho", "live-or-echo", "echo"):
        return DROP_MODE_LIVE_OR_ECHO
    return DROP_MODE_LIVE_ONLY


def live_only_drops() -> bool:
    return drop_mode() == DROP_MODE_LIVE_ONLY


def probe_vision_radius(default: int = 4) -> int:
    """Euclidean probe vision radius (canonical default: 4 = ~49-cell disk)."""
    return max(1, _int_env("SOC_PROBE_RADIUS", int(default)))


def probe_lifetime_nights() -> Optional[int]:
    """Nights a probe survives before dawn expiry (canonical: 3)."""
    k = _int_env("SOC_PROBE_LIFETIME_NIGHTS", 3)
    return k if k > 0 else None


def map_halo_density(default: float = 1.0) -> float:
    """``SOC_MAP_HALO`` — density of the v1.29 jackpot deposit (§2.2).

    Grading roughly doubled the RED on a board, which is a real balance
    change and the kind that is only properly judged after people have
    played on it. This is the escape hatch, so backing it out is a server
    restart rather than a code change:

    * ``0`` / ``off`` / ``none`` — grading off. Boards come out
      **byte-identical to v1.28**, because the pass runs last and draws on
      its own RNG stream, so nothing earlier moves.
    * ``0.5`` — half as much ``mass`` in each deposit; the shoulder and the
      reach are unchanged.
    * unset or ``1`` — as shipped.

    Reverting is safe for games already running: a session stores its
    **grid**, not just its seed (``GameSession.to_dict``), so changing this
    cannot re-terraform a season in progress. It applies to boards generated
    after the restart.

    Read at call time, like every knob here, so a sweep can flip it
    in-process.
    """
    raw = (os.environ.get("SOC_MAP_HALO") or "").strip().lower()
    if not raw:
        return max(0.0, float(default))
    if raw in ("off", "none", "no", "false"):
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return max(0.0, float(default))
