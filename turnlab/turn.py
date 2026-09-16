"""Play one frozen turn with whichever agent you like.

The snapshot is never played in. Every run clones it into a throwaway
session, plays there, reads back what the agent did, and drops the
clone — so the same night can be handed to V12, to the heuristic and to
an attendee's fork, and the three compared, with the board still
pristine at the end.

This mirrors ``scripts/replay_turn.py``, which does the same thing from
the command line. The logic lives here as well rather than being
imported from there because the lab must not depend on a script's
argument parsing, and because a viewer that can be broken by someone
editing a CLI is not much of a viewer.

What comes back is what the agent *decided* and what it was *working
from*: the compiled orders, its stated reasoning, and the option menu
it was offered. That last one is the part usually lost — an agent's
choice only means something next to the choices it was shown.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Sequence

from . import arms as lab_arms
from . import store as lab_store
from .isolation import evict_session


def open_board(
    board_id: str,
    *,
    arms: Mapping[str, str] | None = None,
    store: Any = None,
) -> dict:
    """Clone a frozen turn into a session the ordinary game UI can open.

    Every seat is set to ``human`` on the way out, and that is the whole
    trick. A snapshot carries the season's agent assignments, so opening
    one as-is makes the server fan the bots out the moment the page
    loads: the night resolves, the calendar rolls, the bots fire again,
    and a board frozen on day 2 arrives at season_complete before anyone
    has looked at it. Handing every seat to a human freezes the clock
    until somebody actually asks for a turn.

    Which is what the lab wants anyway — the point is to invoke a
    chosen agent into a chosen seat and watch, not to let the season
    run itself.

    ``arms`` maps a seat to a rack id from :mod:`turnlab.arms`. It is
    applied to the clone, never to the board, so the same frozen night
    can be opened armed and unarmed and the only difference between the
    two runs is the rack.
    """
    from sea_of_colours.snowpark import snapshot as soc_snapshot

    store = store if store is not None else lab_store.store()

    # Name the clone, rather than relying on the ``REPLAY:`` season name
    # ``clone_for_run`` stamps on the row. That stamp does not survive:
    # the engine rebuilds the row from the state blob on its first save,
    # and the blob still carries the original season's name — so the
    # marker vanished the moment the first seat played, and the second
    # invocation was refused as a real game. The id is the one field
    # nothing rewrites.
    run_id = soc_snapshot.clone_for_run(
        store, board_id, run_id=f"{lab_store.RUN_PREFIX}{uuid.uuid4().hex[:16]}"
    )
    _rekey(store, run_id)

    row = store.load_session(run_id)
    blob = row.get("json_state")
    while isinstance(blob, str):
        blob = json.loads(blob)

    seats = list(blob.get("players") or [])
    blob["agents"] = {seat: "human" for seat in seats}

    racked: dict[str, str] = {}
    for seat, rack_id in (arms or {}).items():
        if seat not in seats:
            continue
        rack = lab_arms.arm(blob, seat, rack_id)
        if rack.id != lab_arms.DEFAULT:
            racked[seat] = rack.id

    out = dict(row)
    out["json_state"] = blob
    store.save_session(out)

    # Provenance, so an agent invoked here can recover the season's
    # memory without the caller having to hand the board back.
    lab_store.remember_board(run_id, board_id)

    # Take out the bins on the way in. An opened run cannot be discarded
    # when the turn ends — the page is still holding it — so without
    # this nothing ever reclaims one, and "open another turn" is now the
    # normal thing to do twenty times in an afternoon. Here rather than
    # on a timer because it is the only moment we know a new run is
    # wanted and an old one probably is not.
    try:
        lab_store.sweep_runs()
    except Exception:
        # Tidying is not worth failing an open over.
        pass

    return {
        "run_id": run_id,
        "day": int(blob.get("day") or 0),
        "seats": seats,
        "board": board_id,
        "arms": racked,
    }


def plan_only(
    session_id: str,
    seat: str,
    agent: str,
    *,
    store: Any = None,
) -> dict:
    """What would this agent do here — without doing it.

    The lab wants the advisor's manners: the agent thinks, you see the
    plan painted on the board, and *you* decide whether it stands. That
    needs a way to run a turn and commit nothing, and there isn't one.
    ``harness.run(submit=False)`` exists, but only on V12 — the heuristic
    runtime has no such path, and a fork is free to drop it, so building
    on it would mean the ACCEPT button worked for some agents and
    silently played the turn for others.

    So the turn is played for real, in a **scratch clone of the lab
    clone**, and thrown away. Every agent supports that, because it is
    just an ordinary turn. What comes back is the exact wire moves the
    engine accepted, which is what makes ACCEPT honest: committing
    replays those same moves rather than asking the agent again and
    hoping for the same answer.

    It also isolates the thinking. V12 writes journal and hazard memory
    keyed by ``(session, seat)``; done in the real clone, merely *asking*
    would leave a history of a turn that never happened.
    """
    import time

    from sea_of_colours.evals import dispatch
    from sea_of_colours.snowpark import engine as soc_engine

    from . import recall as lab_recall
    from . import store as lab_store

    store = store if store is not None else lab_store.store()
    started = time.time()
    scratch = ""
    recalled = lab_recall.Recalled(seat=seat)
    try:
        # Two agents thinking about one board must not inherit each
        # other's memory — the V12 harness remembers across turns by
        # design. The scratch clone's fresh id gives that for free: every
        # harness cache is keyed by session, so this run starts with none.
        #
        # It used to call reset_harness_caches(force=True) instead, which
        # was wrong in a way worth recording. Those caches are process
        # globals shared with any season running in the same server, so
        # wiping them to isolate a lab plan would have erased a live
        # agent's hazard memory mid-game — the exact thing isolation.py
        # was written to prevent, defeated by passing force.
        scratch = _scratch_clone(store, session_id)
        day = int(soc_engine.get_session_status(store, scratch).get("day", 1))

        # Give the scratch run the season's memory before anyone asks it
        # to think. Keyed to the scratch id, which is why this has to
        # happen after the clone and cannot be done once at open time:
        # every invocation gets a fresh id, and the harness looks under
        # whichever id it is playing.
        recalled = lab_recall.hydrate(
            scratch,
            seat,
            agent,
            board_id=lab_store.board_of(session_id),
            day=day,
            store=store,
        )

        # What everyone is holding needs no help from here any more.
        # ``arms.disclose`` used to seed each fork's estimator directly,
        # because a rival's rack was private and a frozen turn gave the
        # inference nothing to work from. Since v1.34 the engine
        # broadcasts weaponised blue off ``weapon_stock`` (§4.9.8) — the
        # field ``arms.arm`` stamped at open time — so the ordinary
        # percept already carries it, for the lab exactly as for a real
        # season.

        env = dispatch.play_turn(store, scratch, seat, agent) or {}
        moves = list((store.list_policies(scratch, day) or {}).get(seat) or [])
    finally:
        if scratch:
            evict_session(scratch)
            _discard(store, scratch)

    extras = env.get("extras") if isinstance(env.get("extras"), Mapping) else {}
    extras = extras or {}
    directive = extras.get("thinker_directive") or {}

    # Deliberately the same shape the V12 advisor returns, so the page can
    # render a fork's turn with the code that renders V12's.
    return {
        "ok": True,
        "seat": seat,
        "agent": agent,
        "day": day,
        "elapsed_ms": int((time.time() - started) * 1000),
        # What the agent was given to remember. Reported rather than
        # assumed: a turn planned without the season's journal is a
        # different turn, and the page should be able to say so.
        "memory": recalled.as_dict(),
        "moves": moves,
        "rationale": str(env.get("rationale") or env.get("agent_rationale") or ""),
        "thinking": {
            "reasoning": extras.get("thinker_reasoning") or "",
            "api": extras.get("thinker_api") or "",
            "ms": int(extras.get("thinker_ms") or 0),
            "retried": bool(extras.get("thinker_retried")),
            "option_menu": extras.get("option_menu_block") or "",
            "intent": extras.get("agent_intent") or "",
            "reflection": extras.get("agent_reflection") or "",
        },
        # Untruncated, as sent. The audit table caps its copy at 32k, so
        # for a fork being debugged this is the only complete record.
        "prompts": {
            "think": extras.get("thinker_prompt") or "",
            "plan": extras.get("plan_prompt") or "",
            "mover": extras.get("mover_prompt") or "",
        },
        "plan": {
            "posture": directive.get("posture") or "",
            "note": directive.get("note") or "",
            "plan_ids": list(directive.get("plan") or []),
            "targets": directive.get("targets") or [],
            "avoid": directive.get("avoid") or [],
            "situational": directive.get("situational") or "",
            "selected_options": list(extras.get("selected_option_ids") or []),
            "sanitizer_changes": list(extras.get("sanitizer_changes") or []),
            "packager_used": bool(extras.get("packager_used")),
            "fallback_used": bool(extras.get("fallback_used")),
        },
    }


def commit(
    session_id: str,
    seat: str,
    moves: Sequence[Mapping[str, Any]],
    *,
    store: Any = None,
) -> dict:
    """Put an accepted plan into the real clone.

    The counterpart to :func:`plan_only`: the same wire moves, submitted
    for real. No agent runs here, so what lands is exactly what was on
    screen when you pressed the button.

    Submitting the last outstanding seat resolves the night, which is
    ordinary engine behaviour and the whole point — the lab does not
    trigger nights, it just stops being the reason one is waiting.
    """
    from sea_of_colours.snowpark import engine as soc_engine

    from . import store as lab_store

    store = store if store is not None else lab_store.store()
    day = int(soc_engine.get_session_status(store, session_id).get("day", 1))
    soc_engine.submit_policy(store, session_id, seat, list(moves))

    after = soc_engine.get_session_status(store, session_id)
    return {
        "ok": True,
        "seat": seat,
        "day": day,
        "resolved": int(after.get("day", day)) != day,
        "phase": after.get("phase"),
        "pending": after.get("pending"),
    }


def settle(session_id: str, *, store: Any = None) -> dict:
    """Close the day the frozen night opened, and stop at the next vespera.

    A night on its own does not tell you what it was worth. RED is still
    in the vault when the last hour resolves; it only becomes score at
    the orbit settlement, and GREEN only becomes a penalty there. So a
    turn watched to the end of the night is watched one beat short of
    the number everyone actually argues about.

    The baskets are empty on purpose. Settlement ships RED automatically
    (RULEBOOK §4, v1.13 — no bid), so nothing needs to be bought for the
    catapults to load and the score to move. Spending here would mean
    the lab quietly making a second decision on the seat's behalf, one
    the human never saw and did not accept, and then showing them a
    balance shaped by it. The turn under test is the night.

    Stops at the next planning phase. That is the whole point of a
    frozen turn: one night, its consequences, and no tomorrow.
    """
    from sea_of_colours.snowpark import engine as soc_engine

    from . import store as lab_store

    store = store if store is not None else lab_store.store()
    status = soc_engine.get_session_status(store, session_id)
    phase = str(status.get("phase") or "")
    if phase != "orbit":
        return {
            "ok": True,
            "settled": False,
            "phase": phase,
            "day": status.get("day"),
            "note": f"nothing to settle — the session is in '{phase}'",
        }

    for seat in list(status.get("players") or []):
        soc_engine.submit_orbit_actions(store, session_id, seat, [])

    after = soc_engine.get_session_status(store, session_id)
    return {
        "ok": True,
        "settled": str(after.get("phase") or "") != "orbit",
        "phase": after.get("phase"),
        "day": after.get("day"),
        "scores": after.get("scores") or after.get("cumulative_shipped_score"),
    }


def _scratch_clone(store: Any, session_id: str) -> str:
    """A disposable copy of a session, for a turn nobody has agreed to yet."""
    from sea_of_colours.snowpark import snapshot as soc_snapshot

    from . import store as lab_store

    rid = soc_snapshot.clone_for_run(
        store, session_id, run_id=f"{lab_store.RUN_PREFIX}try{uuid.uuid4().hex[:12]}"
    )
    _rekey(store, rid)
    # Carry the board forward, not just the state: it is how the scratch
    # run finds last night's replay frames and the season's journal. A
    # clone that forgets where it came from plans as though the season
    # started this morning.
    lab_store.remember_board(rid, lab_store.board_of(session_id))
    return rid


def _rekey(store: Any, run_id: str) -> None:
    """Point a fresh clone's inner state at itself.

    ``snapshot.clone_for_run`` re-keys the session *row* but leaves
    ``json_state.session_id`` naming the season the snapshot was cut
    from. The engine hydrates from ``json_state``, so ``sess.session_id``
    comes back as the *original* season and every later
    ``save_session_full`` writes there instead of the clone. The clone
    then looks inert — its blob never changes — while the source season
    quietly collects the replay's policies and log lines.

    That is a bug in the shared snapshot helper, and ``scripts/
    replay_turn.py`` inherits it. It is corrected here rather than
    there because the lab may not edit the engine, and because a lab
    that silently writes into somebody's real season is the exact
    failure this package exists to make impossible. Worth fixing at
    source separately.
    """
    row = store.load_session(run_id)
    if not row:
        return
    blob = row.get("json_state")
    while isinstance(blob, str):
        blob = json.loads(blob)
    if not isinstance(blob, MutableMapping):
        return
    if blob.get("session_id") == run_id:
        return
    blob["session_id"] = run_id
    out = dict(row)
    out["json_state"] = blob
    store.save_session(out)


def source_of(snapshot_id: str) -> str:
    """The season a snapshot was cut from, read off its id.

    ``LAB_dd868733_d2_p1`` came from ``dd868733``. Only a prefix
    survives the name, so the caller has to widen it.
    """
    parts = snapshot_id.split("_")
    return parts[1] if len(parts) >= 4 else ""


def _widen(prefix: str, store: Any) -> str:
    """Expand a short session id to the full one, or "" if it is unclear.

    A snapshot name only carries the first eight characters of the
    season it came from. Ambiguity is treated as not-found rather than
    guessed at, because picking the wrong season would silently give
    the opponent somebody else's orders.
    """
    if not prefix:
        return ""
    try:
        rows = store.list_sessions() or []
    except Exception:
        return ""
    hits = [
        str(r.get("session_id"))
        for r in rows
        if str(r.get("session_id", "")).startswith(prefix)
        and not str(r.get("session_id", "")).startswith("LAB_")
    ]
    return hits[0] if len(hits) == 1 else ""


def seat_of(snapshot_id: str) -> str:
    """The seat a snapshot was taken for, read off its id."""
    tail = snapshot_id.rsplit("_", 1)[-1]
    return tail if tail.startswith("p") and tail[1:].isdigit() else "p1"


def _discard(store: Any, run_id: str) -> None:
    """Drop a throwaway clone. Best effort: a leftover costs disk, not
    correctness, and failing the turn over tidy-up would be worse."""
    for name in ("delete_session", "drop_session", "remove_session"):
        fn = getattr(store, name, None)
        if callable(fn):
            try:
                fn(run_id)
                return
            except Exception:
                return
