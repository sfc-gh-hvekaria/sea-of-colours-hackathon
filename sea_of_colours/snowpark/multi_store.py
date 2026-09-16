"""Composite :class:`SocStore` — Snowflake primary + local file secondary.

The app historically ran a *single* backend (``SOC_BACKEND`` = ``snowflake``
*or* ``file`` *or* ``memory``), which meant a watcher could only ever see one
store at a time: either your real Snowflake history or the offline bot
seasons under ``./seasons/`` — never both. :class:`CompositeSocStore` removes
that split. It fronts two stores at once:

* **primary** — Snowflake (the source of truth). All NEW games are written
  here, and it owns your durable season history.
* **secondary** — the local :class:`FileSocStore` (read-merged). Offline,
  cross-process bot seasons stay visible without touching Snowflake.

Behaviour:

* :meth:`list_sessions` returns the *union* of both stores, tagging each row
  with ``source`` (``"snowflake"`` / ``"local"``) so the picker can show an
  icon. Duplicates (same ``session_id``) prefer the primary.
* Every per-session call routes to whichever store owns that id (membership
  is learned from ``list_sessions`` and cached; unknown ids are probed
  locally-first, then primary).
* New sessions are written to the primary; if Snowflake is unreachable the
  store **degrades gracefully** to local-only (new games persist to the file
  store) and reports ``snowflake: down`` via :meth:`health` so the UI badge
  can reflect it.

The primary is created lazily through a factory so importing/starting the
app never blocks on a Snowflake connection — the warehouse is only contacted
on first real use, and a failure there is caught rather than fatal.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from sea_of_colours.snowpark.store import SocStore

PRIMARY = "primary"
SECONDARY = "secondary"

SOURCE_LABEL = {PRIMARY: "snowflake", SECONDARY: "local"}


class CompositeSocStore:
    """Read-merge two :class:`SocStore` backends; write to the primary.

    :param primary_factory: zero-arg callable returning the primary store
        (Snowflake). Called lazily on first primary access; an exception is
        caught and the store degrades to local-only.
    :param secondary: the always-available local store (file-backed).
    """

    def __init__(
        self,
        primary_factory: Callable[[], SocStore],
        secondary: SocStore,
    ) -> None:
        self._primary_factory = primary_factory
        self._secondary = secondary
        self._primary: Optional[SocStore] = None
        self._primary_attempted = False
        self._primary_ok = False
        # session_id -> PRIMARY | SECONDARY, learned from list_sessions /
        # save_session and used to route per-session calls without probing.
        self._ownership: Dict[str, str] = {}
        self._lock = threading.RLock()

    # ── primary lifecycle / health ───────────────────────────────────
    def _get_primary(self) -> Optional[SocStore]:
        """Return the primary store, lazily building it once. ``None`` when
        Snowflake is unreachable (the app then runs local-only)."""
        with self._lock:
            if self._primary is not None:
                return self._primary
            if self._primary_attempted:
                return None
            self._primary_attempted = True
            try:
                self._primary = self._primary_factory()
                self._primary_ok = True
            except Exception:
                self._primary = None
                self._primary_ok = False
            return self._primary

    def health(self) -> Dict[str, str]:
        """Backend health for the status badge.

        ``snowflake``: ``ok`` (connected) / ``down`` (tried, failed) /
        ``idle`` (not contacted yet). ``local`` is always ``ok`` (the file
        store needs only a writable dir)."""
        if self._primary_ok:
            sf = "ok"
        elif self._primary_attempted:
            sf = "down"
        else:
            sf = "idle"
        return {"snowflake": sf, "local": "ok"}

    # ── routing ──────────────────────────────────────────────────────
    def _route(self, sid: str, *, for_write: bool) -> Optional[SocStore]:
        """Resolve the store owning ``sid``.

        Cached owners answer instantly. Unknown ids are probed
        locally-first (cheap), then the primary. A write to an unknown id
        defaults to the primary (or the secondary if Snowflake is down) so
        new games land in the source of truth."""
        sid = str(sid)
        src = self._ownership.get(sid)
        if src == SECONDARY:
            return self._secondary
        if src == PRIMARY:
            p = self._get_primary()
            if p is not None:
                return p
            return self._secondary if for_write else None

        # Unknown — probe the cheap local store first.
        try:
            if self._secondary.load_session(sid) is not None:
                self._ownership[sid] = SECONDARY
                return self._secondary
        except Exception:
            pass
        p = self._get_primary()
        if p is not None:
            try:
                if p.load_session(sid) is not None:
                    self._ownership[sid] = PRIMARY
                    return p
            except Exception:
                self._primary_ok = False

        if for_write:
            target = self._get_primary() or self._secondary
            self._ownership[sid] = (
                PRIMARY if target is not self._secondary else SECONDARY
            )
            return target
        return None

    def _rstore(self, sid: str) -> Optional[SocStore]:
        return self._route(sid, for_write=False)

    def _wstore(self, sid: str) -> SocStore:
        store = self._route(sid, for_write=True)
        # _route(for_write=True) never returns None.
        return store  # type: ignore[return-value]

    # ── season lifecycle ─────────────────────────────────────────────
    def wipe_all_sessions(self) -> None:
        self._secondary.wipe_all_sessions()
        p = self._get_primary()
        if p is not None:
            try:
                p.wipe_all_sessions()
            except Exception:
                self._primary_ok = False
        self._ownership.clear()

    # ── SOC_GAME_SESSION ─────────────────────────────────────────────
    def save_session(self, row: Mapping[str, Any]) -> None:
        sid = str(row["session_id"])
        self._wstore(sid).save_session(row)

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        store = self._rstore(session_id)
        return store.load_session(session_id) if store is not None else None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Union of both stores, primary-first, each row tagged ``source``.

        Refreshes the ownership cache so subsequent per-session calls route
        without probing."""
        out: List[Dict[str, Any]] = []
        seen: set = set()
        with self._lock:
            self._ownership.clear()
            p = self._get_primary()
            if p is not None:
                try:
                    for r in p.list_sessions():
                        sid = str(r.get("session_id"))
                        row = dict(r)
                        row["source"] = SOURCE_LABEL[PRIMARY]
                        out.append(row)
                        seen.add(sid)
                        self._ownership[sid] = PRIMARY
                except Exception:
                    self._primary_ok = False
            try:
                for r in self._secondary.list_sessions():
                    sid = str(r.get("session_id"))
                    if sid in seen:
                        continue
                    row = dict(r)
                    row["source"] = SOURCE_LABEL[SECONDARY]
                    out.append(row)
                    seen.add(sid)
                    self._ownership[sid] = SECONDARY
            except Exception:
                pass
        return out

    def latest_session(self) -> Optional[Dict[str, Any]]:
        p = self._get_primary()
        if p is not None:
            try:
                latest = p.latest_session()
                if latest is not None:
                    return latest
            except Exception:
                self._primary_ok = False
        return self._secondary.latest_session()

    def bulk_session_scores(self) -> Dict[str, Dict[str, int]]:
        merged: Dict[str, Dict[str, int]] = {}
        try:
            merged.update(self._secondary.bulk_session_scores())
        except Exception:
            pass
        p = self._get_primary()
        if p is not None:
            try:
                merged.update(p.bulk_session_scores())
            except Exception:
                self._primary_ok = False
        return merged

    # ── per-session writes (merge targets / appends) ─────────────────
    def upsert_square_identity(
        self, session_id: str, entries: List[Mapping[str, Any]]
    ) -> None:
        self._wstore(session_id).upsert_square_identity(session_id, entries)

    def upsert_asset_records(
        self, session_id: str, records: List[Mapping[str, Any]]
    ) -> None:
        self._wstore(session_id).upsert_asset_records(session_id, records)

    def replace_entity_state(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None:
        self._wstore(session_id).replace_entity_state(session_id, rows)

    def upsert_grid_cells(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None:
        self._wstore(session_id).upsert_grid_cells(session_id, rows)

    def replace_hoard(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None:
        self._wstore(session_id).replace_hoard(session_id, owner, rows)

    def replace_shipped(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None:
        self._wstore(session_id).replace_shipped(session_id, owner, rows)

    def replace_hoard_bundle(
        self,
        session_id: str,
        by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        self._wstore(session_id).replace_hoard_bundle(session_id, by_owner)

    def replace_shipped_bundle(
        self,
        session_id: str,
        by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        self._wstore(session_id).replace_shipped_bundle(session_id, by_owner)

    def list_hoard(self, session_id: str, owner: str) -> List[Dict[str, Any]]:
        store = self._rstore(session_id)
        return store.list_hoard(session_id, owner) if store is not None else []

    def list_shipped(self, session_id: str, owner: str) -> List[Dict[str, Any]]:
        store = self._rstore(session_id)
        return store.list_shipped(session_id, owner) if store is not None else []

    # ── SOC_POLICY_QUEUE ─────────────────────────────────────────────
    def upsert_policy(
        self, session_id: str, day: int, player: str, queue: List[Any]
    ) -> None:
        self._wstore(session_id).upsert_policy(session_id, day, player, queue)

    def list_policies(self, session_id: str, day: int) -> Dict[str, List[Any]]:
        store = self._rstore(session_id)
        return store.list_policies(session_id, day) if store is not None else {}

    # ── SOC_GAME_LOG ─────────────────────────────────────────────────
    def append_log(
        self, session_id: str, day: int, entries: List[Mapping[str, Any]]
    ) -> None:
        self._wstore(session_id).append_log(session_id, day, entries)

    def list_log(
        self,
        session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        store = self._rstore(session_id)
        if store is None:
            return []
        return store.list_log(
            session_id, day_from=day_from, day_to=day_to, limit=limit
        )

    # ── SOC_REPLAY_FRAME ─────────────────────────────────────────────
    def append_replay_frames(
        self, session_id: str, day: int, frames: List[Mapping[str, Any]]
    ) -> None:
        self._wstore(session_id).append_replay_frames(session_id, day, frames)

    def list_replay_frames(
        self,
        session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        store = self._rstore(session_id)
        if store is None:
            return []
        return store.list_replay_frames(
            session_id, day_from=day_from, day_to=day_to
        )

    def day_index(self, session_id: str) -> List[Dict[str, Any]]:
        store = self._rstore(session_id)
        return store.day_index(session_id) if store is not None else []

    # ── SOC_AGENT_INVOCATION ─────────────────────────────────────────
    def append_agent_invocation(self, row: Mapping[str, Any]) -> None:
        sid = str(row["session_id"])
        self._wstore(sid).append_agent_invocation(row)

    def append_agent_invocations(
        self, rows: Sequence[Mapping[str, Any]],
    ) -> None:
        rows = [r for r in rows if r]
        if not rows:
            return
        # Group by session so each row lands in its correct write store, then
        # hand each store one batched call.
        by_sid: Dict[str, List[Mapping[str, Any]]] = {}
        for r in rows:
            by_sid.setdefault(str(r["session_id"]), []).append(r)
        for sid, batch in by_sid.items():
            self._wstore(sid).append_agent_invocations(batch)

    def list_agent_invocations(
        self, session_id: str, day: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        store = self._rstore(session_id)
        if store is None:
            return []
        return store.list_agent_invocations(session_id, day=day)

    # ── deletion (per-session) ───────────────────────────────────────
    def delete_session(self, session_id: str) -> None:
        """Remove one session from whichever store owns it."""
        store = self._rstore(session_id)
        if store is None:
            return
        store.delete_session(session_id)
        self._ownership.pop(str(session_id), None)
