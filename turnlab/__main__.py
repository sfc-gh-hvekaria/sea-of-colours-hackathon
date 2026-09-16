"""Grab a turn out of a season you already played.

    python -m turnlab list                     # what is grabbable
    python -m turnlab grab CONTROL_s42_file 5  # take day 5 as a board
    python -m turnlab check CONTROL_s42_file   # prove the re-walk is exact

``--from`` picks the store to read seasons out of. It defaults to
``seasons/``, the on-disk store the local server uses; pass ``snowflake``
to grab from a real account, or any directory for another file store.
The lab always *writes* to ``turnlab/data`` regardless, which is what
keeps a grab from being able to touch the season it came from.
"""

from __future__ import annotations

import argparse
import sys

from . import rewalk
from . import store as lab_store


def _source_store(spec: str):
    if spec in ("snowflake", "sf"):
        from sea_of_colours.snowpark import backend

        return backend.get_store_for("snowflake")
    from sea_of_colours.snowpark.file_store import FileSocStore

    return FileSocStore(spec)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="turnlab", description=__doc__)
    ap.add_argument("--from", dest="src", default="seasons",
                    help="store to read seasons from (default: seasons/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="seasons and the days they can offer")

    g = sub.add_parser("grab", help="turn a season + day into a lab board")
    g.add_argument("season", help="season name, a unique prefix, or session id")
    g.add_argument("day", type=int)
    g.add_argument("--seat", default="p1",
                   help="which seat the board opens on (default: p1)")
    g.add_argument("--replace", action="store_true",
                   help="re-walk even if this board was grabbed before")

    c = sub.add_parser("check", help="re-walk to the end and diff")
    c.add_argument("season", nargs="?", default="",
                   help="one season, or omit for all of them")

    args = ap.parse_args(argv)
    src = _source_store(args.src)

    if args.cmd == "list":
        found = rewalk.sources(src, skip=(lab_store.BOARD_PREFIX,
                                          lab_store.RUN_PREFIX))
        if not found:
            print(f"no grabbable seasons in {args.src!r}")
            return 1
        print(f"{'season':30} {'seed':>7}  days you can grab")
        for s in found:
            days = ", ".join(str(d) for d in s.playable_days)
            print(f"{s.name[:30]:30} {s.seed:>7}  {days}")
        blocked = [s for s in rewalk.sources(src, include_blocked=True)
                   if s.blocked]
        for s in blocked:
            print(f"\n  {s.name}: cannot be grabbed — {s.blocked}")
        return 0

    if args.cmd == "grab":
        try:
            got = rewalk.grab(args.season, args.day, src=src,
                              into=lab_store.store(), seat=args.seat,
                              replace=args.replace)
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"{got.source_name} day {got.day} -> {got.session_id}")
        if got.walked:
            print(f"  re-walked {got.walked} night(s) from seed {got.seed}")
        else:
            print("  already grabbed; reusing it")
        for note in got.complaints:
            print(f"  ! {note}", file=sys.stderr)
        print(f"  open it at /lab and pick {got.session_id}")
        return 1 if got.complaints else 0

    # check
    from sea_of_colours.snowpark.store import InMemorySocStore

    pool = rewalk.sources(src)
    if args.season:
        one = rewalk.find(src, args.season)
        pool = [one] if one else []
        if not pool:
            print(f"error: no season matching {args.season!r}", file=sys.stderr)
            return 1

    bad = 0
    for s in pool:
        got = rewalk.check(s.session_id, src=src, into=InMemorySocStore())
        broke = [k for k, v in got["fields"].items() if not v]
        good = got["ok"] and not broke
        bad += 0 if good else 1
        note = "" if good else f"  broken={broke} {got['complaints']}"
        print(f"  {'OK  ' if good else 'FAIL'} {s.name[:28]:28} "
              f"d{got['day']}{note}")
    print(f"\n{len(pool) - bad} exact, {bad} not")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
