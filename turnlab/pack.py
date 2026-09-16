"""Make the frozen turns small enough to live in the repo.

A board is a real saved game, and a saved game's replay frames are
enormous: one dense per-cell percept per hour per seat, restated every
hour with a handful of cells different. The ten-turn library is 107MB
on disk and 1.6MB after this, which is the difference between a lab
everybody clones and a lab that only exists on the machine that made it.

Two reductions, in this order.

**Drop the nights the board cannot use.** A frozen turn on night N reads
exactly one night of history — night N-1, which is where ``YOU ORDERED``
and the execution log come from. A re-walked board carries every night
the season played, and nights 1..N-2 are dead weight that no prompt and
no screen ever reads.

**Then gzip what is left.** Nothing clever; the redundancy is already in
the data.

Boards only. A ``LABRUN_`` clone is scratch and is swept, not packed,
and nothing in the lab ever *writes* a ``.gz`` — reading one is a
fallback in :class:`turnlab.store.LabStore`, so a packed board behaves
exactly like an unpacked one.

    python -m turnlab.pack            # report what it would save
    python -m turnlab.pack --write    # do it

Only frames are touched. The other half of making a board shippable is
:mod:`turnlab.freeze`, which captures the seat's journal into it — the
same problem (the board travels, its season does not) solved in the
opposite direction, by adding a small file rather than shrinking a
large one.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import re
from typing import Iterator

#: ``LAB_<source>_d<day>_p<seat>`` — the day is the night being planned.
_BOARD_DAY = re.compile(r"_d(\d+)_p\d+$")

FRAMES = "frames.jsonl"
PACKED = "frames.jsonl.gz"


def frozen_day(board_id: str) -> int:
    """The night a board is frozen on, or 0 if its id does not say."""
    m = _BOARD_DAY.search(str(board_id or ""))
    return int(m.group(1)) if m else 0


def _needed_nights(board_id: str) -> set[int]:
    """Which nights of replay a board actually has a use for.

    Just the one before it. Kept as a set rather than an int because the
    honest answer is "the history this turn can read", and if a future
    prompt ever reaches two nights back this is the line that changes.
    """
    day = frozen_day(board_id)
    return {day - 1} if day > 1 else set()


def _rows(path: pathlib.Path) -> Iterator[tuple[int, str]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                day = int(json.loads(line).get("day") or 0)
            except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
                continue
            yield day, line


def pack_board(board_dir: pathlib.Path, *, write: bool) -> dict:
    """Trim and compress one board's frames. Returns a size report."""
    raw = board_dir / FRAMES
    out = {"board": board_dir.name, "before": 0, "after": 0, "nights": []}
    if not raw.exists():
        packed = board_dir / PACKED
        if packed.exists():
            out["before"] = out["after"] = packed.stat().st_size
            out["already"] = True
        return out

    out["before"] = raw.stat().st_size
    keep_nights = _needed_nights(board_dir.name)
    kept = [line for day, line in _rows(raw) if day in keep_nights]
    out["nights"] = sorted(keep_nights)
    blob = "".join(kept).encode("utf-8")

    if write:
        # Written before the original is removed: a crash between the
        # two leaves a board with both files, which reads correctly
        # (raw wins), rather than one with neither.
        with gzip.open(board_dir / PACKED, "wb", compresslevel=9) as fh:
            fh.write(blob)
        raw.unlink()
        out["after"] = (board_dir / PACKED).stat().st_size
    else:
        out["after"] = len(gzip.compress(blob, 9))
    return out


def main() -> int:
    from . import store as lab_store

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="actually rewrite the boards (default: report only)")
    args = ap.parse_args()

    boards = sorted(
        d for d in lab_store.DATA_DIR.glob(f"{lab_store.BOARD_PREFIX}*")
        if d.is_dir()
    )
    if not boards:
        print(f"no boards under {lab_store.DATA_DIR}")
        return 1

    before = after = 0
    for d in boards:
        r = pack_board(d, write=args.write)
        before += r["before"]
        after += r["after"]
        note = "already packed" if r.get("already") else (
            f"night {r['nights'][0]}" if r["nights"] else "no history needed"
        )
        print(f"  {r['board']:<24} {r['before'] / 1e6:7.1f}MB -> "
              f"{r['after'] / 1e6:6.2f}MB   ({note})")

    verb = "packed" if args.write else "would pack"
    print(f"\n{verb}: {before / 1e6:.1f}MB -> {after / 1e6:.2f}MB")
    if not args.write:
        print("re-run with --write to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
