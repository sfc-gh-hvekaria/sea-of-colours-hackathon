"""Write-coalescing, read-caching decorator over any :class:`SocStore`.

Why this exists
===============

On the file backend a store call is a function call plus a small write:
~2ms. On Snowflake every store call is a separate HTTPS request that costs
**~300ms before it does any work at all** — measured, and not reducible from
the client (see ``docs/SNOWFLAKE_LATENCY_BRIEF.md`` §T1.1: 97.5% of a
``SELECT 1`` is spent blocked on the response, with a 12ms network RTT and
27ms of server time).

So the cost of a turn is essentially ``statements x 300ms``, and the engine
issues 11-14 of them. Several are pure waste:

* ``load_session`` runs 3x per turn, re-reading state this process already
  holds — 1.6s for nothing.
* ``save_session`` runs 2x, writing the same ~375KB blob twice.
* ``append_log`` runs 2-3x, one INSERT each, for a handful of lines.

Multi-statement requests do NOT fix this — measured at n=1/3/6 they are a
wash with separate statements, because Snowflake runs the batch sequentially
and the driver polls per result set. The only lever is issuing fewer
requests, which is what this class does.

Durability model
================

Two tiers, because they have different consequences if lost:

**Per turn (flushed by** :meth:`flush_turn` **, called from**
``engine.save_session_full``\\ **)** — the authoritative ``json_state`` row,
the policy queue, and the parcel bundles. These are game state and
cross-player handoff; losing them would corrupt or rewind a game.

**Per day (flushed by** :meth:`flush` **)** — ``SOC_GAME_LOG``,
``SOC_REPLAY_FRAME`` and ``SOC_AGENT_INVOCATION``. These are append-only and
nothing reads them mid-turn except ``list_log``, which this class serves from
the buffer. A hard crash mid-day loses at most one day of LOG text and replay
animation for that season; the game itself is intact and resumable.

A day rollover is detected from the ``day`` on the session row, so no caller
has to remember to flush. ``atexit`` flushes too, so a normal exit never
loses anything.

The ordering guarantee (§7)
===========================

``_hydrate_session`` reads ``SOC_GAME_SESSION.json_state`` exclusively, so
that row must be written LAST — after every sibling write — or a following
hydrate can observe a pre-resolution snapshot and the season runner
re-resolves the same night ("day 3 repeats three times").

Previously that was a convention spread across 8 ``save_session_full`` call
sites. Here it is one line at the bottom of :meth:`_emit`, in one place, and
it cannot be got wrong by a caller. Buffering therefore *strengthens* the
guarantee rather than threatening it.
"""

from __future__ import annotations

import atexit
import os
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def buffering_enabled() -> bool:
    """``SOC_BUFFERED_STORE`` — on unless explicitly disabled.

    On by default since v1.43. It shipped off while it was new, which
    meant the canonical run command never used it and the saving was
    only ever collected by whoever had read the latency brief.

    Measured on a full seven-day season rather than a bench, because
    that is what somebody actually waits for: 97.9s → 62.2s with
    heuristic seats, 254.5s → 205.3s with V12 in one. Smaller than the
    per-turn store figures suggest — a real season is mostly engine and
    model time, which no amount of buffering touches.

    What it costs, stated plainly: game state is still flushed every
    turn, so a crash can never rewind or corrupt a game. The deferred
    tier is the day's LOG text, replay frames and invocation rows, and
    a hard crash mid-day loses that day's worth of them. You lose the
    animation and the transcript of a day, never the game.

    ``SOC_BUFFERED_STORE=0`` is the way back, and the thing to try first
    if a Snowflake season ever looks like it is missing history.
    """
    return os.environ.get(
        "SOC_BUFFERED_STORE", "1"
    ).strip().lower() in {"1", "true", "yes", "on"}


