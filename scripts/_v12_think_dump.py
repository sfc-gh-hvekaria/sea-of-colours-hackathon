"""Dump V12's own reasoning for a finished season, night by night.

The audit stores the raw THINK / PLAN completions alongside the rationale
summary. Reading them in order is the cheapest way to see what the agent
believed about the board versus what the engine actually did.
"""
from __future__ import annotations

import json
import sys
import textwrap
import urllib.request

BASE = "http://127.0.0.1:8000"


def main() -> None:
    gid = sys.argv[1]
    want_day = int(sys.argv[2]) if len(sys.argv) > 2 else None
    with urllib.request.urlopen(f"{BASE}/api/game/{gid}/agent-log", timeout=90) as r:
        rows = json.load(r).get("invocations") or []

    for inv in rows:
        day = inv.get("day")
        if want_day is not None and day != want_day:
            continue
        agent = str(inv.get("agent_id") or "")
        text = (inv.get("response_text") or "").strip()
        if not text:
            continue
        print("=" * 72)
        print(f"DAY {day}  —  {agent}  ({inv.get('ms_elapsed')}ms)")
        print("=" * 72)
        for para in text.split("\n"):
            print(textwrap.fill(para, 96) if para.strip() else "")
        print()


if __name__ == "__main__":
    main()
