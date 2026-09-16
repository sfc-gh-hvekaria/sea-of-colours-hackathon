"""Mint lab boards by playing a season and freezing every decision.

This is the lab's own copy of the season loop, and the duplication is
deliberate. The obvious implementation was a two-line hook inside
``agent/runtime.py`` that snapshots whenever an env var is set — and
that is in fact how ``orchestrator_2`` already does it. But the lab is
a side viewer, and a side viewer does not get to reach into the engine
and change what happens on a real turn. A copy that calls only public
functions can never make a live season behave differently, however
badly it is written, and that is worth more here than saving forty
lines.

So the loop below is `evals/seasons.py`'s, with one thing added: before
a seat plans, :func:`snapshot.take` freezes the board. That is the only
moment the pre-decision state exists — the session blob is overwritten
as the night resolves — and it is exactly the state a new agent has to
be handed if "what would MY agent have done here" is to mean anything.

What comes out is one snapshot per (day, seat). Each is a complete,
inert session that can be cloned and played forever without touching
the season it came from, which is what lets the same night be run by
V12, by the heuristic, and by an attendee's fork, and the three
compared.

Nothing here writes to an existing season. Every write is either a new
session of its own making or a snapshot under a fresh id — the
headless-season exception, and the only one the lab has.
"""

from __future__ import annotations

import contextlib
import hashlib
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from . import store as lab_store

#: A season that will not advance is a bug, not a long game. Matches
#: the ceiling `evals/seasons.py` uses for the same reason.
MAX_TURNS = 400

#: Global uuid patching is process-wide, so two mints at once would
#: interleave one seeded stream and both come out irreproducible —
#: quietly, which is the whole problem this is here to fix.
_MINT_LOCK = threading.Lock()


@contextlib.contextmanager
def _pinned_ids(*parts: Any):
    """Make ``uuid4`` a seeded stream for the duration of a mint.

    v1.42 — a board set is supposed to be a fact you can hand someone:
    same seed, same boards. It was not. ``seed`` pins the *map* and
    nothing else, because the heuristic seeds its own RNG off the
    session id (deliberately — it is what stops two seats opening
    identically) and the session id is a fresh ``uuid4`` every run. So
    minting seed 4242 twice produced two different seasons that both
    claimed to be seed 4242, and a board could never be regenerated
    once its season was gone. We learned that the expensive way, on a
    board set whose source season no longer exists.

    Pinning ids is sufficient — with this in place two mints of one
    seed are byte-identical, which ``test_minting_a_seed_twice_gives_
    the_same_boards`` holds us to. It is deliberately scoped to the
    mint and restored on the way out: patching ``uuid4`` process-wide
    is a thing you do to an offline authoring step, never to a server.
    """
    key = "|".join(str(p) for p in parts)
    rng = random.Random(hashlib.sha256(key.encode()).digest())
    with _MINT_LOCK:
        real = uuid.uuid4
        uuid.uuid4 = lambda: uuid.UUID(int=rng.getrandbits(128), version=4)
        try:
            yield
        finally:
            uuid.uuid4 = real


@dataclass
class Frozen:
    """One frozen decision: a seat, on a day, with a board to play."""

    snapshot_id: str
    day: int
    seat: str
    #: Who actually held the seat in the season this came from.
    played_by: str
    phase: str = "planning"

    def as_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "day": self.day,
            "seat": self.seat,
            "played_by": self.played_by,
            "phase": self.phase,
        }


@dataclass
class MintResult:
    session_id: str
    season_name: str
    seed: int
    seats: Mapping[str, str]
    frozen: list[Frozen] = field(default_factory=list)
    days_played: int = 0
    scores: Mapping[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    aborted: str = ""

    def for_day(self, day: int) -> list[Frozen]:
        return [f for f in self.frozen if f.day == day]


def mint(
    seats: Mapping[str, str],
    *,
    seed: int = 42,
    width: int = 40,
    height: int = 28,
    days: int | None = None,
    season_name: str | None = None,
    store: Any = None,
    prefix: str = "LAB",
    on_freeze: Callable[[Frozen], None] | None = None,
) -> MintResult:
    """Play a season, freezing the board before every planning turn.

    ``seats`` maps seat id to agent label, e.g.
    ``{"p1": "red_harvest", "p2": "tabula_v12"}``. Heuristic seats cost
    nothing and run offline, which is why a wholly offline board set is
    possible at all.
    """
    from sea_of_colours.evals import dispatch
    from sea_of_colours.game.session import SEASON_DAY_CAP
    from sea_of_colours.snowpark import engine as soc_engine
    from sea_of_colours.snowpark import snapshot as soc_snapshot

    if not seats:
        raise ValueError("a board set needs at least one seat")
    for agent in seats.values():
        dispatch.resolve(agent)  # fail on a typo before the long run

    store = store if store is not None else lab_store.store()
    order: list[str] = list(seats)
    name = season_name or f"{prefix}_{seed}"

    started = time.time()
    seen: set[str] = set()

    # Everything that decides how the season goes, and nothing that
    # doesn't: the same arguments must always mint the same boards.
    with _pinned_ids(seed, name, width, height, days, sorted(seats.items())):
        info = soc_engine.init_session(
            store,
            seed=seed,
            width=width,
            height=height,
            season_name=name,
            season_day_cap=days or SEASON_DAY_CAP,
            players=order,
            agents={s: dispatch.strategy_slug(a) for s, a in seats.items()},
            player_profiles=dispatch.seat_profiles(seats, seed=seed),
        )
        sid = info["session_id"]
        result = MintResult(
            session_id=sid,
            season_name=info.get("season_name") or name,
            seed=seed,
            seats=dict(seats),
        )

        for _ in range(MAX_TURNS):
            status = soc_engine.get_session_status(store, sid)
            if status.get("phase") == "season_complete":
                break

            pending = status.get("pending") or {}
            seat = next((s for s in order if not pending.get(s, False)), None)
            if seat is None:
                # Everyone submitted but nothing drives the clock here.
                soc_engine.run_night(store, sid)
                continue

            phase = str(status.get("phase") or "")
            day = int(status.get("day", 1))

            # The freeze. Only the planning phase is worth a board: an
            # orbit turn is a shopping decision against a board nobody
            # has moved on yet, and freezing both doubles the set for
            # nothing.
            if phase == "planning":
                key = f"{day}:{seat}"
                if key not in seen:
                    seen.add(key)
                    snap = soc_snapshot.take(
                        store, sid, day, seat, prefix=prefix)
                    if snap:
                        frozen = Frozen(
                            snapshot_id=snap, day=day, seat=seat,
                            played_by=seats[seat], phase=phase,
                        )
                        result.frozen.append(frozen)
                        if on_freeze is not None:
                            on_freeze(frozen)

            try:
                dispatch.play_turn(store, sid, seat, seats[seat])
            except Exception as exc:
                # One seat failing must not cost the run; the night
                # resolves without it and the boards already frozen
                # are still good.
                result.aborted = f"{seat} d{day}: {type(exc).__name__}: {exc}"
        else:
            result.aborted = f"gave up after {MAX_TURNS} turns"

    view = soc_engine.get_view(store, sid, order[0])
    result.days_played = max(0, int(view.get("day", 1)) - 1)
    result.scores = {
        s: int((view.get("scores") or {}).get(s) or 0) for s in order
    }
    result.seconds = round(time.time() - started, 1)
    return result
