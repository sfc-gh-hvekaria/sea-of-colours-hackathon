"""Time the Snowflake round-trips that bracket every agent turn.

The audit measures the dispatch only. Each seat turn also pays a
get_view before it and a submit + two audit writes after it, none of
which the audit can see. This times the read half out-of-band, against
the live store, without touching the running game.
"""
from __future__ import annotations

import os
import statistics
import sys
import time

os.environ.setdefault("SOC_BACKEND", "snowflake")

from sea_of_colours.snowpark import backend as soc_backend  # noqa: E402
from sea_of_colours.snowpark import engine as soc_engine  # noqa: E402


def timed(fn, n: int = 3):
    out = []
    for _ in range(n):
        t = time.time()
        fn()
        out.append((time.time() - t) * 1000.0)
    return out


def main() -> None:
    gid = sys.argv[1]
    store = soc_backend.get_store()

    for label, fn in (
        ("get_session_status", lambda: soc_engine.get_session_status(store, gid)),
        ("get_view(p1)", lambda: soc_engine.get_view(store, gid, "p1")),
        ("get_view(p2)", lambda: soc_engine.get_view(store, gid, "p2")),
    ):
        try:
            ms = timed(fn)
            print(f"  {label:22} {statistics.mean(ms):8.0f} ms mean   "
                  f"{[round(m) for m in ms]}")
        except Exception as exc:
            print(f"  {label:22} ERROR {type(exc).__name__}: {str(exc)[:90]}")


if __name__ == "__main__":
    main()
