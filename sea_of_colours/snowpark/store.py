"""Storage protocol for the Snowflake-facing engine layer.

Every method maps 1:1 to a SOC_* table or view in
``snowflake/soc_schema.sql``. The :class:`InMemorySocStore` keeps the same
shape in-process so the engine module is fully testable without a live
Snowflake account; :class:`SnowparkSocStore` (Phase 3) implements the
same protocol against a Snowpark session.

Convention:

* Tables that hold a session lifetime ``json_state`` blob (the single
  authoritative serialized :class:`~sea_of_colours.game.session.GameSession`)
  are exposed as a row dict keyed by ``session_id``.
* Tables that are append-only (logs, replay frames, agent invocations)
  expose ``append_*`` / ``list_*`` operations.
* Tables that are merge-targets (entities, grid cells, asset records,
  hoard / shipped slots, policy queues) expose ``upsert_*`` + ``list_*``.

Keep the surface minimal — the engine module is allowed to ask for what
it needs, not for SQL-style query primitives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence


class SocStore(Protocol):
    """Storage surface used by :mod:`sea_of_colours.snowpark.engine`."""

    # ── season lifecycle ─────────────────────────────────────────────
    def wipe_all_sessions(self) -> None:
        """Drop every row across every SOC_* table.

        The engine calls this from :func:`init_session` so each NEW GAME
        starts the season from a clean slate — a deliberate
        "one-season-at-a-time" model.         The future "save / restore"
        feature will copy the prior season into archive tables *before*
        invoking this method so nothing is lost.
        """

    def delete_session(self, session_id: str) -> None:
        """Permanently remove a single session and all its child rows.

        Powers the watcher's per-replay "delete" (bin) control. Unlike
        :meth:`wipe_all_sessions` this targets exactly one ``session_id``
        across every SOC_* table. Deleting a missing id is a no-op.
        """

    # ── SOC_GAME_SESSION ─────────────────────────────────────────────
    def save_session(self, row: Mapping[str, Any]) -> None: ...
    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]: ...
    def list_sessions(self) -> List[Dict[str, Any]]: ...
    def latest_session(self) -> Optional[Dict[str, Any]]: ...

    def bulk_session_scores(self) -> Dict[str, Dict[str, int]]:
        """Score lookup keyed by session_id → ``{"p1": int, "p2": int}``.

        Used by the watcher frontend's season picker to render
        leaderboards without an N-roundtrip score hydration loop. The
        Snowflake backend implements this as a single query over
        ``SOC_SESSION_STANDINGS`` (purity-summed across hoard +
        shipped, matching :meth:`GameSession.score_for`); the
        in-memory backend folds the per-session parcel lists.

        Sessions with no parcels yet return ``{"p1": 0, "p2": 0}``.
        Missing sessions are simply absent from the dict so callers
        should ``.get(sid, {"p1": 0, "p2": 0})`` defensively.
        """
        ...

    # ── SOC_SQUARE_IDENTITY ─────────────────────────────────────────
    def upsert_square_identity(
        self, session_id: str, entries: List[Mapping[str, Any]]
    ) -> None: ...

    # ── SOC_ASSET_RECORD ────────────────────────────────────────────
    def upsert_asset_records(
        self, session_id: str, records: List[Mapping[str, Any]]
    ) -> None: ...

    # ── SOC_ENTITY_STATE ────────────────────────────────────────────
    def replace_entity_state(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None: ...

    # ── SOC_GRID_CELL ───────────────────────────────────────────────
    def upsert_grid_cells(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None: ...

    # ── SOC_HOARD_PARCEL / SOC_SHIPPED_PARCEL ───────────────────────
    def replace_hoard(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None: ...
    def replace_shipped(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None: ...

    def replace_hoard_bundle(
        self,
        session_id: str,
        by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        """Replace hoard parcels for *all* listed owners in one round-trip.

        v0.9.6 — added so :func:`save_session_full` can collapse the
        per-player hoard write loop into a single DELETE + INSERT
        instead of one DELETE+INSERT per seat. The single-owner
        :meth:`replace_hoard` continues to exist for ad-hoc callers
        that only need to touch one seat.
        """
        ...

    def replace_shipped_bundle(
        self,
        session_id: str,
        by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        """Counterpart of :meth:`replace_hoard_bundle` for shipped parcels."""
        ...

    def list_hoard(self, session_id: str, owner: str) -> List[Dict[str, Any]]: ...
    def list_shipped(self, session_id: str, owner: str) -> List[Dict[str, Any]]: ...

    # ── SOC_POLICY_QUEUE ────────────────────────────────────────────
    def upsert_policy(
        self,
        session_id: str,
        day: int,
        player: str,
        queue: List[Any],
    ) -> None: ...
    def list_policies(
        self, session_id: str, day: int
    ) -> Dict[str, List[Any]]: ...

    # ── SOC_GAME_LOG ────────────────────────────────────────────────
    def append_log(
        self,
        session_id: str,
        day: int,
        entries: List[Mapping[str, Any]],
    ) -> None: ...
    def list_log(
        self,
        session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]: ...

    # ── SOC_REPLAY_FRAME ────────────────────────────────────────────
    def append_replay_frames(
        self, session_id: str, day: int, frames: List[Mapping[str, Any]]
    ) -> None: ...
    def list_replay_frames(
        self,
        session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
    ) -> List[Dict[str, Any]]: ...
    def day_index(self, session_id: str) -> List[Dict[str, Any]]: ...

    # ── SOC_AGENT_INVOCATION ────────────────────────────────────────
    def append_agent_invocation(self, row: Mapping[str, Any]) -> None: ...
    def append_agent_invocations(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> None: ...
    def list_agent_invocations(
        self, session_id: str, day: Optional[int] = None
    ) -> List[Dict[str, Any]]: ...


@dataclass
class InMemorySocStore:
    """Default storage backend — keeps SOC_* tables in process memory.

    Each attribute mirrors a Snowflake table or merge-target. The shape
    is intentionally row-of-dicts so the same payloads we ship into
    Snowpark INSERTs round-trip 1:1.
    """

    sessions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sessions_order: List[str] = field(default_factory=list)

    square_identity: Dict[str, Dict[tuple, Dict[str, Any]]] = field(default_factory=dict)
    asset_records: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    entity_state: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    grid_cells: Dict[str, Dict[tuple, Dict[str, Any]]] = field(default_factory=dict)
    hoard: Dict[str, Dict[str, List[Dict[str, Any]]]] = field(default_factory=dict)
    shipped: Dict[str, Dict[str, List[Dict[str, Any]]]] = field(default_factory=dict)
    policies: Dict[str, Dict[int, Dict[str, List[Any]]]] = field(default_factory=dict)
    log_rows: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    replay_frames: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    agent_invocations: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    # ── season lifecycle ─────────────────────────────────────────────
    def wipe_all_sessions(self) -> None:
        """Wipe every session EXCEPT eval-tagged ones.

        Mirrors :meth:`SnowparkSocStore.wipe_all_sessions` so the
        in-memory and Snowflake backends produce identical
        post-NEW-GAME states. Sessions whose ``season_name`` starts
        with ``eval:`` are preserved across wipes — those belong
        to the ``/evals`` command center and a player clicking NEW
        GAME shouldn't nuke their evaluation history. Every
        per-session container is rebuilt to contain only the
        surviving (eval-tagged) entries.
        """
        survivors = {
            sid: row
            for sid, row in self.sessions.items()
            if str(row.get("season_name") or "").startswith("eval:")
        }
        keep = set(survivors)

        def _prune(d):
            return {sid: v for sid, v in d.items() if sid in keep}

        self.sessions = survivors
        self.sessions_order = [sid for sid in self.sessions_order if sid in keep]
        self.square_identity = _prune(self.square_identity)
        self.asset_records = _prune(self.asset_records)
        self.entity_state = _prune(self.entity_state)
        self.grid_cells = _prune(self.grid_cells)
        self.hoard = _prune(self.hoard)
        self.shipped = _prune(self.shipped)
        self.policies = _prune(self.policies)
        self.log_rows = _prune(self.log_rows)
        self.replay_frames = _prune(self.replay_frames)
        self.agent_invocations = _prune(self.agent_invocations)

    def delete_session(self, session_id: str) -> None:
        """Drop one session id from every per-session container."""
        sid = str(session_id)
        self.sessions.pop(sid, None)
        if sid in self.sessions_order:
            self.sessions_order.remove(sid)
        for container in (
            self.square_identity, self.asset_records, self.entity_state,
            self.grid_cells, self.hoard, self.shipped, self.policies,
            self.log_rows, self.replay_frames, self.agent_invocations,
        ):
            container.pop(sid, None)

    # ── SOC_GAME_SESSION ─────────────────────────────────────────────
    def save_session(self, row: Mapping[str, Any]) -> None:
        sid = str(row["session_id"])
        if sid not in self.sessions:
            self.sessions_order.append(sid)
        self.sessions[sid] = dict(row)

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.sessions.get(session_id)

    def list_sessions(self) -> List[Dict[str, Any]]:
        # Newest-first to match :class:`SnowparkSocStore.list_sessions`
        # (``ORDER BY last_touched_at DESC``). ``sessions_order`` is
        # append-only by insertion, so the most recently created
        # session is the last entry — reversing gives the watcher's
        # season picker the same "freshest at the top" presentation
        # regardless of backend.
        return [dict(self.sessions[sid]) for sid in reversed(self.sessions_order)]

    def latest_session(self) -> Optional[Dict[str, Any]]:
        if not self.sessions_order:
            return None
        return dict(self.sessions[self.sessions_order[-1]])

    def bulk_session_scores(self) -> Dict[str, Dict[str, int]]:
        """In-memory standings — canonical per-seat score per session.

        Uses :func:`sea_of_colours.game.session.compute_player_score`
        (the SAME scorer behind :meth:`GameSession.score_for` and the
        end-of-game screen) folded over each session's SHIPPED + HOARD
        parcel tables, so the watcher picker, the HUD, and the results
        screen agree. N-seat aware (every seat in the session's
        ``players`` list, not just p1/p2) and endgame-penalty aware
        (the season-end vault-RED fire-sale only realises once the
        session phase is ``season_complete``).
        """
        from sea_of_colours.game.session import compute_player_score

        # SHIPPED / HOARD rows wrap the canonical in-session parcel (with
        # the catapult-stamped ``effective_purity`` / ``score_tier``) in a
        # ``payload`` field — the same dict ``_hydrate_session`` rebuilds
        # ``shipped_squares`` / ``hoard_squares`` from. Unwrap it so the
        # scorer sees identical parcels to the live game (the bare row
        # only carries the ``origin_*`` column mapping).
        def _canon(rows: List[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
            out_rows: List[Mapping[str, Any]] = []
            for r in rows:
                payload = r.get("payload")
                out_rows.append(payload if isinstance(payload, dict) and payload else r)
            return out_rows

        out: Dict[str, Dict[str, int]] = {}
        for sid in self.sessions_order:
            meta = self.sessions.get(sid, {}) or {}
            complete = str(meta.get("phase", "")) == "season_complete"
            shipped_by_owner = self.shipped.get(sid, {})
            hoard_by_owner = self.hoard.get(sid, {})
            seats = meta.get("players")
            if not seats:
                seats = sorted(set(shipped_by_owner) | set(hoard_by_owner)) \
                    or ["p1", "p2"]
            scores: Dict[str, int] = {}
            for seat in seats:
                seat = str(seat)
                scores[seat] = compute_player_score(
                    _canon(shipped_by_owner.get(seat, [])),
                    _canon(hoard_by_owner.get(seat, [])),
                    is_complete=complete,
                )
            out[sid] = scores
        return out

    # ── SOC_SQUARE_IDENTITY ──────────────────────────────────────────
    def upsert_square_identity(
        self, session_id: str, entries: List[Mapping[str, Any]]
    ) -> None:
        bucket = self.square_identity.setdefault(session_id, {})
        for row in entries:
            bucket[(int(row["x"]), int(row["y"]))] = dict(row)

    # ── SOC_ASSET_RECORD ─────────────────────────────────────────────
    def upsert_asset_records(
        self, session_id: str, records: List[Mapping[str, Any]]
    ) -> None:
        bucket = self.asset_records.setdefault(session_id, {})
        for row in records:
            bucket[str(row["asset_id"])] = dict(row)

    # ── SOC_ENTITY_STATE ─────────────────────────────────────────────
    def replace_entity_state(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None:
        self.entity_state[session_id] = {
            str(r["entity_id"]): dict(r) for r in rows
        }

    # ── SOC_GRID_CELL ────────────────────────────────────────────────
    def upsert_grid_cells(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None:
        bucket = self.grid_cells.setdefault(session_id, {})
        for r in rows:
            bucket[(int(r["x"]), int(r["y"]))] = dict(r)

    # ── SOC_HOARD_PARCEL / SOC_SHIPPED_PARCEL ────────────────────────
    def replace_hoard(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None:
        self.hoard.setdefault(session_id, {})[owner] = [dict(r) for r in rows]

    def replace_shipped(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None:
        self.shipped.setdefault(session_id, {})[owner] = [dict(r) for r in rows]

    def replace_hoard_bundle(
        self,
        session_id: str,
        by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        for owner, rows in by_owner.items():
            self.replace_hoard(session_id, owner, list(rows))

    def replace_shipped_bundle(
        self,
        session_id: str,
        by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        for owner, rows in by_owner.items():
            self.replace_shipped(session_id, owner, list(rows))

    def list_hoard(self, session_id: str, owner: str) -> List[Dict[str, Any]]:
        return list(self.hoard.get(session_id, {}).get(owner, []))

    def list_shipped(self, session_id: str, owner: str) -> List[Dict[str, Any]]:
        return list(self.shipped.get(session_id, {}).get(owner, []))

    # ── SOC_POLICY_QUEUE ─────────────────────────────────────────────
    def upsert_policy(
        self,
        session_id: str,
        day: int,
        player: str,
        queue: List[Any],
    ) -> None:
        bucket = self.policies.setdefault(session_id, {}).setdefault(day, {})
        bucket[player] = list(queue)

    def list_policies(
        self, session_id: str, day: int
    ) -> Dict[str, List[Any]]:
        return dict(self.policies.get(session_id, {}).get(day, {}))

    # ── SOC_GAME_LOG ─────────────────────────────────────────────────
    def append_log(
        self,
        session_id: str,
        day: int,
        entries: List[Mapping[str, Any]],
    ) -> None:
        bucket = self.log_rows.setdefault(session_id, [])
        next_seq = (max((r["seq"] for r in bucket), default=0)) + 1
        for offset, entry in enumerate(entries):
            bucket.append({
                "session_id": session_id,
                "day": day,
                "seq": next_seq + offset,
                "level": str(entry.get("level", "info")),
                "text": str(entry.get("text", "")),
                "ts": entry.get("ts"),
            })

    def list_log(
        self,
        session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        rows = list(self.log_rows.get(session_id, []))
        if day_from is not None:
            rows = [r for r in rows if r["day"] >= day_from]
        if day_to is not None:
            rows = [r for r in rows if r["day"] <= day_to]
        rows.sort(key=lambda r: r["seq"])
        if limit is not None:
            rows = rows[-int(limit):]
        return rows

    # ── SOC_REPLAY_FRAME ─────────────────────────────────────────────
    def append_replay_frames(
        self, session_id: str, day: int, frames: List[Mapping[str, Any]]
    ) -> None:
        bucket = self.replay_frames.setdefault(session_id, [])
        # E1b — idempotent per (session, day): a re-resolved night must not
        # stack a second copy of its frames onto the replay.
        stale = [r for r in bucket if int(r.get("day", -1)) == int(day)]
        if stale:
            bucket[:] = [r for r in bucket if int(r.get("day", -1)) != int(day)]
        next_global = (max((r["global_idx"] for r in bucket), default=-1)) + 1
        for i, frame in enumerate(frames):
            row = dict(frame)
            row["session_id"] = session_id
            row["day"] = day
            row["frame_idx"] = int(row.get("frame_idx", i))
            row["global_idx"] = next_global + i
            bucket.append(row)

    def list_replay_frames(
        self,
        session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        rows = list(self.replay_frames.get(session_id, []))
        if day_from is not None:
            rows = [r for r in rows if r["day"] >= day_from]
        if day_to is not None:
            rows = [r for r in rows if r["day"] <= day_to]
        rows.sort(key=lambda r: r["global_idx"])
        return rows

    def day_index(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self.replay_frames.get(session_id, [])
        by_day: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            day = int(r["day"])
            d = by_day.setdefault(
                day,
                {
                    "day": day,
                    "frame_count": 0,
                    "first_global_idx": r["global_idx"],
                    "last_global_idx": r["global_idx"],
                    "first_caption": r.get("caption"),
                    "last_caption": r.get("caption"),
                },
            )
            d["frame_count"] += 1
            if r["global_idx"] < d["first_global_idx"]:
                d["first_global_idx"] = r["global_idx"]
                d["first_caption"] = r.get("caption")
            if r["global_idx"] > d["last_global_idx"]:
                d["last_global_idx"] = r["global_idx"]
                d["last_caption"] = r.get("caption")
        return [by_day[k] for k in sorted(by_day)]

    # ── SOC_AGENT_INVOCATION ─────────────────────────────────────────
    def append_agent_invocation(self, row: Mapping[str, Any]) -> None:
        sid = str(row["session_id"])
        bucket = self.agent_invocations.setdefault(sid, [])
        next_seq = (max((r["seq"] for r in bucket), default=0)) + 1
        out = dict(row)
        out.setdefault("seq", next_seq)
        bucket.append(out)

    def append_agent_invocations(
        self, rows: Sequence[Mapping[str, Any]],
    ) -> None:
        for r in rows:
            if r:
                self.append_agent_invocation(r)

    def list_agent_invocations(
        self, session_id: str, day: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        rows = list(self.agent_invocations.get(session_id, []))
        if day is not None:
            rows = [r for r in rows if r.get("day") == day]
        rows.sort(key=lambda r: r.get("seq", 0))
        return rows
