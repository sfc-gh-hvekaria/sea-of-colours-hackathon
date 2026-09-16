"""Wall-clock timeline for a running game, sampled from /status.

The audit only measures time INSIDE the agent. This measures what a
watcher actually waits through: each seat's turn, and -- critically --
the gap between the last seat submitting and the next day opening,
which is where night resolve + persistence live.

    python scripts/_v12_timing.py <session_id> [seconds]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def snap(gid: str):
    with urllib.request.urlopen(f"{BASE}/api/game/{gid}/status", timeout=30) as r:
        d = json.load(r)
    bt = d.get("bot_turn") or {}
    return (
        d.get("day"),
        d.get("phase"),
        bt.get("seat"),
        bool(d.get("is_season_complete")),
    )


def main() -> None:
    gid = sys.argv[1]
    budget = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0

    t0 = time.time()
    last = None
    last_t = t0
    # Unbuffered: this runs backgrounded into a file, and a SIGTERM (the
    # usual way it ends) discards a block buffer, losing the whole run.
    print(f"{'t+':>8}  {'held':>7}  state", flush=True)
    while time.time() - t0 < budget:
        try:
            cur = snap(gid)
        except Exception as exc:
            print(f"  poll error: {type(exc).__name__}")
            time.sleep(2.0)
            continue
        if cur != last:
            now = time.time()
            if last is not None:
                day, phase, seat, _ = last
                who = f"{seat} thinking" if seat else "NO BOT (resolve/idle)"
                print(f"{now - t0:8.1f}s  {now - last_t:6.1f}s  "
                      f"day {day} {phase:<9} {who}", flush=True)
            last, last_t = cur, now
        if cur[3]:
            print("season complete")
            return
        time.sleep(1.0)


if __name__ == "__main__":
    main()
