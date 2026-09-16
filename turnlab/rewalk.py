"""Pause any finished season at any day, from what the season already saved.

``boards.py`` used to say this was impossible, and that claim was wrong.
It confused two different questions. A replay *frame* is rendered paint
— ``bg``/``ch``/``fg``, no tile, no purity — so you genuinely cannot
reconstitute a board by reading one, and that part stands. But a season
does not have to be read back. It can be **re-walked**, and everything
needed to re-walk it is already on disk:

* ``seed``, ``width``, ``height`` on the session row — the map generator
  is deterministic in all three, so day 1 is exactly reproducible.
* ``SOC_POLICY_QUEUE`` — every seat's night orders, for every day, kept
  forever under ``(session_id, day, player)``.
* ``SOC_GAME_LOG`` — the ``[orbit] pN: built ...`` settlement lines,
  which are the only surviving record of what each seat bought.

The night simulator contains no randomness at all, and the one seeded
RNG in the engine is ``random.Random(self.seed)``. So replaying the
archived orders through the real engine does not *approximate* the
season, it reproduces it. Measured on ``CONTROL_s42_file`` (seed 42,
8 days): re-walking to each of days 3, 4, 6 and 7 reproduced the entity
roster, every entity position, both seats' credits and all 1120 cells of
the observer paint identically against that day's own saved opening
frame — and running to the end reproduced ``grid``, ``ledger``,
``entities``, ``credits``, ``weapon_stock``, ``harvest_log``,
``blue_bank`` and ``cumulative_shipped_score`` byte for byte.

Two caveats, both narrow:

* Orbit is recovered from log **prose**. If a settlement line is ever
  reworded, :data:`_BUY` stops matching and the re-walk diverges from
  the day the wording changed — loudly, because the buy simply does not
  happen. :func:`check` re-walks to the end and diffs, which is how you
  find that out before trusting a board.
* Ids that embed the session id (``asset_records[*].session_id``, a
  hoard parcel's ``square_id``) necessarily differ in the copy. Nothing
  reads them as game state.

Why this matters: it means the board library is not limited to seasons
someone remembered to snapshot. Every season ever played is a library
of turns, one per day, already sitting in the store.

Nothing here writes to the source. Reads come from one store, the
re-walk is built in another, and the two are never the same object.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from . import store as lab_store

#: A season that will not advance is a bug, not a long game.
MAX_STEPS = 400

#: The orbit settlement lines, e.g.
#: ``[orbit] p1: built 2 probe(s) (+2 stock → 2) for 500c``. Anchored at
#: the start so a line merely *quoting* a settlement cannot match.
_BUY = re.compile(
    r"^\[orbit\] (p\d+): built (?:(\d+) )?"
    r"(probe|harvester|EMP warhead|chaff flare)"
)

#: ``[orbit] p1: repaired harvester harvester_p1 for 500c``. Easy to
#: forget, because it buys nothing — but it spends credits and un-damages
#: a unit, so a re-walk that drops it drifts from the day it first
#: happened. It was in fact the whole of the first divergence this module
#: had: every season that repaired a harvester failed, every season that
#: never did was exact.
_REPAIR = re.compile(r"^\[orbit\] (p\d+): repaired (?:\w+ )?(\S+) for")

#: The caltrop mine was retired in v1.31 and there is no longer an action
#: that builds one. A season that bought one cannot be re-walked at all,
#: so say so rather than quietly replaying a different season.
_RETIRED = re.compile(r"^\[orbit\] p\d+: built .*\bmine\(s\)")

#: Log wording -> the wire action ``policy.parse_orbit_actions`` expects.
_WIRE = {
    "probe": "build_probe",
    "harvester": "build_harvester",
    "EMP warhead": "build_emp",
    "chaff flare": "build_chaff",
}


@dataclass(frozen=True)
class Source:
    """A season that can be grabbed from, and the days it can offer."""

    session_id: str
    name: str
    seed: int
    #: Highest day the season reached.
    last_day: int
    #: Days this season can be paused at.
    playable_days: tuple[int, ...] = ()
    seats: Mapping[str, str] = field(default_factory=dict)
    #: Non-empty when the season uses something the engine no longer has.
    blocked: str = ""

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "seed": self.seed,
            "last_day": self.last_day,
            "days": list(self.playable_days),
            "seats": dict(self.seats),
            "blocked": self.blocked,
        }


def _state(store: Any, session_id: str) -> dict:
    """The session's decoded ``json_state``, or ``{}``."""
    row = store.load_session(session_id) or {}
    blob = row.get("json_state")
    for _ in range(4):  # older rows are double-encoded
        if not isinstance(blob, str):
            break
        blob = json.loads(blob)
    return dict(blob) if isinstance(blob, Mapping) else {}


