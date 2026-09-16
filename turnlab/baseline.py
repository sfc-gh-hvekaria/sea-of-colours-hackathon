"""Stock V12's own answer to every board, frozen and checked in.

The lab's question is "how is my agent different from the one I forked",
and answering it needs V12's take on the same night. Asking V12 live
would mean a Snowflake PAT, a model call and about seventeen seconds
every time you wanted to look — and, because V12 is an LLM, a slightly
different answer each time. A reference that moves is not a reference.

So V12 runs once per board and seat, and the whole envelope is written
here: the prompts as sent, the reasoning, the option menu it was shown,
the directive it chose, the moves the engine accepted, and anything the
sanitiser corrected. A fork can then be diffed against it offline, with
no credentials and no waiting.

**Unarmed, deliberately.** Stock V12 cannot emit a weapon order at all —
``emp_launch`` and ``chaff_flare`` appear nowhere in its reply schema or
its option menu, only in ``last_night.py``, which narrates what already
happened rather than choosing what to do next. Handing it a rack changes
what it can see and never what it can do, so one baseline per board is
the honest number. That gap is the point of the exercise: a fork that
learns to use the rack has something to show, and this is what it will
be shown against.

Re-record with::

    python -m turnlab.baseline            # anything missing
    python -m turnlab.baseline --force    # all of it, again
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Optional

#: Checked in, unlike ``data/``. This is the reference forks are measured
#: against; one that every machine regenerates for itself is not one.
DIR = pathlib.Path(__file__).resolve().parent / "baseline"

#: The agent being frozen. Stock V12 — the thing attendees fork.
AGENT = "tabula_v12"


def path_for(board_id: str, seat: str) -> pathlib.Path:
    return DIR / f"{board_id}__{seat}.json"


def load(board_id: str, seat: str) -> Optional[dict]:
    """The frozen take, or ``None`` if this board was never recorded."""
    p = path_for(board_id, seat)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def have(board_id: str, seat: str) -> bool:
    return path_for(board_id, seat).is_file()


def record(board_id: str, seat: str, *, store: Any = None) -> dict:
    """Run V12 on a board and freeze the result.

    Opens its own throwaway clone, exactly as the page does, so what is
    frozen is a take from the same code path a live invocation uses —
    not a special recording mode that could drift away from it.
    """
    from . import store as lab_store
    from . import turn as lab_turn

    store = store if store is not None else lab_store.store()
    run_id = lab_turn.open_board(board_id, store=store)["run_id"]
    try:
        take = lab_turn.plan_only(run_id, seat, AGENT, store=store)
    finally:
        lab_turn._discard(store, run_id)

    import datetime as _dt

    take["baseline"] = {
        "board": board_id,
        "seat": seat,
        "agent": AGENT,
        # An LLM answers differently each time, so when this was taken is
        # part of reading it. A diff against a two-month-old reference is
        # a diff against a two-month-old model as much as an old prompt.
        "recorded": _dt.datetime.now(_dt.timezone.utc)
        .isoformat(timespec="seconds"),
        "armed": False,
    }
    DIR.mkdir(parents=True, exist_ok=True)
    path_for(board_id, seat).write_text(
        json.dumps(take, indent=1, sort_keys=True), encoding="utf-8",
    )
    return take


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    from . import boards as lab_boards
    from . import store as lab_store

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="re-record boards that already have a baseline")
    ap.add_argument("--board", default="", help="just this one")
    ap.add_argument("--seat", default="", help="just this seat")
    args = ap.parse_args(argv)

    store = lab_store.store()
    every = lab_boards.discover(store)
    if args.board:
        every = [b for b in every if b.id == args.board]
        if not every:
            print(f"no board named {args.board!r}")
            return 1

    # v1.42 — ask the board, don't assume two seats. Hardcoding p1/p2
    # meant a three-seat night recorded a baseline for two of its seats
    # and the third had nothing to be diffed against.
    def seats_of(board: Any) -> tuple[str, ...]:
        if args.seat:
            return (args.seat,)
        return board.castable or ("p1", "p2")

    todo = [
        (b.id, s) for b in every for s in seats_of(b)
        if args.force or not have(b.id, s)
    ]
    if not todo:
        on_disk = sum(len(seats_of(b)) for b in every)
        print(f"nothing to do — {on_disk} baseline(s) already on disk")
        return 0

    print(f"recording {len(todo)} baseline(s) with {AGENT}; "
          f"this calls a model, so it is slow\n")
    failed = 0
    for i, (board_id, seat) in enumerate(todo, 1):
        started = time.time()
        print(f"  [{i}/{len(todo)}] {board_id} {seat} … ", end="", flush=True)
        try:
            take = record(board_id, seat, store=store)
        except Exception as exc:
            failed += 1
            print(f"FAILED: {exc}")
            continue
        moves = len(take.get("moves") or [])
        fell_back = bool((take.get("plan") or {}).get("fallback_used"))
        print(f"{moves} order(s) in {time.time() - started:.1f}s"
              + ("  ** FALLBACK, no live think **" if fell_back else ""))

    print(f"\nwritten to {DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
