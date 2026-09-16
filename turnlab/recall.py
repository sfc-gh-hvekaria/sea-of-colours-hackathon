"""Give a frozen turn the memory the night actually had.

A board is a position, and an agent planning it should know what it
knew. Two things stopped that being true, both of them the same shape.

V12 keeps its STRATEGY JOURNAL — the INTENT it set each night and the
REFLECTION it wrote the morning after — in a process-global dict keyed
``session::player::day``, with a Snowflake table behind it for when the
process restarts. A lab board is a fresh clone with an id that has never
existed, so the dict misses, the hydrate finds nothing under the new id,
and ``render_journal`` prints "no prior nights — this is your first
turn". On night six. The agent then plans as though it had never played,
which is not the night you froze.

So the lab hydrates it here: read the *source* season's rows, write them
under the *clone's* id, and let the harness find its own memory exactly
where it always looks. Three properties make that safe, and all three
are load-bearing:

* **Keyed by the clone, never the source.** Writing under the source id
  would land in the memory of anyone playing that season live in this
  process. The clone id is unique per turn, so it cannot collide.
* **Read-only against the season.** ``save_entry`` mirrors to Snowflake
  only when the store it is handed has a Snowpark session behind it. The
  lab hands it the lab's own file store, so the mirror stays dormant and
  a season can never be edited by being studied.
* **Cut at the frozen day.** Only nights *before* the board are seeded.
  Day six must not be able to read day six's own reflection, which was
  written the morning after and would be tomorrow's newspaper.

Nothing in a harness changes, and nothing in a fork needs to. The
modules already read this dict first and only fall back to Snowflake
when it is empty, so a seeded entry is simply found. That matters more
than it sounds: ``_v7/memory.py`` is copied wholesale into every fork,
so a fix written into V12's copy would reach V12 and no one else, and
the divergence view would be comparing two different states of
knowledge while claiming to compare two agents.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Optional

_HARNESSES = "sea_of_colours.orchestrator_2.harnesses"

#: Journal rows are filed one per night under ``arena:day<N>``.
_JOURNAL_PREFIX = "arena:day"

#: ``(source session, seat) -> entries``. A season that has been played
#: is finished, so its journal cannot change; caching it means a board
#: opened five times costs one Snowflake round trip, not five.
_CACHE: dict[tuple[str, str], list[dict]] = {}

#: Stores to look for a board's source season in, in order. A board only
#: carries the first eight characters of the season it came from, so the
#: id has to be widened against a real store — and which store that is
#: depends on where the season was played.
_SOURCES = ("seasons", "snowflake")


class Recalled:
    """What a hydration managed to recover, and what it could not.

    Returned rather than logged because the alternative is the failure
    that cost an afternoon last week: an agent that quietly falls back
    looks exactly like an agent that had nothing to say. A caller that
    can see ``entries == 0`` and a reason can show that, instead of
    presenting an amnesiac turn as a considered one.
    """

    __slots__ = ("entries", "source", "seat", "days", "note", "error")

    def __init__(
        self,
        *,
        entries: int = 0,
        source: str = "",
        seat: str = "",
        days: Optional[list[int]] = None,
        note: str = "",
        error: str = "",
    ) -> None:
        self.entries = entries
        self.source = source
        self.seat = seat
        self.days = days or []
        # Why there is nothing, when there is nothing. "No journal" and
        # "the journal could not be read" look identical in a count, and
        # only one of them is a bug.
        self.note = note
        self.error = error

    def as_dict(self) -> dict:
        return {
            "entries": self.entries,
            "source": self.source,
            "seat": self.seat,
            "days": list(self.days),
            "note": self.note,
            "error": self.error,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        if self.error:
            return f"<Recalled seat={self.seat} FAILED: {self.error}>"
        return f"<Recalled seat={self.seat} nights={self.days}>"


# ── resolving an agent to the memory module it will actually read ──────
def harness_package(agent: str) -> str:
    """The harness package ``agent`` routes to, or ``""`` if it has none.

    A fork is a directory under ``harnesses/`` and its label is that
    directory's name, so the label *is* the lookup — but only after the
    manifest has had its say, because a manifest is free to name itself
    something other than its folder. Heuristic agents legitimately
    return ``""``: they have no journal to seed and never did.
    """
    label = str(agent or "").strip().lower()
    if not label:
        return ""

    try:
        from sea_of_colours.orchestrator_2 import agent_manifest

        for man in agent_manifest.discover() or ():
            if str(getattr(man, "label", "")).lower() == label:
                return f"{_HARNESSES}.{man.directory.name}"
    except Exception:
        pass

    # No manifest said so; fall back to the label as a package name,
    # which is what the shipped agents use.
    import importlib.util

    try:
        if importlib.util.find_spec(f"{_HARNESSES}.{label}") is not None:
            return f"{_HARNESSES}.{label}"
    except Exception:
        pass
    return ""


def fork_module(agent: str, dotted: str):
    """The very module object this agent's harness will use.

    Resolved through the fork's own package rather than V12's. A fork is
    a wholesale copy — its ``_v7/memory.py`` and ``_v7/opponent_weapons``
    are separate modules with their own module-level dicts, and its
    harness imports them from its own package. Seeding V12's copy for a
    fork's turn succeeds, changes nothing, and reports success.
    """
    pkg = harness_package(agent)
    if not pkg:
        return None
    try:
        return __import__(f"{pkg}.{dotted}", fromlist=["_"])
    except Exception:
        return None


def memory_module(agent: str):
    """The journal store this agent's harness will read from."""
    return fork_module(agent, "_v7.memory")


