"""Entity definitions for web prototype orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Entity:
    id: str
    entity_type: str  # harvester | orblift | probe
    owner: str  # p1 | p2
    x: int | None
    y: int | None
    carrying_red: bool = False  # synced with cargo_squares for legacy checks
    # Orbital lifter flag (cargo now goes to hoard; kept for summaries / hints).
    orbital_cargo_red: bool = False
    #: Harvested red tiles currently held on this harvester (surface only).
    cargo_squares: List[Dict[str, Any]] = field(default_factory=list)
    #: Set at dawn if the unit was stranded on-surface — remains in orbit, empty cargo.
    lost_last_night: bool = False
    #: Damaged state (RULEBOOK §3.6 v0.7.3).
    #:
    #: Harvester-on-harvester contact (drop-on, step-into, or
    #: pass-through swap) flips both colliding harvesters to
    #: ``damaged = True``, drops their cargo on the spot, and leaves
    #: them on the surface (no orbital recall). Damaged harvesters
    #: can co-occupy a cell with other harvesters (the blocking rule
    #: only fires for *undamaged* harvesters). Damage persists across
    #: nights — only a successful pickup clears the flag (and even
    #: then the parcels lost in the crash are gone forever). Dawn
    #: destruction (§3.11.2) still applies: a damaged harvester left
    #: on the surface at sunrise is destroyed exactly like a healthy
    #: one — damage does not grant immunity.
    damaged: bool = False
