"""Freeze a session at one night so the same decision can be replayed forever.

A session's whole restorable BOARD is the ``json_state`` blob on its
``SOC_GAME_SESSION`` row — :func:`engine._hydrate_session` reads nothing else.
The board is not the whole story a harness sees, though: three sibling tables
are keyed by ``session_id`` and all three are read while a prompt is built, so
a snapshot copies each of them under the new id.

* ``SOC_AGENT_MEMORY`` — the seat's journal (``session_id::player::day``).
  Without it LAST NIGHT has no intent, no plan and no reflection thread.
* ``SOC_REPLAY_FRAME`` — the per-hour engine truth. Without it the EXECUTION
  LOG falls through to its coarse fallback.
* ``SOC_GAME_LOG`` — what ``build_agent_view`` turns into
  ``last_night.my_orders``, which is what that fallback reads. Without BOTH,
  the block reported a night that plainly did happen as "nothing executed",
  directly above a yield line showing the parcels it banked, and the agent
  believed it.

Two operations:

``take``
    Called during a live season, before a seat plans. Writes the board exactly
    as that seat is about to see it.

``clone_for_run``
    Called at replay time. Copies a snapshot into a throwaway id so a harness
    can play the night without mutating the snapshot. Any number of clones can
    be taken from one snapshot, which is what makes a turn repeatable.

Snapshots are inert: nothing advances them, so they survive as a permanent
regression fixture for "did the fix change this decision?".
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Mapping, Optional

_SNAPSHOT_ENV = "SOC_SNAPSHOT_PREFIX"


def _decoded(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy ``row`` with ``json_state`` as a dict.

    The store hands the blob back as a VARIANT string but re-encodes on save,
    so passing it straight through double-encodes it and hydration then reads
    a ``str`` where it expects a mapping.
    """
    out = dict(row)
    blob = out.get("json_state")
    # Unwrap repeatedly: snapshots written before this was fixed are encoded
    # twice, and one pass would leave them as a str.
    for _ in range(4):
        if not isinstance(blob, str):
            break
        blob = json.loads(blob)
    out["json_state"] = blob
    return out


def snapshot_name(session_id: str, day: int, seat: str, prefix: str = "SNAP") -> str:
    return f"{prefix}_{session_id[:8]}_d{int(day)}_{seat}"


def _copy_memory(store: Any, src: str, dst: str) -> None:
    """Carry a seat's journal across to the copy. Best-effort by design —
    a missing journal degrades LAST NIGHT, it does not break the board."""
    exec_ = getattr(store, "_exec", None)
    if not callable(exec_):
        return
    try:
        exec_(
            "INSERT INTO SOC_AGENT_MEMORY "
            "(SESSION_ID, SEASON_NAME, PLAYER, KIND, PAYLOAD, UPDATED_AT) "
            f"SELECT '{dst}', SEASON_NAME, PLAYER, KIND, PAYLOAD, CURRENT_TIMESTAMP() "
            f"FROM SOC_AGENT_MEMORY WHERE SESSION_ID = '{src}'"
        )
    except Exception:
        pass


def _copy_child_rows(
    store: Any, table: str, src: str, dst: str, *, where: str = "",
) -> None:
    """Re-key one child table's rows onto ``dst``.

    Columns are read from the table rather than listed here so a schema
    addition can't silently drop a column from every future snapshot. Same
    best-effort contract as :func:`_copy_memory`.

    Idempotent per destination: snapshot ids are deterministic, so re-taking
    one must not leave the night duplicated in its replay.
    """
    exec_ = getattr(store, "_exec", None)
    if not callable(exec_):
        return
    try:
        cols = [str(r[0]) for r in (exec_(f"DESC TABLE {table}") or [])]
        if not cols:
            return
        exec_(f"DELETE FROM {table} WHERE session_id = '{dst}'")
        projected = ", ".join(
            f"'{dst}'" if c.upper() == "SESSION_ID" else c for c in cols
        )
        clause = f" AND ({where})" if where else ""
        exec_(
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"SELECT {projected} FROM {table} "
            f"WHERE session_id = '{src}'{clause}"
        )
    except Exception:
        pass