# ── finding the season a board was cut from ───────────────────────────
def _candidate_stores() -> list[tuple[str, Any]]:
    """Stores that might hold the season a board came from.

    Tolerant by design: a machine with no Snowflake credentials should
    still be able to open a board grabbed from ``seasons/``, and one
    with no ``seasons/`` directory should still reach Snowflake.
    """
    spec = os.environ.get("SOC_LAB_SOURCE", "").strip()
    names = (spec,) if spec else _SOURCES

    out: list[tuple[str, Any]] = []
    if not spec:
        # v1.42 — the lab's own store, first. A *minted* season is played
        # into turnlab/data and lives nowhere else, so leaving this out
        # meant a minted board could never find the season it came from.
        # Today that costs nothing because minting is heuristic-only and
        # a heuristic keeps no journal, but the moment someone mints with
        # an LLM in a seat the memories would be silently unreachable.
        try:
            from . import store as lab_store

            out.append(("turnlab", lab_store.store()))
        except Exception:
            pass

    for name in names:
        try:
            if name in ("snowflake", "sf"):
                from sea_of_colours.snowpark import backend

                out.append((name, backend.get_store_for("snowflake")))
            else:
                from sea_of_colours.snowpark.file_store import FileSocStore

                if os.path.isdir(name):
                    out.append((name, FileSocStore(name)))
        except Exception:
            continue
    return out


def _widen(prefix: str, store: Any) -> str:
    """Expand a board's eight-character source stub to a full id.

    Ambiguity is not-found rather than a guess: two seasons sharing a
    stub is rare, and seeding a turn with the wrong season's memories
    would be far worse than seeding it with none.
    """
    if not prefix:
        return ""
    try:
        rows = store.list_sessions() or []
    except Exception:
        return ""
    hits = {
        str(r.get("session_id"))
        for r in rows
        if str(r.get("session_id", "")).startswith(prefix)
        and not str(r.get("session_id", "")).startswith("LAB")
    }
    return hits.pop() if len(hits) == 1 else ""