def orbit_baskets(store: Any, session_id: str) -> dict[int, dict[str, list]]:
    """What each seat bought, per day, recovered from the settlement log.

    The orbit phase is the one thing a season does not archive as a
    submission — ``submit_orbit_actions`` never calls ``upsert_policy``
    — so this reads the resolver's own account of what it granted.
    That is arguably the better record anyway: it is what the seat got,
    not what it asked for.
    """
    out: dict[int, dict[str, list]] = {}
    # One pass, keeping log order: the settlement order is the order the
    # seat's basket was resolved in, and a repair before a build is not
    # the same submission as a build before a repair.
    for row in store.list_log(session_id) or []:
        text = str(row.get("text") or "")
        buy, fix = _BUY.match(text), _REPAIR.match(text)
        if buy is not None:
            seat = buy.group(1)
            action: dict[str, Any] = {"a": _WIRE[buy.group(3)]}
            if int(buy.group(2) or 1) > 1:
                action["count"] = int(buy.group(2))
        elif fix is not None:
            seat, action = fix.group(1), {"a": "repair", "unit": fix.group(2)}
        else:
            continue
        out.setdefault(int(row.get("day") or 0), {}).setdefault(
            seat, []).append(action)
    return out


def unsupported(store: Any, session_id: str) -> str:
    """Why this season cannot be re-walked, or ``""`` if it can be."""
    for row in store.list_log(session_id) or []:
        if _RETIRED.match(str(row.get("text") or "")):
            return "bought a caltrop mine, retired in v1.31"
    return ""


def night_orders(store: Any, session_id: str, upto: int) -> dict[int, dict]:
    """Archived night orders for days ``1..upto``."""
    out: dict[int, dict] = {}
    for day in range(1, int(upto) + 1):
        got = store.list_policies(session_id, day) or {}
        if got:
            out[day] = {str(k): list(v or []) for k, v in got.items()}
    return out


def describe(store: Any, session_id: str) -> Optional[Source]:
    """What a season can offer, without re-walking it."""
    row = store.load_session(session_id)
    if not row:
        return None
    state = _state(store, session_id)
    last = int(row.get("day") or state.get("day") or 1)
    archived = set(night_orders(store, session_id, last))

    # A day is reachable only if every night before it can be replayed,
    # so the offer is the unbroken run starting at day 1 — plus the day
    # after the last archived night, which is reachable precisely because
    # nobody has played it yet. A session that begins mid-season (a lab
    # clone that leaked in here, say) has no day 1 and so offers nothing.
    reach: list[int] = []
    day = 1
    while day <= last:
        reach.append(day)
        if day not in archived:
            break
        day += 1

    return Source(
        session_id=session_id,
        name=str(row.get("season_name") or ""),
        seed=int(row.get("seed") or 0),
        last_day=last,
        playable_days=tuple(reach) if 1 in archived or last == 1 else (),
        seats=dict(state.get("agents") or {}),
        blocked=unsupported(store, session_id),
    )


def sources(
    store: Any, *, skip: Sequence[str] = (), include_blocked: bool = False
) -> list[Source]:
    """Every season in ``store`` worth grabbing a turn from.

    Ordered longest first, because a season that ran further has more
    turns in it and its later days are the interesting ones.
    """
    out: list[Source] = []
    for row in store.list_sessions() or []:
        sid = str(row.get("session_id") or "")
        if not sid or any(sid.startswith(p) for p in skip):
            continue
        got = describe(store, sid)
        if got is None or not got.playable_days:
            continue
        if got.blocked and not include_blocked:
            continue
        out.append(got)
    out.sort(key=lambda s: (-s.last_day, s.name))
    return out


