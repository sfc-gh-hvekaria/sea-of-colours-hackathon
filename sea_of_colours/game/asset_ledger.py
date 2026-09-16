"""Per-asset lifecycle ledger — every harvester, lifter, and probe is
tracked from creation through (eventual) destruction.

Architecturally a sibling of :mod:`sea_of_colours.game.ledger`: the
ledger lives outside :class:`~sea_of_colours.game.session.GameSession`
behind a :class:`AssetLedgerStore` protocol so the same rows can move
into a real Snowflake table later without touching session code.

Planned Snowflake shape::

    CREATE TABLE asset_record (
        session_id              STRING,
        asset_id                STRING,
        asset_type              STRING,   -- 'harvester' | 'orblift' | 'probe'
        owner                   STRING,   -- 'p1' | 'p2'
        created_on_day          NUMBER,
        first_deployed_day      NUMBER,   -- null if never surfaced
        destroyed_on_day        NUMBER,   -- null if alive
        destroyed_by            STRING,   -- e.g. 'crushed_by:harvester_p1'
        last_seen_x             NUMBER,
        last_seen_y             NUMBER,
        total_red_harvested     NUMBER,
        total_days_on_surface   NUMBER,
        PRIMARY KEY (session_id, asset_id)
    );

Lifecycle hooks (driven from session.py + simulator.py):

- ``register_existing`` on session start for the default roster
  (orbital harvester + orblift per player).
- ``register_existing`` on probe spawn.
- ``mark_deployed`` on a successful drop / on probe spawn (probes
  surface immediately).
- ``bump_red_harvested`` on every RED → GREEN conversion.
- ``mark_destroyed`` on probe crush.
- ``advance_day_counters`` at end of night for each asset still on the
  surface, incrementing ``total_days_on_surface``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol


@dataclass
class AssetRecord:
    """Lifecycle record for a single in-game asset.

    The dataclass mirrors the Snowflake table shape one-for-one so a
    future migration becomes ``INSERT … SELECT`` rather than a schema
    redesign.
    """

    asset_id: str
    asset_type: str  # "harvester" | "orblift" | "probe"
    owner: str  # "p1" | "p2"
    session_id: str
    created_on_day: int
    first_deployed_day: Optional[int] = None
    destroyed_on_day: Optional[int] = None
    destroyed_by: Optional[str] = None
    last_seen_x: Optional[int] = None
    last_seen_y: Optional[int] = None
    total_red_harvested: int = 0
    total_days_on_surface: int = 0
    # v0.9.5 — repair lifecycle. ``repair_count`` is the total
    # number of times this harvester has been pulled out of the
    # damaged state via :meth:`GameSession.apply_repair`. The day
    # of the most recent repair lets the HUD render "repaired
    # last on day N" so a player can tell at a glance whether a
    # given harvester has been through the workshop. Both fields
    # stay at their defaults for non-harvester assets.
    repair_count: int = 0
    last_repaired_day: Optional[int] = None

    def is_alive(self) -> bool:
        return self.destroyed_on_day is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_type": self.asset_type,
            "owner": self.owner,
            "session_id": self.session_id,
            "created_on_day": self.created_on_day,
            "first_deployed_day": self.first_deployed_day,
            "destroyed_on_day": self.destroyed_on_day,
            "destroyed_by": self.destroyed_by,
            "last_seen_x": self.last_seen_x,
            "last_seen_y": self.last_seen_y,
            "total_red_harvested": self.total_red_harvested,
            "total_days_on_surface": self.total_days_on_surface,
            "repair_count": self.repair_count,
            "last_repaired_day": self.last_repaired_day,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetRecord":
        def _opt_int(v: Any) -> Optional[int]:
            return None if v is None else int(v)

        return cls(
            asset_id=str(data.get("asset_id", "")),
            asset_type=str(data.get("asset_type", "")),
            owner=str(data.get("owner", "")),
            session_id=str(data.get("session_id", "")),
            created_on_day=int(data.get("created_on_day", 1)),
            first_deployed_day=_opt_int(data.get("first_deployed_day")),
            destroyed_on_day=_opt_int(data.get("destroyed_on_day")),
            destroyed_by=(
                None
                if data.get("destroyed_by") is None
                else str(data.get("destroyed_by"))
            ),
            last_seen_x=_opt_int(data.get("last_seen_x")),
            last_seen_y=_opt_int(data.get("last_seen_y")),
            total_red_harvested=int(data.get("total_red_harvested", 0) or 0),
            total_days_on_surface=int(data.get("total_days_on_surface", 0) or 0),
            repair_count=int(data.get("repair_count", 0) or 0),
            last_repaired_day=_opt_int(data.get("last_repaired_day")),
        )


@dataclass
class AssetLedger:
    """All :class:`AssetRecord` rows for one session."""

    session_id: str
    records: Dict[str, AssetRecord] = field(default_factory=dict)

    def upsert(self, record: AssetRecord) -> None:
        self.records[record.asset_id] = record

    def get(self, asset_id: str) -> Optional[AssetRecord]:
        return self.records.get(asset_id)

    def list_all(self) -> List[AssetRecord]:
        return list(self.records.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "records": {k: v.to_dict() for k, v in self.records.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetLedger":
        out = cls(session_id=str(data.get("session_id", "")))
        for k, v in (data.get("records") or {}).items():
            out.records[str(k)] = AssetRecord.from_dict(v)
        return out


class AssetLedgerStore(Protocol):
    """Pluggable backend for asset lifecycle rows (Snowflake-ready shape)."""

    def save(self, session_id: str, ledger: AssetLedger) -> None: ...
    def load(self, session_id: str) -> Optional[AssetLedger]: ...
    def delete(self, session_id: str) -> bool: ...


class InMemoryAssetLedgerStore:
    """Default backend: one :class:`AssetLedger` per session id."""

    def __init__(self) -> None:
        self._by_sid: Dict[str, AssetLedger] = {}

    def save(self, session_id: str, ledger: AssetLedger) -> None:
        self._by_sid[session_id] = ledger

    def load(self, session_id: str) -> Optional[AssetLedger]:
        return self._by_sid.get(session_id)

    def delete(self, session_id: str) -> bool:
        return self._by_sid.pop(session_id, None) is not None


ASSET_LEDGER_STORE: AssetLedgerStore = InMemoryAssetLedgerStore()
"""Singleton seam — swap with a Snowflake-backed store later."""