def _agrees(board_id: str, candidate: str, store: Any) -> bool:
    """Does ``candidate`` actually look like the season this board is of?

    v1.42 — eight hex characters is a narrow thing to identify a season
    by, and the lab was treating any session that started with them as
    the source. It found one: a stray empty session in ``seasons/``
    whose id happened to begin ``dd868733`` was being reported as the
    origin of the ``dd868733`` boards, which were minted somewhere else
    entirely. It cost nothing that time because the impostor had no
    journal rows to hand over. The next collision would quietly seed a
    frozen turn with a different game's memories, and an agent reasoning
    from another season's INTENT is a failure nobody would think to look
    for. So the stub has to be corroborated: same season name, or same
    seed. Neither on file means we cannot tell, and cannot tell is
    treated as no.
    """
    def facts(sid: str, st: Any) -> tuple[Any, Any]:
        try:
            blob = (st.load_session(sid) or {}).get("json_state")
        except Exception:
            return None, None
        while isinstance(blob, (str, bytes)):
            try:
                blob = json.loads(blob)
            except Exception:
                return None, None
        blob = blob or {}
        return blob.get("season_name"), blob.get("seed")

    from . import store as lab_store

    want_name, want_seed = facts(board_id, lab_store.store())
    got_name, got_seed = facts(candidate, store)

    # Seed first: it is the strongest thing both ends agree on, and it
    # survives the renaming below.
    if want_seed is not None and got_seed is not None:
        return want_seed == got_seed

    # Then the name, but leniently. A re-walked board stamps its own
    # title on the copy — "Vetus_Lantern · day 6" for a season simply
    # called "Vetus_Lantern" — so an exact match rejects every grabbed
    # board, which is the half of the library that actually has
    # memories to recover.
    if want_name and got_name:
        a, b = str(want_name), str(got_name)
        a, b = a.split(" · ")[0].strip(), b.split(" · ")[0].strip()
        return a == b
    return False


def _board_agents(board_id: str) -> dict:
    """``{seat: agent}`` for the cast a board was frozen with.

    Read off the board's own state rather than inferred. Empty when the
    board cannot be read — callers treat that as "no opinion" and carry
    on looking.
    """
    try:
        from . import store as lab_store

        blob = (lab_store.store().load_session(board_id) or {}).get("json_state")
    except Exception:
        return {}
    while isinstance(blob, (str, bytes)):
        try:
            blob = json.loads(blob)
        except Exception:
            return {}
    agents = (blob or {}).get("agents") or {}
    return {str(k): str(v or "") for k, v in agents.items()} if agents else {}


def seats_of(board_id: str) -> list[str]:
    """Every seat on a board, in seat order."""
    return sorted(_board_agents(board_id))


def _played_by(board_id: str, seat: str) -> str:
    """Which agent held ``seat`` in the season this board was cut from."""
    return _board_agents(board_id).get(str(seat), "")


def sources_of(board_id: str) -> list[tuple[str, Any]]:
    """Every ``(full session id, store)`` that could be this board's season.

    Usually one. Two when a season was played locally and also exists in
    an account — which is not hypothetical: a season played on the file
    backend keeps its journal in a process-global dict and writes no
    ``SOC_AGENT_MEMORY`` rows at all, so the copy in ``seasons/``
    corroborates the board perfectly and has no memory to give. Stopping
    at the first match therefore finds the season and loses the journal.

    Callers that want a single answer still take the first; the freezer
    tries them all and keeps whichever actually held the memory.
    """
    parts = str(board_id or "").split("_")
    stub = parts[1] if len(parts) >= 4 else ""
    if not stub:
        return []
    out: list[tuple[str, Any]] = []
    for _name, store in _candidate_stores():
        full = _widen(stub, store)
        if full and _agrees(board_id, full, store):
            out.append((full, store))
    return out


def source_of(board_id: str) -> tuple[str, Any]:
    """``(full session id, store)`` for the season behind a board."""
    found = sources_of(board_id)
    return found[0] if found else ("", None)


# ── the journal that travels with the board ───────────────────────────
#: Frozen beside a board by ``python -m turnlab.freeze``.
JOURNAL_FILE = "journal.json"

#: Parsed ``journal.json`` per board id. ``None`` is a cached miss — a
#: board with no frozen journal is the common case for the minted half
#: of the library and should not cost a stat per invoke.
_FROZEN: dict[str, Optional[dict]] = {}