def find(store: Any, name: str, *, day: int | None = None) -> Optional[Source]:
    """Look a season up by name — the thing a person actually remembers.

    Matches the season name exactly first, then case-insensitively, then
    on a unique prefix, and finally falls back to treating ``name`` as a
    session id. When ``day`` is given, only seasons that can actually
    reach it are considered, so naming a day that one season is too
    short for quietly picks the one that is long enough.
    """
    pool = [s for s in sources(store)
            if day is None or int(day) in s.playable_days
            or int(day) == s.last_day]
    want = str(name or "").strip()
    if not want:
        return None
    for pick in (lambda s: s.name == want,
                 lambda s: s.name.lower() == want.lower(),
                 lambda s: s.session_id == want,
                 lambda s: s.session_id.startswith(want)):
        hit = [s for s in pool if pick(s)]
        if len(hit) == 1:
            return hit[0]
        if hit:
            return hit[0]
    starts = [s for s in pool if s.name.lower().startswith(want.lower())]
    return starts[0] if len(starts) == 1 else None


@dataclass
class Rewalk:
    """The result of pausing a season."""

    session_id: str
    day: int
    phase: str
    source: str
    source_name: str
    seed: int
    #: Days actually walked through to get here.
    walked: int = 0
    #: Anything the engine refused on the way. Empty is the good case.
    complaints: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "day": self.day,
            "phase": self.phase,
            "source": self.source,
            "source_name": self.source_name,
            "seed": self.seed,
            "walked": self.walked,
            "complaints": list(self.complaints),
        }


def rewalk(
    session_id: str,
    day: int,
    *,
    src: Any,
    into: Any = None,
    new_id: str | None = None,
    season_name: str | None = None,
) -> Rewalk:
    """Replay ``session_id`` from day 1 and stop at ``day``'s planning phase.

    The result is an ordinary session in ``into`` — a real board the
    engine will happily play, sitting exactly where the seats sat when
    they planned that night, with no orders in yet.

    ``src`` and ``into`` must be different stores. The source is only
    ever read.
    """
    from sea_of_colours.game.session import SEASON_DAY_CAP
    from sea_of_colours.snowpark import engine as soc_engine

    into = into if into is not None else lab_store.store()
    if into is src:
        raise ValueError(
            "rewalk reads one store and writes another; "
            "pass a separate 'into'"
        )

    row = src.load_session(session_id)
    if not row:
        raise KeyError(f"no session {session_id!r} to re-walk")
    state = _state(src, session_id)
    target = int(day)
    reached = int(row.get("day") or state.get("day") or 1)
    if target < 1 or target > reached:
        raise ValueError(
            f"day {target} is outside {session_id!r}, which reached "
            f"day {reached}"
        )

    seats = list(state.get("players") or ["p1", "p2"])
    orders = night_orders(src, session_id, target)
    baskets = orbit_baskets(src, session_id)

    fresh = soc_engine.init_session(
        into,
        seed=int(row.get("seed") or state.get("seed") or 0),
        width=int(row.get("width") or state.get("width") or 40),
        height=int(row.get("height") or state.get("height") or 28),
        season_name=season_name or str(row.get("season_name") or ""),
        season_day_cap=int(state.get("season_day_cap") or SEASON_DAY_CAP),
        players=seats,
        agents=dict(state.get("agents") or {}),
        player_profiles=dict(state.get("player_profiles") or {}),
        weapons_enabled=bool(state.get("weapons_enabled", True)),
        signs_enabled=bool(state.get("signs_enabled", True)),
        visibility_mode=str(state.get("visibility_mode") or "hidden"),
    )
    rid = fresh["session_id"]
    if new_id:
        _rename(into, rid, new_id)
        rid = new_id

    out = Rewalk(
        session_id=rid,
        day=target,
        phase="planning",
        source=session_id,
        source_name=str(row.get("season_name") or ""),
        seed=int(row.get("seed") or 0),
    )

    walked = 0
    for _ in range(MAX_STEPS):
        status = soc_engine.get_session_status(into, rid)
        now, phase = int(status.get("day") or 1), str(status.get("phase") or "")
        if now == target and phase == "planning":
            break
        if phase == "season_complete":
            # Legitimate landing when the target *is* the end — a season
            # that ran its cap has no planning phase on its final day.
            if now != target:
                out.complaints.append(
                    f"season ended on day {now}, before day {target}"
                )
            break
        if phase == "orbit":
            for seat in seats:
                soc_engine.submit_orbit_actions(
                    into, rid, seat, baskets.get(now, {}).get(seat, [])
                )
        elif phase == "planning":
            queues = orders.get(now)
            if not queues:
                out.complaints.append(f"day {now}: no archived orders")
                break
            for seat in seats:
                got = soc_engine.submit_policy(
                    into, rid, seat, queues.get(seat, [])
                )
                if not got.get("ok"):
                    out.complaints.append(
                        f"day {now} {seat}: {got.get('errors')}"
                    )
            walked += 1
        else:
            out.complaints.append(f"day {now}: stuck in phase {phase!r}")
            break
    else:
        out.complaints.append(f"gave up after {MAX_STEPS} steps")

    final = soc_engine.get_session_status(into, rid)
    out.day = int(final.get("day") or target)
    out.phase = str(final.get("phase") or "")
    out.walked = walked
    return out


