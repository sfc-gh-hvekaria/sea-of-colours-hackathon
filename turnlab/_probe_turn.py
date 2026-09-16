"""Drive one frozen turn end to end against a running lab server.

Heuristic seats only, so it costs nothing and finishes in seconds. It
exists to answer questions the UI cannot answer about itself: which days
the replay offers after the night resolves, and what the phase settles
on. Point it at a server you started.

    python -m turnlab._probe_turn --port 8022 --board LAB_b67deb8d_d3_p1
"""
from __future__ import annotations

import argparse
import json
import urllib.request


def _call(base: str, path: str, payload: dict | None = None) -> dict:
    url = f"{base}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.loads(res.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8022)
    ap.add_argument("--board", default="LAB_b67deb8d_d3_p1")
    ap.add_argument("--agent", default="red_harvest_lite")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    opened = _call(base, "/api/lab/open", {"board": args.board})
    run = opened.get("run_id") or opened.get("session")
    print(f"run      : {run}")
    print(f"url      : {opened.get('url')}")

    st = _call(base, f"/api/game/{run}/status")
    seats = list(st.get("players") or [])
    day = st.get("day")
    print(f"day      : {day}   seats {seats}   phase {st.get('phase')}")

    before = _call(base, f"/api/game/{run}/replay")
    print("frames before:", sorted(
        {int(f.get("day") or 0) for f in (before.get("frames") or [])}
    ))

    for seat in seats:
        plan = _call(base, "/api/lab/plan", {
            "session": run, "seat": seat, "agent": args.agent,
        })
        moves = plan.get("moves") or []
        print(f"  {seat}: planned {len(moves)} move(s)")
        _call(base, "/api/lab/commit", {
            "session": run, "seat": seat, "moves": moves,
        })

    st2 = _call(base, f"/api/game/{run}/status")
    print(f"night    : day {st2.get('day')}  phase {st2.get('phase')}"
          f"  scores {st2.get('scores')}")

    settled = _call(base, "/api/lab/settle", {"session": run})
    print(f"settle   : {settled.get('settled')}  ->  day {settled.get('day')}"
          f"  phase {settled.get('phase')}  scores {settled.get('scores')}")
    again = _call(base, "/api/lab/settle", {"session": run})
    print(f"settle x2: {again.get('settled')}  ({again.get('note') or 'n/a'})")

    st2 = _call(base, f"/api/game/{run}/status")
    after = _call(base, f"/api/game/{run}/replay")
    days = sorted({int(f.get("day") or 0) for f in (after.get("frames") or [])})
    print(f"after    : day {st2.get('day')}  phase {st2.get('phase')}")
    print(f"frames after : {days}")
    print(f"day_index    : "
          f"{[r.get('day') for r in (after.get('day_index') or [])]}")

    frozen = int(str(args.board).split("_d")[1].split("_")[0])
    stale = [d for d in days if d < frozen]
    print(f"\nfrozen night : {frozen}")
    print(f"rewindable   : {stale or 'none'}"
          f"{'   <-- the scrubber must hide these' if stale else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