def frozen_journal(board_id: str) -> Optional[dict]:
    """The journal shipped inside ``board_id``, or ``None`` if it has none.

    v1.43 — boards travel in the repo; their seasons do not. ``seasons/``
    is ignored, the minted sources are ignored, and a grabbed board's
    season lives in whichever Snowflake account played it. So every
    lookup through :func:`source_of` fails on a fresh clone, and the two
    boards where memory is the entire exercise — a night-six race and a
    night-four recovery, both played by V12 — planned blind for everyone
    except the machine that made them.

    Freezing the journal into the board fixes that, and is also the more
    correct design for a *frozen* turn. Reading a live season means the
    turn's state of knowledge depends on which account you have; reading
    a file that shipped with the board means everyone plans the same
    night. That matters directly for the divergence view, which compares
    a fork against a baseline recorded here — against a journal an
    attendee could not otherwise see.

    So this is consulted first and the season is the fallback, not the
    other way around. Refresh it with ``python -m turnlab.freeze``.
    """
    key = str(board_id or "")
    if key in _FROZEN:
        return _FROZEN[key]

    from . import store as lab_store

    out: Optional[dict] = None
    try:
        blob = json.loads(
            (lab_store.DATA_DIR / key / JOURNAL_FILE).read_text(encoding="utf-8")
        )
        # A file whose shape we do not recognise is treated as absent
        # rather than as an empty journal: "no memory" is a claim, and
        # a truncated write should not get to make it.
        if isinstance(blob, dict) and isinstance(blob.get("seats"), dict):
            out = blob
    except (OSError, json.JSONDecodeError, TypeError):
        out = None
    _FROZEN[key] = out
    return out


# ── reading the journal ───────────────────────────────────────────────
def read_journal(source_id: str, seat: str, store: Any) -> list[dict]:
    """Every journal entry the season recorded for ``seat``.

    Read straight out of ``SOC_AGENT_MEMORY`` rather than through the
    harness, because the harness would file them under the season's id —
    and it is the clone's id they have to end up under.

    No ``LIKE`` on ``kind``, for the reason spelled out in the harness's
    ``_hydrate_from_snowflake``: the table is hybrid and keyed
    ``(session_id, player, kind)``, and a range predicate on that last
    column brings back the next seat's rows alongside your own. The seat
    is re-checked on the way through for the same reason.
    """
    key = (str(source_id), str(seat))
    hit = _CACHE.get(key)
    if hit is not None:
        return [dict(e) for e in hit]

    entries: list[dict] = []
    try:
        from sea_of_colours.snowpark.backend import snowpark_session_for

        session = snowpark_session_for(store)
        if session is None:
            _CACHE[key] = []
            return []
        rows = session.sql(
            """
            SELECT PLAYER, KIND, PAYLOAD FROM SOC_AGENT_MEMORY
            WHERE session_id = ? AND player = ?
            """,
            params=[str(source_id), str(seat)],
        ).collect()
        for row in rows:
            if str(row["PLAYER"]) != str(seat):
                continue
            kind = str(row["KIND"])
            if not kind.startswith(_JOURNAL_PREFIX):
                continue
            payload = row["PAYLOAD"]
            if isinstance(payload, (str, bytes)):
                try:
                    payload = json.loads(payload)
                except Exception:
                    continue
            if not isinstance(payload, dict):
                continue
            try:
                day = int(kind.split(_JOURNAL_PREFIX, 1)[-1])
            except ValueError:
                continue
            payload.setdefault("day", day)
            entries.append(dict(payload))
    except Exception:
        return []

    entries.sort(key=lambda e: int(e.get("day") or 0))
    _CACHE[key] = [dict(e) for e in entries]
    return entries


