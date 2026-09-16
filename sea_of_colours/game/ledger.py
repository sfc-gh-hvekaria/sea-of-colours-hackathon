"""Per-square identity ledger — the architectural seam to a Snowflake table.

Every coloured cell of a generated grid is assigned a deterministic
64-bit hex identifier at world-build time, *and* every RED→GREEN
conversion during play mints a fresh synthetic-green identity for the
tile that's just been poisoned. The mapping (``x:y → square_id`` plus
``square_id → row``) lives outside
:class:`~sea_of_colours.game.session.GameSession` so it can be moved to
a real Snowflake table later without touching session code: callers
talk to :class:`SquareLedger` (the per-game record) and the
:class:`LedgerStore` protocol (the storage backend).

Why a separate ledger?

- A square's id is canonical and survives outside the session lifecycle:
  once a House harvests a square, the **hash** is the certificate of
  origin that follows the parcel into orbit and (later) to Earth.
- Generation is deterministic, so the natural-lineage portion of the
  ledger can be reconstructed from ``(seed, grid)`` alone. The in-memory
  store keeps a cached copy; a Snowflake-backed store would persist the
  same rows in ``square_identity`` keyed by ``(session_id, square_id)``.
- Synthetic-green rows minted during play (from RED→GREEN harvests)
  carry parent provenance — who poisoned which square, when, with which
  harvester — so the Church can later run reputation / penalty
  mechanics against the audit trail.

Identity is derived from
``blake2b(seed, x, y, tile_at_generation, purity_at_generation)``
for natural rows and from
``blake2b(seed, x, y, day, harvester_id, parent_square_id, "synthetic")``
for synthetic-green rows. The synthetic hash mixes in the day +
harvester id so a row never collides with the natural row that sits at
the same ``(x, y)``.

Every row carries:

- ``square_id``                — canonical hash
- ``x`` / ``y``                — grid coordinates
- ``tile_at_generation``       — int(Tile) at the moment the row was
                                 minted (RED for harvested-source RED,
                                 GREEN for synthetic-green, etc.)
- ``purity_at_generation``     — purity at minting time
- ``lineage``                  — ``"natural"`` (world-build) or
                                 ``"synthetic"`` (RED→GREEN conversion)
- ``parent_square_id``         — only set for ``synthetic`` rows;
                                 points to the closed-out RED row.
- ``generated_by_owner``       — only set for ``synthetic`` rows;
                                 ``"p1"`` / ``"p2"``.
- ``generated_by_harvester_id`` — only set for ``synthetic`` rows.
- ``generated_on_day``          — only set for ``synthetic`` rows.
- ``harvested_on_day``          — stamped by :meth:`mark_harvested`.
                                 ``None`` for active rows.
- ``harvested_by``              — harvester id that closed out the
                                 row; ``None`` for active rows.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Protocol

from sea_of_colours.generator import Grid, Tile


def square_hash(
    seed: int,
    x: int,
    y: int,
    tile_at_generation: int,
    purity_at_generation: int,
) -> str:
    """Deterministic 16-char hex id for a single tile at generation time."""
    h = hashlib.blake2b(digest_size=8)
    payload = f"{int(seed)}|{int(x)}|{int(y)}|{int(tile_at_generation)}|{int(purity_at_generation)}"
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


def synthetic_green_hash(
    seed: int,
    x: int,
    y: int,
    day: int,
    harvester_id: str,
    parent_square_id: str,
) -> str:
    """Deterministic 16-char hex id for a synthetic-green row minted during play.

    Mixes day + harvester_id + parent_square_id into the hash so it
    can never collide with the natural-build row that would otherwise
    sit at the same ``(x, y)`` — and so two harvesters poisoning the
    same coordinates on different nights produce distinct identities.
    """
    h = hashlib.blake2b(digest_size=8)
    payload = (
        f"{int(seed)}|{int(x)}|{int(y)}|{int(day)}|"
        f"{harvester_id}|{parent_square_id}|synthetic"
    )
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


def _make_natural_row(
    seed: int, x: int, y: int, tile_i: int, pur_i: int,
) -> Dict[str, Any]:
    """Build a fresh natural-lineage ledger row dict."""
    return {
        "square_id": square_hash(seed, x, y, tile_i, pur_i),
        "x": x,
        "y": y,
        "tile_at_generation": tile_i,
        "purity_at_generation": pur_i,
        "lineage": "natural",
        "parent_square_id": None,
        "generated_by_owner": None,
        "generated_by_harvester_id": None,
        "generated_on_day": None,
        "harvested_on_day": None,
        "harvested_by": None,
    }


@dataclass
class SquareLedger:
    """All identities for one session — natural (world-build) + synthetic.

    Architecturally analogous to a Snowflake table:

        CREATE TABLE square_identity (
            session_id                 STRING,
            square_id                  STRING,   -- 16-char blake2b
            x                          NUMBER,
            y                          NUMBER,
            tile_at_generation         NUMBER,
            purity_at_generation       NUMBER,
            lineage                    STRING,   -- 'natural' | 'synthetic'
            parent_square_id           STRING,   -- nullable
            generated_by_owner         STRING,   -- nullable
            generated_by_harvester_id  STRING,   -- nullable
            generated_on_day           NUMBER,   -- nullable
            harvested_on_day           NUMBER,   -- nullable
            harvested_by               STRING    -- nullable
        );
    """

    seed: int
    width: int
    height: int
    # World-build natural rows, keyed by ``"x:y"``. One per cell at
    # session birth; mutates in place when :meth:`mark_harvested`
    # stamps an entry, but the dict shape never changes.
    entries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Synthetic-green rows minted by :meth:`mint_synthetic_green`
    # during play, keyed by their (unique) ``square_id``. We key by
    # id rather than ``"x:y"`` because two harvesters could in
    # principle poison the same coordinates on different nights and
    # both rows must remain auditable.
    synthetic_rows: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_grid(cls, seed: int, grid: Grid) -> "SquareLedger":
        height = len(grid)
        width = len(grid[0]) if height > 0 else 0
        entries: Dict[str, Dict[str, Any]] = {}
        for y in range(height):
            for x in range(width):
                cell = grid[y][x]
                tile_i = int(cell.tile)
                pur_i = int(cell.purity)
                entries[f"{x}:{y}"] = _make_natural_row(
                    seed, x, y, tile_i, pur_i,
                )
        return cls(seed=int(seed), width=width, height=height, entries=entries)

    # --- Read helpers -------------------------------------------------

    def _xy_key(self, x: int, y: int) -> str:
        return f"{x}:{y}"

    def _active_synthetic_at(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        """Most recent unharvested synthetic-green at ``(x, y)``, if any."""
        best: Optional[Dict[str, Any]] = None
        best_day = -1
        for row in self.synthetic_rows.values():
            if row.get("x") != x or row.get("y") != y:
                continue
            if row.get("harvested_on_day") is not None:
                continue
            d = int(row.get("generated_on_day") or 0)
            if d >= best_day:
                best_day = d
                best = row
        return best

    def lookup(self, x: int, y: int) -> str:
        """Active square_id at ``(x, y)`` — synthetic-green wins if present."""
        synth = self._active_synthetic_at(x, y)
        if synth is not None:
            return str(synth["square_id"])
        rec = self.entries.get(self._xy_key(x, y))
        return rec["square_id"] if rec else ""

    def record(self, x: int, y: int) -> Dict[str, Any]:
        """Active row at ``(x, y)`` — synthetic-green if present, else natural."""
        synth = self._active_synthetic_at(x, y)
        if synth is not None:
            return dict(synth)
        return dict(self.entries.get(self._xy_key(x, y), {}))

    def row_by_sid(self, square_id: str) -> Optional[Dict[str, Any]]:
        """Return the row dict for ``square_id`` (natural OR synthetic), if any."""
        if not square_id:
            return None
        if square_id in self.synthetic_rows:
            return self.synthetic_rows[square_id]
        for row in self.entries.values():
            if row.get("square_id") == square_id:
                return row
        return None

    # --- Mutators ------------------------------------------------------

    def mint_synthetic_green(
        self,
        x: int,
        y: int,
        day: int,
        harvester_id: str,
        owner: str,
        parent_square_id: str,
    ) -> str:
        """Mint a synthetic-green row for a freshly-converted RED tile.

        Called by the engine immediately after a RED → GREEN harvest
        completes. Returns the new ``square_id`` so the caller can
        carry it on the harvester's cargo record / replay frame.
        """
        sid = synthetic_green_hash(
            self.seed, x, y, day, harvester_id, parent_square_id,
        )
        # In the (extremely unlikely) case of a hash collision with an
        # earlier synthetic at the same coords, the dict update is
        # safe: same id, same xy, idempotent.
        self.synthetic_rows[sid] = {
            "square_id": sid,
            "x": x,
            "y": y,
            "tile_at_generation": int(Tile.GREEN),
            "purity_at_generation": 255,
            "lineage": "synthetic",
            "parent_square_id": parent_square_id,
            "generated_by_owner": owner,
            "generated_by_harvester_id": harvester_id,
            "generated_on_day": int(day),
            "harvested_on_day": None,
            "harvested_by": None,
        }
        return sid

    def mark_harvested(
        self,
        square_id: str,
        harvested_on_day: int,
        harvested_by: str,
    ) -> bool:
        """Stamp the row for ``square_id`` with harvest metadata.

        Returns True if a row was found and stamped, False otherwise
        (callers can fall through silently — a missing row just means
        the harvested tile was outside the ledger, e.g. early v0.5
        sessions loaded from a payload without lineage tracking).

        Idempotent: re-stamping a row already marked harvested is a
        no-op (we keep the earliest stamp, matching the §3.12
        lifecycle: a square is harvested exactly once).
        """
        row = self.row_by_sid(square_id)
        if row is None:
            return False
        if row.get("harvested_on_day") is not None:
            return True  # already closed; idempotent
        row["harvested_on_day"] = int(harvested_on_day)
        row["harvested_by"] = str(harvested_by)
        return True

    # --- Serialization -------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "entries": {k: dict(v) for k, v in self.entries.items()},
            "synthetic_rows": {
                k: dict(v) for k, v in self.synthetic_rows.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SquareLedger":
        # Back-compat: pre-v0.6 ledger payloads have no lineage / harvest
        # fields. Coerce them to the new shape by patching missing keys
        # with default values so downstream queries don't KeyError.
        raw_entries = (data.get("entries") or {})
        entries: Dict[str, Dict[str, Any]] = {}
        for k, v in raw_entries.items():
            row = dict(v)
            row.setdefault("lineage", "natural")
            row.setdefault("parent_square_id", None)
            row.setdefault("generated_by_owner", None)
            row.setdefault("generated_by_harvester_id", None)
            row.setdefault("generated_on_day", None)
            row.setdefault("harvested_on_day", None)
            row.setdefault("harvested_by", None)
            entries[k] = row
        synth: Dict[str, Dict[str, Any]] = {
            k: dict(v) for k, v in (data.get("synthetic_rows") or {}).items()
        }
        return cls(
            seed=int(data.get("seed", 0)),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            entries=entries,
            synthetic_rows=synth,
        )


class LedgerStore(Protocol):
    """Pluggable backend for square identity rows (Snowflake-ready shape)."""

    def save(self, session_id: str, ledger: SquareLedger) -> None: ...
    def load(self, session_id: str) -> Optional[SquareLedger]: ...
    def delete(self, session_id: str) -> bool: ...


class InMemoryLedgerStore:
    """Default backend: holds one :class:`SquareLedger` per session id."""

    def __init__(self) -> None:
        self._by_sid: Dict[str, SquareLedger] = {}

    def save(self, session_id: str, ledger: SquareLedger) -> None:
        self._by_sid[session_id] = ledger

    def load(self, session_id: str) -> Optional[SquareLedger]:
        return self._by_sid.get(session_id)

    def delete(self, session_id: str) -> bool:
        return self._by_sid.pop(session_id, None) is not None


LEDGER_STORE: LedgerStore = InMemoryLedgerStore()
"""Singleton seam — swap with a Snowflake-backed store later."""
