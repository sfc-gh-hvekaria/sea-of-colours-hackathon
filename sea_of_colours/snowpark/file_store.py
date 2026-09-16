"""Local file-backed :class:`SocStore` — durable, cross-process, no Snowflake.

The default :class:`~sea_of_colours.snowpark.store.InMemorySocStore` keeps
SOC_* tables in process RAM, so a season produced by a headless runner (a
separate process) is invisible to a running web server, and everything is
lost on exit. :class:`FileSocStore` closes that gap for offline play and is
the backend selected by ``SOC_BACKEND=file`` (directory ``SOC_STORE_DIR``,
default ``./seasons/``).

Layout — **one directory per season**, one small file per SOC_* table::

    <store_dir>/<session_id>/
        meta.json            session row WITHOUT the heavy json_state blob
        state.json           {"json_state": <full GameSession serialization>}
        square_identity.json grid_cells.json  asset_records.json …
        hoard.json  shipped.json  policies.json  log.json  agent.json
        frames.jsonl         append-only replay frames (one per line)

Why split, not one fat file: the season's ``json_state`` blob is ~MB-to-
tens-of-MB, and ``save_session_full`` writes seven SOC_* tables per turn
**concurrently**. A single combined file would re-serialize the whole
state on every one of those writes — quadratic, minutes of wall time, and
a 70 MB file. Splitting means a small ``upsert_grid_cells`` only rewrites
``grid_cells.json``; ``json_state`` is written once per ``save_session``;
listing seasons reads only the tiny ``meta.json`` files; and the replay
stream appends to ``frames.jsonl`` in O(1).

Mechanics:

* Wraps a private :class:`InMemorySocStore` as the in-RAM working set so
  the merge/append/list semantics match the Snowflake backend 1:1.
* A re-entrant-free lock guards every public method — ``save_session_full``
  fans its phases across threads, and the in-RAM store + atomic file
  writes are not otherwise thread-safe.
* Writes mutate RAM then atomically rewrite just the touched table file
  (temp file + :func:`os.replace`); reads re-read the relevant file(s)
  from disk first, so a *separately running* web server always sees the
  latest harness output — even one that started before the season existed.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sea_of_colours.snowpark.store import InMemorySocStore

DEFAULT_STORE_DIR = "./seasons"

# Per-table filenames inside a season directory.
F_META = "meta.json"
F_STATE = "state.json"
F_SQUARE = "square_identity.json"
F_ASSET = "asset_records.json"
F_ENTITY = "entity_state.json"
F_GRID = "grid_cells.json"
F_HOARD = "hoard.json"
F_SHIPPED = "shipped.json"
F_POLICIES = "policies.json"
F_LOG = "log.json"
F_AGENT = "agent.json"
F_FRAMES = "frames.jsonl"


def _xy_rows(by_xy: Dict[tuple, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten an ``(x, y) -> row`` map back to a JSON-safe row list."""
    return [dict(r) for r in by_xy.values()]


