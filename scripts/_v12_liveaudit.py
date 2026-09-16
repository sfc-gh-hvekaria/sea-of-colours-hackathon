"""Dump the per-invocation audit for a live game's LLM seat.

Answers one question only: what is actually being invoked, in what order,
for how long, and what did the engine accept — as opposed to what the
harness thought it planned. Written to compare live play against a
headless season run, which uses the same runtime entrypoint.
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"


def fetch(path: str):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


def main() -> None:
    gid = sys.argv[1]
    rows = fetch(f"/api/game/{gid}/agent-log")
    if isinstance(rows, dict):
        rows = rows.get("invocations") or []
    if rows:
        print("keys:", sorted(rows[0].keys()), "\n")
    print(f"{len(rows)} audit rows\n")
    for r in rows:
        day = r.get("day")
        agent = r.get("agent_id") or r.get("agent")
        seat = r.get("player") or r.get("seat")
        runtime = r.get("runtime")
        ms = r.get("ms_elapsed")
        rat = r.get("rationale") or ""
        if isinstance(rat, dict):
            rat = json.dumps(rat)
        rat = str(rat).replace("\n", " ")
        calls = r.get("tool_calls")
        ncalls = len(calls) if isinstance(calls, list) else 0
        print(f"day {day} seat={seat} agent={agent} runtime={runtime} "
              f"ms={ms} tool_calls={ncalls}")
        if isinstance(calls, list):
            for c in calls:
                if isinstance(c, dict):
                    print(f"    - {c.get('name')} {json.dumps(c.get('args'))[:200]}")
        print(f"    rationale: {rat[:400]}")
        print()


if __name__ == "__main__":
    main()