# ── the one call the lab makes ────────────────────────────────────────
def hydrate(
    run_id: str,
    seat: str,
    agent: str,
    *,
    board_id: str,
    day: int,
    store: Any,
) -> Recalled:
    """Seed ``run_id``'s journal from the season ``board_id`` came from.

    ``day`` is the night being planned, and only nights strictly before
    it are seeded — the entry for ``day`` itself carries a reflection
    written the morning after, which the agent cannot have read yet.

    ``store`` is the lab's own store, handed to ``save_entry`` so its
    Snowflake mirror stays dormant. Passing a live store here would
    write a throwaway clone's memory into a real account.
    """
    mem = memory_module(agent)
    if mem is None:
        # Heuristic agents have no journal. Not a failure — saying so
        # beats reporting zero entries as though something went wrong.
        return Recalled(seat=seat, note="this agent keeps no journal")

    if not board_id:
        return Recalled(
            seat=seat,
            error=(
                "this run has no board recorded against it, so there is no "
                "season to recover memory from. Re-open the board to fix it"
            ),
        )

    # v1.42 — ask the board who actually played this seat before going
    # looking for a journal it may never have had.
    #
    # A minted board's season was played by heuristics, which write no
    # memory, and the season itself does not ship: it is 24MB of frames
    # that exist only so the boards could be cut from it. Without this
    # the missing season read as a fault — a red NO MEMORY banner on
    # every seed-50 turn, sending people to debug storage when the true
    # answer is that RED_HARVEST keeps no diary. The board records its
    # own cast, so it can say so without the season being present.
    played_by = _played_by(board_id, seat)
    if played_by and memory_module(played_by) is None:
        return Recalled(
            seat=seat,
            note=(
                f"nothing to recall — {seat} was played by "
                f"{played_by.upper()}, which keeps no journal"
            ),
        )

    # v1.43 — the journal the board shipped with, before going looking
    # for a season that is probably not on this machine. See
    # ``frozen_journal`` for why the file wins over the live season.
    frozen = frozen_journal(board_id)
    if frozen is not None:
        entries = (frozen.get("seats") or {}).get(str(seat)) or []
        return _seed(
            mem, run_id, seat, [dict(e) for e in entries],
            day=day, store=store,
            source=str(frozen.get("source") or board_id),
        )

    source_id, source_store = source_of(board_id)
    if not source_id:
        return Recalled(
            seat=seat,
            error=(
                f"could not find the season behind {board_id} — its memory "
                f"cannot be recovered, so this turn plans blind. If this "
                f"board should carry one, run python -m turnlab.freeze"
            ),
        )

    try:
        entries = read_journal(source_id, seat, source_store)
    except Exception as exc:  # pragma: no cover - defensive
        return Recalled(seat=seat, source=source_id, error=str(exc))

    return _seed(mem, run_id, seat, entries, day=day, store=store,
                 source=source_id)


def _seed(
    mem: Any,
    run_id: str,
    seat: str,
    entries: list[dict],
    *,
    day: int,
    store: Any,
    source: str,
) -> Recalled:
    """Write ``entries`` into the clone's journal, cut at ``day``.

    The cut is repeated here even though :mod:`turnlab.freeze` already
    applies it, because the two callers arrive by different routes and
    only one of them has been through the freezer. Day N's own entry
    carries the reflection written the morning after — tomorrow's
    newspaper, and the one thing this turn must not be able to read.
    """
    kept = [e for e in entries if 0 < int(e.get("day") or 0) < int(day)]
    for entry in kept:
        try:
            mem.save_entry(run_id, seat, entry, store=store)
        except Exception as exc:  # pragma: no cover - defensive
            return Recalled(seat=seat, source=source, error=str(exc))

    note = ""
    if not kept:
        note = (
            f"no journal was recorded for {seat} before day {day} — "
            f"that seat was played by a human, or by an agent that keeps none"
        )

    return Recalled(
        entries=len(kept),
        source=source,
        seat=seat,
        days=[int(e.get("day") or 0) for e in kept],
        note=note,
    )


def forget() -> None:
    """Drop the cached journals. For tests, and for a long-lived server
    that has had a board re-grabbed or re-frozen underneath it."""
    _CACHE.clear()
    _FROZEN.clear()
