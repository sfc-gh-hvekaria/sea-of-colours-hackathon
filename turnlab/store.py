"""The lab's own store — local files, under this folder, always.

The lab does not use ``SOC_BACKEND``, and that is the point rather than
an oversight. Every other part of this repo resolves its store from the
environment or from the per-game registry, which means the same code
reads a memory store on one machine and somebody's live Snowflake
account on another. For a thing whose whole job is to clone saved games
and let agents scribble on the copies, that is the wrong default: the
one bug this package must never have is writing into a real season, and
"it depends what the environment says" is not a defence against it.

So the boards and every throwaway clone live in :data:`DATA_DIR`, a
plain directory of JSON next to this file. It works offline, it needs no
credentials, it can be deleted wholesale without consequence, and it is
the same on every machine.

Routing: a lab session is recognised by its id (``LAB_`` for a frozen
board, ``LABRUN_`` for a clone), so the server can send exactly those to
this store and everything else to the ordinary one. That is what lets a
lab clone open in the normal game UI while still living here.
"""

from __future__ import annotations

import gzip
import json
import pathlib
from typing import Any, Optional

#: Where the lab keeps everything. Sibling of this file, so moving the
#: folder moves the data with it.
DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"

#: A frozen board, written by ``mint``.
BOARD_PREFIX = "LAB_"
#: A throwaway clone of a board, made to be played and discarded.
RUN_PREFIX = "LABRUN_"

_store: Optional[Any] = None


def owns(session_id: str) -> bool:
    """Does the lab own ``session_id``?

    Deliberately a name test rather than a lookup. It has to answer
    before the store is opened (the server routes on it), and it has to
    give the same answer for a session that does not exist yet.
    """
    sid = str(session_id or "")
    return sid.startswith(BOARD_PREFIX) or sid.startswith(RUN_PREFIX)


def is_run(session_id: str) -> bool:
    """Is this a throwaway clone — the only thing the lab may play in?

    Boards are excluded on purpose. A board is read, cloned and never
    written to; if it were playable it would stop being a fixed point
    and the second agent to see it would get a different night.
    """
    return str(session_id or "").startswith(RUN_PREFIX)


def _lab_store_class():
    """Build the lab's store class lazily, so importing this module does
    not drag in the engine's storage layer."""
    from sea_of_colours.snowpark.file_store import FileSocStore

    class LabStore(FileSocStore):
        """A file store where a clone can see its board's history.

        ``clone_for_run`` copies the session row and the state blob, and
        nothing else — so a clone of a day-6 board has no replay frames
        for day 5. That is what left the agent reading "YOU ORDERED: (no
        orders on record)" and "EXECUTION LOG (unavailable)" on a night
        it had demonstrably played: the harness rebuilds both by reading
        the *previous* day's frames out of the store, and the clone's
        were empty.

        Copying them across was the obvious fix and the wrong one. The
        frames carry a dense per-seat percept for every cell, so one
        board's sidecar is tens of megabytes; a clone per open and
        another per invoke would spend most of a turn moving bytes that
        never change.

        So a run borrows instead. A read for a day the clone has nothing
        for falls through to the board it was cut from, which is
        read-only, immutable, and by definition has exactly the history
        the clone is supposed to have inherited.

        The fallback is deliberately keyed on *emptiness* rather than on
        the day. Once a lab night resolves, the clone owns real frames
        for that day and answers with them; earlier days keep coming
        from the board, which is the true record of them.
        """

        def list_replay_frames(self, session_id, day_from=None, day_to=None):
            own = super().list_replay_frames(session_id, day_from, day_to)
            if own or not is_run(session_id):
                return own
            board = board_of(session_id)
            if not board or board == session_id:
                return own
            return super().list_replay_frames(board, day_from, day_to)

        def _read_frames(self, sid):
            """Read ``frames.jsonl``, or ``frames.jsonl.gz`` if that is
            what shipped.

            v1.42 — the boards travel in the repo, and raw frames do not
            fit: a night is one dense per-cell percept per hour per seat,
            so the ten-turn library is 107MB uncompressed and 1.6MB
            gzipped. That ratio is not a lucky result, it is what the
            data is — the same board, restated hour after hour, with a
            few cells different each time.

            Read-only, and only as a fallback. A run that plays a night
            writes ordinary uncompressed frames next to its own state;
            compression is a property of a *shipped board*, not of the
            store, and nothing here ever writes a ``.gz``.
            """
            own = super()._read_frames(sid)
            if own:
                return own
            path = self._part(sid, "frames.jsonl.gz")
            if not path.exists():
                return own
            frames = []
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            frames.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                return own
            return frames

    return LabStore