def _rename(store: Any, old: str, new: str) -> None:
    """Re-key a just-built session so it carries a lab id.

    The id is baked into the state blob as well as the row, and
    hydration reads the blob, so both have to move or the session writes
    itself back under the old name.
    """
    row = dict(store.load_session(old) or {})
    blob = row.get("json_state")
    for _ in range(4):
        if not isinstance(blob, str):
            break
        blob = json.loads(blob)
    if isinstance(blob, Mapping):
        blob = dict(blob)
        blob["session_id"] = new
    row["session_id"] = new
    row["json_state"] = blob
    store.save_session(row)
    for day in range(1, int(row.get("day") or 1) + 1):
        for seat, queue in (store.list_policies(old, day) or {}).items():
            store.upsert_policy(new, day, seat, queue)


def board_id(session_id: str, day: int, seat: str = "p1") -> str:
    """The lab id a given (season, day) always grabs to.

    Derived rather than random, which is the whole point: ``turnlab/data``
    is disposable and git-ignored, so a board has to come back with the
    same id after it is deleted or the baselines recorded against it are
    orphaned. Same season, same day, same id, forever.

    Shaped to match ``mint``'s snapshot ids so :func:`boards._parse`
    reads both without knowing which kind it has.
    """
    stem = str(session_id or "")[:8] or "unknown"
    return f"{lab_store.BOARD_PREFIX}{stem}_d{int(day)}_{seat}"


def grab(
    name: str,
    day: int,
    *,
    src: Any,
    into: Any = None,
    seat: str = "p1",
    replace: bool = False,
) -> Rewalk:
    """Name a season and a day; get a board in the lab you can play.

    The everyday entry point. ``name`` is whatever a person remembers —
    the season name, a unique prefix of it, or the session id.
    """
    into = into if into is not None else lab_store.store()
    found = find(src, name, day=day)
    if found is None:
        raise KeyError(f"no season matching {name!r} that reaches day {day}")
    if found.blocked:
        raise ValueError(f"{found.name!r} cannot be re-walked: {found.blocked}")

    bid = board_id(found.session_id, day, seat)
    if into.load_session(bid) and not replace:
        row = into.load_session(bid)
        return Rewalk(
            session_id=bid, day=int(day), phase=str(row.get("phase") or ""),
            source=found.session_id, source_name=found.name, seed=found.seed,
        )

    return rewalk(
        found.session_id, day, src=src, into=into, new_id=bid,
        # Carry the human name onto the board so the launcher can list
        # "CONTROL_s42_file, day 5" rather than eight bytes of hex.
        season_name=f"{found.name} · day {day}",
    )


def check(session_id: str, *, src: Any, into: Any = None) -> dict:
    """Re-walk a season to its end and diff against what it really was.

    The honest self-test for this module: if the orbit log wording ever
    drifts, or the engine picks up a source of nondeterminism, the
    fields below stop matching and every board grabbed from this season
    is suspect.
    """
    row = src.load_session(session_id) or {}
    truth = _state(src, session_id)
    end = int(row.get("day") or truth.get("day") or 1)
    got = rewalk(session_id, end, src=src, into=into,
                 new_id=None, season_name="REWALK_CHECK")
    from sea_of_colours.snowpark import engine as soc_engine

    into = into if into is not None else lab_store.store()
    mine = soc_engine._hydrate_session(into, got.session_id).to_dict()

    # Fields that are the game. Deliberately excludes asset_records and
    # hoard parcel ids, which embed the session id and so can never match.
    fields = (
        "grid", "ledger", "entities", "credits", "weapon_stock",
        "harvest_log", "blue_bank", "cumulative_shipped_score",
        "probe_stock", "redsign",
    )
    diffs = {
        f: (json.dumps(mine.get(f), sort_keys=True)
            == json.dumps(truth.get(f), sort_keys=True))
        for f in fields
    }
    return {
        "session_id": session_id,
        "name": str(row.get("season_name") or ""),
        "day": end,
        "ok": all(diffs.values()) and not got.complaints,
        "fields": diffs,
        "complaints": got.complaints,
    }