class BufferedSocStore:
    """Buffer writes and cache reads in front of ``inner``.

    Not thread-safe across *different* sessions by design — it is scoped to
    one process's view of the games it is playing, and a re-entrant lock
    guards the buffer itself so the ``save_session_full`` thread pool cannot
    corrupt it.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._lock = threading.RLock()
        # per-turn tier
        self._sessions: Dict[str, Mapping[str, Any]] = {}
        self._policies: Dict[Tuple[str, int, str], List[Any]] = {}
        self._hoard: Dict[str, Mapping[str, Any]] = {}
        self._shipped: Dict[str, Mapping[str, Any]] = {}
        # per-day tier
        self._log: Dict[Tuple[str, int], List[Mapping[str, Any]]] = {}
        self._frames: Dict[Tuple[str, int], List[Mapping[str, Any]]] = {}
        self._invocations: List[Mapping[str, Any]] = []
        # write-only projections, buffered for completeness
        self._projections: List[Tuple[str, tuple]] = []
        # read caches
        self._session_cache: Dict[str, Optional[Mapping[str, Any]]] = {}
        # Sessions this process has written, and so may answer from cache.
        # See ``load_session`` for why a reader must not.
        self._written: set[str] = set()
        self._log_durable: Dict[str, List[Dict[str, Any]]] = {}
        self._day_seen: Dict[str, int] = {}
        self._flushes = 0
        atexit.register(self._atexit_flush)

    # ── stats, for the bench and tests ────────────────────────────
    @property
    def flush_count(self) -> int:
        return self._flushes

    def _atexit_flush(self) -> None:
        try:
            self.flush()
        except Exception:  # noqa: BLE001 — never raise from atexit
            pass

    # ── SOC_GAME_SESSION ──────────────────────────────────────────
    def save_session(self, row: Mapping[str, Any]) -> None:
        sid = str(row["session_id"])
        day = int(row.get("day", 0) or 0)
        with self._lock:
            # A day rollover means the previous day's append-only buffers are
            # complete, so land them now. Done here rather than in the runner
            # so no caller has to know about flush boundaries.
            previous = self._day_seen.get(sid)
            if previous is not None and day != previous:
                self._flush_locked(appends=True)
            self._day_seen[sid] = day
            self._sessions[sid] = row
            # Write-through: a later load_session in this process must see what
            # we just wrote, which is also what makes the §7 stale-read
            # impossible for a buffered reader.
            self._session_cache[sid] = row
            self._written.add(sid)
            end_of_season = str(row.get("phase", "")) == "season_complete"
        if end_of_season:
            self.flush()

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Cached only for sessions *this* process is playing.

        v1.43 — the cache has no expiry, so if it answered every read it
        would pin a session at whatever this process last saw of it, for
        the life of the process. That is fine for the writer, which is
        the only one that can change it, and wrong for anyone else:
        a server with a Snowflake game open while ``soc season`` advances
        it in another terminal would show a frozen board and never
        recover. The brief called this out at §T2 before the store
        existed; making buffering the default is what made it reachable.

        So the cache is scoped to sessions we have written. A pure reader
        pays full price on every read, which is what it was paying before
        buffering existed, and the turn loop keeps its saving because a
        turn writes before it re-reads. Two processes *writing* one
        session remains unsafe, but it was unsafe before this store too —
        §V6's ``state_version`` is the fix for that, not a cache policy.
        """
        with self._lock:
            if session_id in self._written and session_id in self._session_cache:
                cached = self._session_cache[session_id]
                return dict(cached) if cached is not None else None
        row = self._inner.load_session(session_id)
        with self._lock:
            self._session_cache[session_id] = row
        return row

    # ── SOC_POLICY_QUEUE ──────────────────────────────────────────
    def upsert_policy(
        self, session_id: str, day: int, player: str, queue: List[Any],
    ) -> None:
        with self._lock:
            self._policies[(session_id, int(day), player)] = queue

    # ── parcels ───────────────────────────────────────────────────
    def replace_hoard_bundle(
        self, session_id: str, by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        with self._lock:
            self._hoard[session_id] = by_owner

    def replace_shipped_bundle(
        self, session_id: str, by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        with self._lock:
            self._shipped[session_id] = by_owner

    # ── SOC_GAME_LOG ──────────────────────────────────────────────
    def append_log(
        self, session_id: str, day: int, entries: List[Mapping[str, Any]],
    ) -> None:
        if not entries:
            return
        with self._lock:
            self._log.setdefault((session_id, int(day)), []).extend(entries)

    def list_log(
        self,
        session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Durable rows plus anything still buffered, without a flush.

        The LOG panel is read once per turn. Flushing here would defeat the
        per-day deferral entirely, so instead we merge. ``seq`` for pending
        entries is synthesised as ``max(durable seq) + n``, which is exactly
        what the INSERT's folded ``MAX(seq)+1`` will assign, so the numbers
        the UI shows now match the numbers that land.
        """
        with self._lock:
            pending = {k: list(v) for k, v in self._log.items()
                       if k[0] == session_id}
        if session_id not in self._log_durable:
            durable = self._inner.list_log(session_id) or []
            with self._lock:
                self._log_durable[session_id] = list(durable)
        rows = list(self._log_durable[session_id])
        if pending:
            next_seq = max((int(r.get("seq", 0) or 0) for r in rows), default=0)
            for (_sid, day), entries in sorted(pending.items()):
                for entry in entries:
                    next_seq += 1
                    rows.append({
                        "session_id": session_id,
                        "day": int(day),
                        "seq": next_seq,
                        "level": entry.get("level", "info"),
                        "text": entry.get("text", ""),
                        "ts": entry.get("ts"),
                    })
        if day_from is not None:
            rows = [r for r in rows if int(r.get("day", 0) or 0) >= int(day_from)]
        if day_to is not None:
            rows = [r for r in rows if int(r.get("day", 0) or 0) <= int(day_to)]
        rows.sort(key=lambda r: (int(r.get("day", 0) or 0), int(r.get("seq", 0) or 0)))
        if limit is not None:
            rows = rows[-int(limit):]
        return rows

    # ── SOC_REPLAY_FRAME ──────────────────────────────────────────
    def append_replay_frames(
        self, session_id: str, day: int, frames: List[Mapping[str, Any]],
    ) -> None:
        if not frames:
            return
        with self._lock:
            self._frames.setdefault((session_id, int(day)), []).extend(frames)

    def day_index(self, session_id: str) -> List[Dict[str, Any]]:
        """Durable index plus pending frame counts.

        ``get_session_status`` only sums ``frame_count`` (it gates the replay
        animation on a non-zero total), so pending days are reported with the
        right count and ``first/last_global_idx`` left as ``None`` — those are
        assigned server-side by the INSERT and are unknowable until it runs.
        ``_season_integrity`` already skips entries with a null index, and it
        runs after a flush.
        """
        durable = list(self._inner.day_index(session_id) or [])
        with self._lock:
            pending = {k[1]: len(v) for k, v in self._frames.items()
                       if k[0] == session_id}
        if not pending:
            return durable
        by_day = {int(d.get("day", 0) or 0): dict(d) for d in durable}
        for day, extra in pending.items():
            entry = by_day.setdefault(
                day, {"day": day, "frame_count": 0,
                      "first_global_idx": None, "last_global_idx": None},
            )
            entry["frame_count"] = int(entry.get("frame_count", 0) or 0) + extra
            entry["first_global_idx"] = None
            entry["last_global_idx"] = None
        return [by_day[d] for d in sorted(by_day)]

    # ── SOC_AGENT_INVOCATION ──────────────────────────────────────
    def append_agent_invocation(self, row: Mapping[str, Any]) -> None:
        with self._lock:
            self._invocations.append(row)

    def append_agent_invocations(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        with self._lock:
            self._invocations.extend(rows)

    # ── write-only projections ────────────────────────────────────
    def upsert_square_identity(self, session_id: str, entries: List[Mapping[str, Any]]) -> None:
        with self._lock:
            self._projections.append(("upsert_square_identity", (session_id, entries)))

    def upsert_asset_records(self, session_id: str, records: List[Mapping[str, Any]]) -> None:
        with self._lock:
            self._projections.append(("upsert_asset_records", (session_id, records)))

    def replace_entity_state(self, session_id: str, rows: List[Mapping[str, Any]]) -> None:
        with self._lock:
            self._projections.append(("replace_entity_state", (session_id, rows)))

    def upsert_grid_cells(self, session_id: str, rows: List[Mapping[str, Any]]) -> None:
        with self._lock:
            self._projections.append(("upsert_grid_cells", (session_id, rows)))

    # ── flushing ──────────────────────────────────────────────────
    def flush_turn(self) -> None:
        """Land game state: policies, parcels, then the authoritative row.

        Called from ``engine.save_session_full``. Leaves the append-only
        buffers alone — those are the per-day tier.
        """
        with self._lock:
            self._flush_locked(appends=False)

    def flush(self) -> None:
        """Land everything, append-only buffers included."""
        with self._lock:
            self._flush_locked(appends=True)

    def _flush_locked(self, *, appends: bool) -> None:
        if appends:
            frames = self._frames
            log = self._log
            invocations = self._invocations
            self._frames, self._log, self._invocations = {}, {}, []
        else:
            frames, log, invocations = {}, {}, []
        policies, hoard, shipped = self._policies, self._hoard, self._shipped
        sessions, projections = self._sessions, self._projections
        self._policies, self._hoard, self._shipped = {}, {}, {}
        self._sessions, self._projections = {}, []
        if not any((frames, log, invocations, policies, hoard, shipped,
                    sessions, projections)):
            return
        self._flushes += 1
        self._emit(frames, log, invocations, policies, hoard, shipped,
                   sessions, projections)

    def _emit(self, frames, log, invocations, policies, hoard, shipped,
              sessions, projections) -> None:
        """Issue the buffered writes. THE SESSION ROW GOES LAST — see §7."""
        for (sid, day), rows in sorted(frames.items()):
            self._inner.append_replay_frames(sid, day, rows)
        for (sid, day), entries in sorted(log.items()):
            self._inner.append_log(sid, day, entries)
            # the durable read cache is now stale for this session
            self._log_durable.pop(sid, None)
        if invocations:
            self._inner.append_agent_invocations(list(invocations))
        for (sid, day, player), queue in sorted(policies.items()):
            self._inner.upsert_policy(sid, day, player, queue)
        for sid, by_owner in sorted(hoard.items()):
            self._inner.replace_hoard_bundle(sid, by_owner)
        for sid, by_owner in sorted(shipped.items()):
            self._inner.replace_shipped_bundle(sid, by_owner)
        for method, args in projections:
            getattr(self._inner, method)(*args)
        # LAST, unconditionally. Do not move.
        for _sid, row in sorted(sessions.items()):
            self._inner.save_session(row)

    # ── everything else ───────────────────────────────────────────
    def __getattr__(self, name: str) -> Any:
        """Flush, then delegate.

        Reached for anything this class does not override — every remaining
        read (``list_replay_frames``, ``list_policies``,
        ``list_agent_invocations``, ``list_hoard``, ``list_shipped``,
        ``bulk_session_scores``, ``list_sessions``, ``latest_session``), the
        destructive paths (``wipe_all_sessions``, ``delete_session``), and
        anything added to the protocol later. Defaulting to a flush means an
        unforeseen reader is correct out of the box; it just pays a round trip.

        Non-callables pass through untouched, which is what keeps
        ``backend.snowpark_session_for`` working: it does
        ``getattr(store, "session", None)`` to decide whether a game's agent
        memory may be persisted to ``SOC_AGENT_MEMORY``. Wrapping a Snowflake
        store must still yield that Session, and wrapping an in-memory store
        must still yield ``None`` — see
        ``test_snowpark_session_for_still_resolves_through_the_wrapper``.
        """
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def _flushed(*args: Any, **kwargs: Any) -> Any:
            self.flush()
            return attr(*args, **kwargs)

        return _flushed
