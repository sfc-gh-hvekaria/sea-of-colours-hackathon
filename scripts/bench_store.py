#!/usr/bin/env python3
"""Time what a turn costs against a store, method by method.

Why this exists: a season on Snowflake takes ~700s where the same
season on the file backend takes ~160s, and every plausible-sounding
explanation for that gap has been wrong. Payload size was wrong (a
4-row write costs the same as a 1120-row one). Round-trip count was
wrong (22 per turn, not hundreds). Guessing does not work here, so
this measures instead.

It wraps the store in a proxy that records every call and how long it
took, then plays real turns through the real engine. Nothing in
``sea_of_colours/`` is modified or aware of it, so the numbers are of
the actual code path a player pays for — not of a mock.

    python scripts/bench_store.py --backend file --turns 4
    python scripts/bench_store.py --backend snowflake --turns 4

Read the output bottom-up: ``TOTAL`` is what a player waits for, and
the table above says which store calls it went into. ``calls`` matters
more than ``rows`` on a warehouse, where cost is per-statement.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TimingStore:
    """Record every store call's count, wall time and payload size.

    A proxy rather than a subclass because the three store impls share
    no base we can hook, and because this must stay strictly outside
    the code under test — an instrumented store that changed behaviour
    would measure itself.
    """

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "calls", collections.Counter())
        object.__setattr__(self, "seconds", collections.Counter())
        object.__setattr__(self, "rows", collections.Counter())

    def __getattr__(self, name: str) -> Any:
        attr = getattr(object.__getattribute__(self, "_inner"), name)
        if not callable(attr):
            return attr

        def wrapped(*a: Any, **k: Any) -> Any:
            # Widest list/dict in the args approximates the payload —
            # good enough to show that payload does NOT predict cost.
            size = 0
            for val in list(a) + list(k.values()):
                if isinstance(val, (list, tuple, dict)):
                    size = max(size, len(val))
            started = time.time()
            try:
                return attr(*a, **k)
            finally:
                elapsed = time.time() - started
                object.__getattribute__(self, "calls")[name] += 1
                object.__getattribute__(self, "seconds")[name] += elapsed
                object.__getattribute__(self, "rows")[name] += size

        return wrapped

    def reset(self) -> None:
        for bucket in ("calls", "seconds", "rows"):
            object.__getattribute__(self, bucket).clear()

    def report(self, label: str, wall: float) -> None:
        calls = object.__getattribute__(self, "calls")
        seconds = object.__getattribute__(self, "seconds")
        rows = object.__getattribute__(self, "rows")
        total_calls = sum(calls.values())
        in_store = sum(seconds.values())

        print(f"\n  {label}")
        print(f"    {'store method':<28s}{'calls':>6s}{'seconds':>10s}"
              f"{'ms/call':>10s}{'rows':>8s}")
        for name, _ in sorted(seconds.items(), key=lambda kv: -kv[1]):
            n = calls[name]
            print(f"    {name:<28s}{n:>6d}{seconds[name]:>10.2f}"
                  f"{seconds[name] / n * 1000:>10.0f}{rows[name]:>8d}")
        print(f"    {'':-<62s}")
        print(f"    {'WALL (what you wait for)':<28s}{total_calls:>6d}"
              f"{wall:>10.2f}")
        # ``save_session_full`` fans its phases across a thread pool, so
        # the per-call times overlap and their sum can exceed the wall
        # clock. That gap is the whole point of the measurement: if the
        # sum is far above the wall, parallelism is working; if the two
        # are close while many calls are in flight, the "parallel"
        # phases are really queuing on one connection.
        overlap = in_store / wall if wall else 0.0
        print(f"    {'  sum of store calls':<28s}{'':>6s}{in_store:>10.2f}"
              f"   (x{overlap:.1f} of wall)")
        if overlap > 1.15:
            print(f"    {'  -> phases overlap: pool is doing real work':<28s}")
        elif total_calls > 8:
            print(f"    {'  -> little overlap: calls are serialising':<28s}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", default="file",
                    choices=("memory", "file", "multi", "snowflake"))
    ap.add_argument("--turns", type=int, default=4,
                    help="heuristic turns to play and time")
    ap.add_argument("--width", type=int, default=40)
    ap.add_argument("--height", type=int, default=28)
    ap.add_argument("--seed", type=int, default=99)
    args = ap.parse_args(argv)

    # Set before importing the backend: resolution is read at import.
    os.environ["SOC_BACKEND"] = args.backend

    from sea_of_colours.evals import dispatch
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import engine as soc_engine

    print(f"backend={args.backend}  board={args.width}x{args.height}  "
          f"turns={args.turns}")

    started = time.time()
    raw = soc_backend.get_store()
    print(f"\n  connect                {time.time() - started:8.2f}s")

    store = TimingStore(raw)

    started = time.time()
    info = soc_engine.init_session(
        store, seed=args.seed, width=args.width, height=args.height,
        players=["p1", "p2"],
        agents={"p1": "red_harvest", "p2": "red_harvest"},
    )
    store.report("init_session", time.time() - started)
    sid = info["session_id"]

    # Heuristic seats on purpose: a model call would swamp the storage
    # cost we are here to measure, and the store path is identical.
    per_turn: list[float] = []
    for i in range(args.turns):
        seat = "p1" if i % 2 == 0 else "p2"
        status = soc_engine.get_session_status(store, sid)
        if status.get("phase") == "season_complete":
            print("\n  season complete early")
            break
        store.reset()
        started = time.time()
        dispatch.play_turn(store, sid, seat, "red_harvest")
        wall = time.time() - started
        per_turn.append(wall)
        store.report(f"turn {i + 1} ({seat})", wall)

    if per_turn:
        avg = sum(per_turn) / len(per_turn)
        print(f"\n  mean turn {avg:.2f}s  ·  a 26-turn season "
              f"would cost ~{avg * 26:.0f}s of store time")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