class FileSocStore:
    """Directory-per-season, file-per-table :class:`SocStore`."""

    def __init__(self, store_dir: Optional[str] = None) -> None:
        self.dir = Path(store_dir or os.environ.get("SOC_STORE_DIR")
                        or DEFAULT_STORE_DIR).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._mem = InMemorySocStore()
        self._touched: Dict[str, float] = {}
        self._loaded: Dict[str, set] = {}
        self._lock = threading.RLock()
        # v0.9.19 — opt-in write batching for single-process offline runs
        # (the headless season battery). When ``_batch`` is on, the heavy
        # per-table writes are coalesced: each touched part is marked dirty
        # in RAM and only flushed to disk once (``flush_batch``), instead of
        # re-serialising the full state on EVERY agent turn. Reads serve RAM
        # directly during a batch (see ``_reload_part``) since a batched run
        # is the sole writer, so the simulation never sees stale disk state.
        # meta.json is the ONE part still written eagerly so the season stays
        # discoverable on disk and ``_reload_all_meta`` can't drop it.
        self._batch = False
        self._batch_dirty: Dict[str, set] = {}

    # ── disk plumbing ────────────────────────────────────────────────
    def _sdir(self, sid: str) -> Path:
        return self.dir / sid

    def _part(self, sid: str, name: str) -> Path:
        return self._sdir(sid) / name

    def _write_json(self, path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _read_json(self, path: Path) -> Optional[Any]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    # ── write batching (offline single-writer runs) ──────────────────
    def _defer_write(self, sid: str, part: str) -> bool:
        """In batch mode, record ``part`` dirty and skip the disk write.

        Returns ``True`` when the caller should bail out (deferred). The
        in-RAM ``_mem`` has already been updated by the public mutator, so
        the eventual ``flush_batch`` re-serialises the final state once.
        """
        if self._batch:
            self._batch_dirty.setdefault(sid, set()).add(part)
            return True
        return False

    def begin_batch(self) -> None:
        """Start coalescing heavy table writes (offline battery only)."""
        with self._lock:
            self._batch = True

    def flush_batch(self) -> None:
        """Write every dirty part once and leave batch mode off."""
        with self._lock:
            self._batch = False
            dirty = self._batch_dirty
            self._batch_dirty = {}
            for sid, parts in dirty.items():
                for part in sorted(parts):
                    getattr(self, self._BATCH_WRITERS[part])(sid)

    def _bump_touched(self, sid: str) -> None:
        self._touched[sid] = time.time()

    # ── per-part write (serialize just one SOC_* table) ──────────────
    def _write_meta(self, sid: str) -> None:
        row = dict(self._mem.sessions.get(sid) or {})
        row.pop("json_state", None)  # heavy blob lives in state.json
        self._bump_touched(sid)
        self._write_json(
            self._part(sid, F_META),
            {"session_id": sid, "_touched": self._touched[sid], "row": row},
        )

    def _write_state(self, sid: str) -> None:
        if self._defer_write(sid, F_STATE):
            return
        row = self._mem.sessions.get(sid) or {}
        self._write_json(
            self._part(sid, F_STATE), {"json_state": row.get("json_state")}
        )

    def _write_square(self, sid: str) -> None:
        if self._defer_write(sid, F_SQUARE):
            return
        self._write_json(
            self._part(sid, F_SQUARE), _xy_rows(self._mem.square_identity.get(sid, {}))
        )

    def _write_asset(self, sid: str) -> None:
        if self._defer_write(sid, F_ASSET):
            return
        self._write_json(self._part(sid, F_ASSET), self._mem.asset_records.get(sid, {}))

    def _write_entity(self, sid: str) -> None:
        if self._defer_write(sid, F_ENTITY):
            return
        self._write_json(self._part(sid, F_ENTITY), self._mem.entity_state.get(sid, {}))

    def _write_grid(self, sid: str) -> None:
        if self._defer_write(sid, F_GRID):
            return
        self._write_json(
            self._part(sid, F_GRID), _xy_rows(self._mem.grid_cells.get(sid, {}))
        )

    def _write_hoard(self, sid: str) -> None:
        if self._defer_write(sid, F_HOARD):
            return
        self._write_json(self._part(sid, F_HOARD), self._mem.hoard.get(sid, {}))

    def _write_shipped(self, sid: str) -> None:
        if self._defer_write(sid, F_SHIPPED):
            return
        self._write_json(self._part(sid, F_SHIPPED), self._mem.shipped.get(sid, {}))

    def _write_policies(self, sid: str) -> None:
        if self._defer_write(sid, F_POLICIES):
            return
        self._write_json(
            self._part(sid, F_POLICIES),
            {str(d): pl for d, pl in self._mem.policies.get(sid, {}).items()},
        )

    def _write_log(self, sid: str) -> None:
        if self._defer_write(sid, F_LOG):
            return
        self._write_json(self._part(sid, F_LOG), self._mem.log_rows.get(sid, []))

    def _write_agent(self, sid: str) -> None:
        if self._defer_write(sid, F_AGENT):
            return
        self._write_json(
            self._part(sid, F_AGENT), self._mem.agent_invocations.get(sid, [])
        )

    # Maps each batchable "part" to the writer that flushes it from RAM.
    # ``F_META`` is intentionally absent — meta is always written eagerly
    # (it's tiny and keeps the season discoverable during a batch).
    _BATCH_WRITERS = {
        F_STATE: "_write_state", F_SQUARE: "_write_square",
        F_ASSET: "_write_asset", F_ENTITY: "_write_entity",
        F_GRID: "_write_grid", F_HOARD: "_write_hoard",
        F_SHIPPED: "_write_shipped", F_POLICIES: "_write_policies",
        F_LOG: "_write_log", F_AGENT: "_write_agent",
    }

    # ── per-part read into RAM ───────────────────────────────────────
    def _read_frames(self, sid: str) -> List[Dict[str, Any]]:
        path = self._part(sid, F_FRAMES)
        if not path.exists():
            return []
        frames: List[Dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frames.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # tolerate a half-written trailing line
        except OSError:
            return []
        return frames

    def _load_meta(self, sid: str) -> bool:
        data = self._read_json(self._part(sid, F_META))
        if data is None:
            return False
        row = dict(data.get("row") or {})
        # Preserve any json_state already in RAM (state.json loads separately).
        existing = self._mem.sessions.get(sid) or {}
        if "json_state" in existing:
            row["json_state"] = existing["json_state"]
        row.setdefault("session_id", sid)
        self._mem.sessions[sid] = row
        if sid not in self._mem.sessions_order:
            self._mem.sessions_order.append(sid)
        self._touched[sid] = float(data.get("_touched") or 0.0)
        return True

    def _load_state(self, sid: str) -> None:
        data = self._read_json(self._part(sid, F_STATE)) or {}
        row = self._mem.sessions.setdefault(sid, {"session_id": sid})
        row["json_state"] = data.get("json_state")

    def _load_square(self, sid: str) -> None:
        rows = self._read_json(self._part(sid, F_SQUARE)) or []
        self._mem.square_identity[sid] = {
            (int(r["x"]), int(r["y"])): dict(r) for r in rows
        }

    def _load_asset(self, sid: str) -> None:
        d = self._read_json(self._part(sid, F_ASSET)) or {}
        self._mem.asset_records[sid] = {str(k): dict(v) for k, v in d.items()}

    def _load_entity(self, sid: str) -> None:
        d = self._read_json(self._part(sid, F_ENTITY)) or {}
        self._mem.entity_state[sid] = {str(k): dict(v) for k, v in d.items()}

    def _load_grid(self, sid: str) -> None:
        rows = self._read_json(self._part(sid, F_GRID)) or []
        self._mem.grid_cells[sid] = {
            (int(r["x"]), int(r["y"])): dict(r) for r in rows
        }

    def _load_hoard(self, sid: str) -> None:
        d = self._read_json(self._part(sid, F_HOARD)) or {}
        self._mem.hoard[sid] = {o: [dict(r) for r in rows] for o, rows in d.items()}

    def _load_shipped(self, sid: str) -> None:
        d = self._read_json(self._part(sid, F_SHIPPED)) or {}
        self._mem.shipped[sid] = {o: [dict(r) for r in rows] for o, rows in d.items()}

    def _load_policies(self, sid: str) -> None:
        d = self._read_json(self._part(sid, F_POLICIES)) or {}
        self._mem.policies[sid] = {
            int(day): {p: list(q) for p, q in pl.items()} for day, pl in d.items()
        }

    def _load_log(self, sid: str) -> None:
        self._mem.log_rows[sid] = [
            dict(r) for r in (self._read_json(self._part(sid, F_LOG)) or [])
        ]

    def _load_agent(self, sid: str) -> None:
        self._mem.agent_invocations[sid] = [
            dict(r) for r in (self._read_json(self._part(sid, F_AGENT)) or [])
        ]

    def _load_frames(self, sid: str) -> None:
        self._mem.replay_frames[sid] = self._read_frames(sid)

    # Maps each "part" to its (loader). Writes call the matching _write_*.
    _LOADERS = {
        F_META: "_load_meta", F_STATE: "_load_state", F_SQUARE: "_load_square",
        F_ASSET: "_load_asset", F_ENTITY: "_load_entity", F_GRID: "_load_grid",
        F_HOARD: "_load_hoard", F_SHIPPED: "_load_shipped",
        F_POLICIES: "_load_policies", F_LOG: "_load_log", F_AGENT: "_load_agent",
        F_FRAMES: "_load_frames",
    }

    def _ensure_part(self, sid: str, part: str) -> None:
        """Load a part from disk into RAM once (for read-modify-write)."""
        loaded = self._loaded.setdefault(sid, set())
        if part in loaded:
            return
        getattr(self, self._LOADERS[part])(sid)
        loaded.add(part)

    def _reload_part(self, sid: str, part: str) -> None:
        """Re-read a part from disk (for cross-process freshness).

        During a write batch (single-writer offline run) RAM is the source
        of truth — the deferred parts haven't hit disk yet — so we trust the
        already-loaded in-RAM copy and skip the re-read. Outside a batch the
        original always-fresh behaviour is preserved.
        """
        if self._batch and part in self._loaded.get(sid, set()):
            return
        getattr(self, self._LOADERS[part])(sid)
        self._loaded.setdefault(sid, set()).add(part)

    def _exists(self, sid: str) -> bool:
        return self._part(sid, F_META).exists()

    def _drop_from_mem(self, sid: str) -> None:
        mem = self._mem
        mem.sessions.pop(sid, None)
        if sid in mem.sessions_order:
            mem.sessions_order.remove(sid)
        for container in (
            mem.square_identity, mem.asset_records, mem.entity_state,
            mem.grid_cells, mem.hoard, mem.shipped, mem.policies,
            mem.log_rows, mem.replay_frames, mem.agent_invocations,
        ):
            container.pop(sid, None)
        self._touched.pop(sid, None)
        self._loaded.pop(sid, None)

    def _scan_sessions(self) -> List[str]:
        """Return session ids present on disk (have a meta.json)."""
        out = []
        for child in self.dir.iterdir():
            if child.is_dir() and (child / F_META).exists():
                out.append(child.name)
        return out

    def _reload_all_meta(self) -> None:
        """Load every season's meta (lightweight) + rebuild ordering."""
        sids = self._scan_sessions()
        for sid in sids:
            self._load_meta(sid)
        # Drop RAM rows whose dir vanished.
        for sid in list(self._mem.sessions.keys()):
            if sid not in sids:
                self._drop_from_mem(sid)
        self._mem.sessions_order = sorted(
            sids, key=lambda s: (self._touched.get(s, 0.0), s)
        )

    # ── season lifecycle ─────────────────────────────────────────────
    def wipe_all_sessions(self) -> None:
        import shutil
        with self._lock:
            self._reload_all_meta()
            for sid in self._scan_sessions():
                self._load_shipped(sid)  # wipe rule reads season_name only
            self._mem.wipe_all_sessions()
            survivors = set(self._mem.sessions.keys())
            for sid in self._scan_sessions():
                if sid not in survivors:
                    shutil.rmtree(self._sdir(sid), ignore_errors=True)
                    self._touched.pop(sid, None)
                    self._loaded.pop(sid, None)

    def delete_session(self, session_id: str) -> None:
        import shutil
        sid = str(session_id)
        with self._lock:
            shutil.rmtree(self._sdir(sid), ignore_errors=True)
            self._drop_from_mem(sid)

    # ── SOC_GAME_SESSION ─────────────────────────────────────────────
    def save_session(self, row: Mapping[str, Any]) -> None:
        sid = str(row["session_id"])
        with self._lock:
            self._mem.save_session(row)
            self._loaded.setdefault(sid, set()).update({F_META, F_STATE})
            self._write_meta(sid)
            self._write_state(sid)

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._exists(session_id):
                self._drop_from_mem(session_id)
                return None
            # During a write batch the state blob is deferred (RAM is
            # authoritative); re-reading the stale disk copy would rewind
            # the simulation, so serve RAM when both parts are loaded.
            if self._batch and {F_META, F_STATE} <= self._loaded.get(session_id, set()):
                return self._mem.load_session(session_id)
            self._load_meta(session_id)
            self._load_state(session_id)
            self._loaded.setdefault(session_id, set()).update({F_META, F_STATE})
            return self._mem.load_session(session_id)

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload_all_meta()
            return self._mem.list_sessions()

    def latest_session(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._reload_all_meta()
            return self._mem.latest_session()

    def bulk_session_scores(self) -> Dict[str, Dict[str, int]]:
        with self._lock:
            self._reload_all_meta()
            # Canonical scoring needs BOTH parcel tables: SHIPPED for the
            # tier-weighted value and HOARD for the green endgame penalty
            # + season-end vault-RED fire-sale. Both files are small, so
            # the picker stays a single fast pass (no full hydration).
            for sid in list(self._mem.sessions.keys()):
                self._load_shipped(sid)
                self._load_hoard(sid)
            return self._mem.bulk_session_scores()

    # ── SOC_SQUARE_IDENTITY ──────────────────────────────────────────
    def upsert_square_identity(
        self, session_id: str, entries: List[Mapping[str, Any]]
    ) -> None:
        with self._lock:
            self._ensure_part(session_id, F_SQUARE)
            self._mem.upsert_square_identity(session_id, entries)
            self._write_square(session_id)

    # ── SOC_ASSET_RECORD ─────────────────────────────────────────────
    def upsert_asset_records(
        self, session_id: str, records: List[Mapping[str, Any]]
    ) -> None:
        with self._lock:
            self._ensure_part(session_id, F_ASSET)
            self._mem.upsert_asset_records(session_id, records)
            self._write_asset(session_id)

    # ── SOC_ENTITY_STATE ─────────────────────────────────────────────
    def replace_entity_state(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None:
        with self._lock:
            self._mem.replace_entity_state(session_id, rows)
            self._loaded.setdefault(session_id, set()).add(F_ENTITY)
            self._write_entity(session_id)

    # ── SOC_GRID_CELL ────────────────────────────────────────────────
    def upsert_grid_cells(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None:
        with self._lock:
            self._ensure_part(session_id, F_GRID)
            self._mem.upsert_grid_cells(session_id, rows)
            self._write_grid(session_id)

    # ── SOC_HOARD_PARCEL / SOC_SHIPPED_PARCEL ────────────────────────
    def replace_hoard(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None:
        with self._lock:
            self._ensure_part(session_id, F_HOARD)
            self._mem.replace_hoard(session_id, owner, rows)
            self._write_hoard(session_id)

    def replace_shipped(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None:
        with self._lock:
            self._ensure_part(session_id, F_SHIPPED)
            self._mem.replace_shipped(session_id, owner, rows)
            self._write_shipped(session_id)

    def replace_hoard_bundle(
        self, session_id: str, by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        with self._lock:
            self._ensure_part(session_id, F_HOARD)
            self._mem.replace_hoard_bundle(session_id, by_owner)
            self._write_hoard(session_id)

    def replace_shipped_bundle(
        self, session_id: str, by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        with self._lock:
            self._ensure_part(session_id, F_SHIPPED)
            self._mem.replace_shipped_bundle(session_id, by_owner)
            self._write_shipped(session_id)

    def list_hoard(self, session_id: str, owner: str) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload_part(session_id, F_HOARD)
            return self._mem.list_hoard(session_id, owner)

    def list_shipped(self, session_id: str, owner: str) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload_part(session_id, F_SHIPPED)
            return self._mem.list_shipped(session_id, owner)

    # ── SOC_POLICY_QUEUE ─────────────────────────────────────────────
    def upsert_policy(
        self, session_id: str, day: int, player: str, queue: List[Any]
    ) -> None:
        with self._lock:
            self._ensure_part(session_id, F_POLICIES)
            self._mem.upsert_policy(session_id, day, player, queue)
            self._write_policies(session_id)

    def list_policies(self, session_id: str, day: int) -> Dict[str, List[Any]]:
        with self._lock:
            self._reload_part(session_id, F_POLICIES)
            return self._mem.list_policies(session_id, day)

    # ── SOC_GAME_LOG ─────────────────────────────────────────────────
    def append_log(
        self, session_id: str, day: int, entries: List[Mapping[str, Any]]
    ) -> None:
        with self._lock:
            self._ensure_part(session_id, F_LOG)
            self._mem.append_log(session_id, day, entries)
            self._write_log(session_id)

    def list_log(
        self,
        session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload_part(session_id, F_LOG)
            return self._mem.list_log(session_id, day_from, day_to, limit)

    # ── SOC_REPLAY_FRAME (append-only sidecar) ───────────────────────
    def append_replay_frames(
        self, session_id: str, day: int, frames: List[Mapping[str, Any]]
    ) -> None:
        with self._lock:
            # Reconcile RAM with disk so global_idx continues correctly even
            # if a prior read left frames unloaded; then append O(new) lines.
            on_disk = self._read_frames(session_id)
            # E1b — idempotent per (session, day): drop any prior frames for
            # this day so a re-resolution (stale-read re-dispatch) can never
            # leave the replay with a duplicated day.
            deduped = [f for f in on_disk if int(f.get("day", -1)) != int(day)]
            replaced = len(deduped) != len(on_disk)
            self._mem.replay_frames[session_id] = deduped
            self._loaded.setdefault(session_id, set()).add(F_FRAMES)
            existing = len(deduped)
            self._mem.append_replay_frames(session_id, day, frames)
            all_rows = self._mem.replay_frames.get(session_id, [])
            new_rows = all_rows[existing:]
            if not new_rows and not replaced:
                return
            path = self._part(session_id, F_FRAMES)
            path.parent.mkdir(parents=True, exist_ok=True)
            if replaced:
                # Prior day rows were dropped — rewrite the whole sidecar so
                # the stale copy is physically gone, not just shadowed in RAM.
                with path.open("w", encoding="utf-8") as fh:
                    for row in all_rows:
                        fh.write(json.dumps(row, ensure_ascii=False))
                        fh.write("\n")
            else:
                with path.open("a", encoding="utf-8") as fh:
                    for row in new_rows:
                        fh.write(json.dumps(row, ensure_ascii=False))
                        fh.write("\n")

    def list_replay_frames(
        self,
        session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload_part(session_id, F_FRAMES)
            return self._mem.list_replay_frames(session_id, day_from, day_to)

    def day_index(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload_part(session_id, F_FRAMES)
            return self._mem.day_index(session_id)

    # ── SOC_AGENT_INVOCATION ─────────────────────────────────────────
    def append_agent_invocation(self, row: Mapping[str, Any]) -> None:
        self.append_agent_invocations([row])

    def append_agent_invocations(
        self, rows: Sequence[Mapping[str, Any]],
    ) -> None:
        rows = [r for r in rows if r]
        if not rows:
            return
        with self._lock:
            touched: set[str] = set()
            for r in rows:
                sid = str(r["session_id"])
                self._ensure_part(sid, F_AGENT)
                self._mem.append_agent_invocation(r)
                touched.add(sid)
            for sid in touched:
                self._write_agent(sid)

    def list_agent_invocations(
        self, session_id: str, day: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload_part(session_id, F_AGENT)
            return self._mem.list_agent_invocations(session_id, day)