def _copy_history(store: Any, src: str, dst: str, day: int) -> None:
    """Carry the per-hour replay and the event log across.

    Frames are restricted to the night the copy is about to reason over. They
    are the heaviest rows in the schema — three grid snapshots each, ~150KB a
    row, ~22 rows a night — and a season's worth would be copied again on
    every replay clone for frames no prompt ever reads. ``build`` only ever
    asks for ``day_ended``, so that is what travels. The log is text and
    copies whole.

    v1.42 — the SQL path below is a no-op on a store with no ``_exec``,
    which is every file store. Nothing said so, and the failure was
    invisible in exactly the wrong way: the snapshot came out complete
    in every respect a person would check, and only the replay was
    missing. An agent planning on one was told "YOU ORDERED: (no orders
    on record)" and "EXECUTION LOG (unavailable)" about a night it had
    demonstrably played, and read that as having done nothing. So a file
    store now copies through the ordinary store API instead of silently
    copying nothing.
    """
    if callable(getattr(store, "_exec", None)):
        _copy_child_rows(
            store, "SOC_REPLAY_FRAME", src, dst, where=f"day = {int(day) - 1}",
        )
        _copy_child_rows(store, "SOC_GAME_LOG", src, dst)
        return

    night = int(day) - 1
    if night < 1:
        return  # day 1 has no last night, and that is not a gap.
    try:
        frames = list(store.list_replay_frames(src, night, night) or [])
    except Exception:
        return
    if not frames:
        return
    try:
        # Idempotent per (session, day) in the file store, so re-taking a
        # deterministic snapshot cannot leave the night duplicated.
        store.append_replay_frames(dst, night, frames)
    except Exception:
        pass


def take(
    store: Any,
    session_id: str,
    day: int,
    seat: str,
    *,
    prefix: str = "SNAP",
) -> Optional[str]:
    """Freeze ``session_id`` as it stands right now. Returns the snapshot id."""
    row = store.load_session(session_id)
    if not row:
        return None
    snap_id = snapshot_name(session_id, day, seat, prefix)
    snap = _decoded(row)
    snap["session_id"] = snap_id
    snap["season_name"] = f"{prefix}:{row.get('season_name') or ''}:d{int(day)}:{seat}"
    store.save_session(snap)
    _copy_memory(store, session_id, snap_id)
    _copy_history(store, session_id, snap_id, int(day))
    return snap_id


def clone_for_run(store: Any, snapshot_id: str, *, run_id: Optional[str] = None) -> str:
    """Copy a snapshot into a fresh, disposable session and return its id."""
    row = store.load_session(snapshot_id)
    if not row:
        raise KeyError(f"snapshot not found: {snapshot_id}")
    rid = run_id or uuid.uuid4().hex
    clone = _decoded(row)
    clone["session_id"] = rid
    clone["season_name"] = f"REPLAY:{row.get('season_name') or ''}"
    store.save_session(clone)
    _copy_memory(store, snapshot_id, rid)
    blob = clone.get("json_state")
    day = int((blob or {}).get("day") or 0) if isinstance(blob, Mapping) else 0
    _copy_history(store, snapshot_id, rid, day)
    return rid


def maybe_take(store: Any, session_id: str, day: int, seat: str, phase: str) -> None:
    """Snapshot hook for live seasons — a no-op unless the env var is set.

    Wired into ``run_agent_turn`` so the capture happens at the moment the
    board is handed to the agent, which is the state a replay must reproduce.
    """
    prefix = os.environ.get(_SNAPSHOT_ENV)
    if not prefix or str(phase or "") != "planning":
        return
    try:
        take(store, session_id, day, seat, prefix=prefix)
    except Exception:
        pass
