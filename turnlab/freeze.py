"""Freeze each board's journal beside it, so the memory ships too.

A frozen turn is supposed to be the night exactly as its seat found it,
and for a V12 seat most of that is the STRATEGY JOURNAL — the INTENT it
set each night and the REFLECTION it wrote the morning after. The lab
recovered that by reading the source season at invoke time, which works
beautifully on the machine that made the board and nowhere else. The
boards travel in the repo; their seasons do not. ``seasons/`` is
ignored, the minted sources are ignored, and a grabbed board's season
lives in whichever Snowflake account played it. An attendee cloning this
repo has none of the three, so every lookup missed and the two boards
where memory is the whole exercise planned blind.

So the journal gets frozen into the board, next to the state and the
frames, and :func:`turnlab.recall.frozen_journal` reads it there. It is
small — a few hundred bytes a night, against the megabyte of gzipped
frames already sitting in the same directory.

Cut at the board's own day, and that cut is the load-bearing line in
this file. A board frozen on night six may carry nights one to five;
night six's own entry holds the reflection written the *morning after*,
so shipping it would put tomorrow's newspaper inside the turn.

Run on the machine that can still see the seasons — which today means
the one with the Snowflake account that played them:

    python -m turnlab.freeze            # report what it would capture
    python -m turnlab.freeze --write    # write journal.json into each board

A board whose season cannot be reached is skipped, never emptied: a
missing account is not evidence that a seat kept no journal, and
overwriting a good file with that claim would be the worst outcome
available.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
from typing import Optional

from . import recall
from . import store as lab_store
from .pack import frozen_day


def freeze_board(board_id: str, *, write: bool) -> dict:
    """Capture one board's journal. Returns a report; writes nothing
    unless ``write``."""
    out: dict = {
        "board": board_id,
        "day": frozen_day(board_id),
        "seats": {},
        "source": "",
        "skipped": "",
        "bytes": 0,
    }
    day = out["day"]
    if not day:
        out["skipped"] = "its id does not say which night it is"
        return out

    seats = recall.seats_of(board_id)
    if not seats:
        out["skipped"] = "its cast could not be read"
        return out

    candidates = recall.sources_of(board_id)
    if not candidates:
        out["skipped"] = "its season is not on this machine"
        return out

    # Whichever copy of the season actually held the memory. A file-store
    # copy corroborates the board just as well as the account that played
    # it and has no journal rows at all, so "first match" is not enough.
    source_id, seats_out = "", {}
    for cand_id, cand_store in candidates:
        got = {
            seat: sorted(
                (dict(e) for e in recall.read_journal(cand_id, seat, cand_store)
                 if 0 < int(e.get("day") or 0) < int(day)),
                key=lambda e: int(e.get("day") or 0),
            )
            for seat in seats
        }
        source_id, seats_out = cand_id, got
        if any(got.values()):
            break

    out["source"] = source_id
    out["seats"] = {s: [int(e.get("day") or 0) for e in d]
                    for s, d in seats_out.items()}

    blob = {
        "board": board_id,
        "source": source_id,
        "day": day,
        "frozen_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "seats": seats_out,
    }
    text = json.dumps(blob, indent=1, sort_keys=True, default=str) + "\n"
    out["bytes"] = len(text.encode("utf-8"))

    if write:
        path = pathlib.Path(lab_store.DATA_DIR) / board_id / recall.JOURNAL_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        recall.forget()
    return out


def _boards() -> list[str]:
    return sorted(
        d.name for d in lab_store.DATA_DIR.glob(f"{lab_store.BOARD_PREFIX}*")
        if d.is_dir() and not lab_store.is_run(d.name)
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="turnlab.freeze", description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="actually write journal.json (default: report only)")
    ap.add_argument("--board", default="",
                    help="just this one board (default: all of them)")
    args = ap.parse_args(argv)

    boards = [args.board] if args.board else _boards()
    if not boards:
        print(f"no boards under {lab_store.DATA_DIR}")
        return 1

    total = nights = 0
    for board in boards:
        r = freeze_board(board, write=args.write)
        if r["skipped"]:
            # Not an error. Most of the library is minted and played by
            # heuristics, which keep no journal and never had a season
            # worth shipping.
            print(f"  {board:<24} skipped — {r['skipped']}")
            continue
        got = {s: d for s, d in r["seats"].items() if d}
        nights += sum(len(d) for d in got.values())
        total += r["bytes"]
        summary = ", ".join(
            f"{s} d{d[0]}-{d[-1]}" for s, d in sorted(got.items())
        ) or "no seat kept one"
        print(f"  {board:<24} {r['bytes'] / 1e3:5.1f}kB  {summary}")

    verb = "froze" if args.write else "would freeze"
    print(f"\n{verb}: {nights} night(s) across the library, "
          f"{total / 1e3:.1f}kB total")
    if not args.write:
        print("re-run with --write to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