def store() -> Any:
    """The lab's store, opened once per process."""
    global _store
    if _store is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _store = _lab_store_class()(str(DATA_DIR))
    return _store


#: How many opened runs to keep, and how long one is assumed to be in
#: use. Generous on both counts: the cost of keeping a dead run is disk,
#: and the cost of reaping a live one is somebody's open tab going blank
#: mid-turn.
KEEP_RUNS = 24
KEEP_HOURS = 12


def sweep_runs(keep: int = KEEP_RUNS, hours: float = KEEP_HOURS) -> list[str]:
    """Reclaim old throwaway clones. Returns what was removed.

    Scratch clones are discarded the moment the agent stops thinking,
    but an *opened* run cannot be: the page is still holding it. So
    nothing was ever reclaiming them, and one directory per "open this
    turn" accumulated indefinitely — 109 sessions and 367MB by the time
    anyone looked, most of it replay frames belonging to turns nobody
    had thought about in weeks. Opening a turn in a new tab and going
    straight back for another one, which is now the intended way to use
    the launcher, makes them pile up faster.

    Two guards rather than one, because the failure modes are not
    symmetric. Age alone would reap a run somebody left open over lunch;
    count alone would reap the third turn of an afternoon spent
    comparing four. A run survives if it is recent OR if it is among the
    most recent handful, and only the intersection is dropped.

    Boards are never touched. They are the fixed points the whole
    package is built on, and they are checked in.
    """
    import time

    st = store()
    try:
        rows = st.list_sessions() or []
    except Exception:
        return []

    runs = []
    for row in rows:
        sid = str(row.get("session_id") or "")
        if not is_run(sid):
            continue
        path = DATA_DIR / sid
        try:
            runs.append((path.stat().st_mtime, sid))
        except OSError:
            continue

    runs.sort(reverse=True)
    cutoff = time.time() - (float(hours) * 3600.0)
    dropped: list[str] = []
    for i, (mtime, sid) in enumerate(runs):
        if i < int(keep) or mtime >= cutoff:
            continue
        try:
            # Takes the provenance file with it: that is filed inside the
            # session's own directory precisely so a discard is complete.
            st.delete_session(sid)
            dropped.append(sid)
        except Exception:
            continue
    return dropped


#: Filed beside the clone's own state, so discarding the run discards it.
_PROVENANCE = "lab_board.json"


def remember_board(run_id: str, board_id: str) -> None:
    """Record which board a clone was cut from.

    Kept in a file of the lab's own rather than on the session row,
    because the engine rebuilds that row from the state blob on every
    save and any field it does not know about is dropped. The same trap
    already cost the ``REPLAY:`` season-name marker — see ``open_board``.

    It matters because provenance is what lets a clone recover the
    season's memory. Deriving it from the client's URL instead would
    mean a stale link produced a turn planned from nothing, which looks
    identical to an agent that simply had nothing to remember.
    """
    if not (run_id and board_id):
        return
    path = DATA_DIR / str(run_id)
    try:
        path.mkdir(parents=True, exist_ok=True)
        (path / _PROVENANCE).write_text(
            '{"board": "%s"}\n' % str(board_id), encoding="utf-8"
        )
    except OSError:
        # Provenance is a nicety; failing the open over it would be worse.
        pass


def board_of(run_id: str) -> str:
    """The board a clone came from, or ``""`` if it was not recorded."""
    import json

    try:
        raw = (DATA_DIR / str(run_id) / _PROVENANCE).read_text(encoding="utf-8")
        return str(json.loads(raw).get("board") or "")
    except Exception:
        return ""
