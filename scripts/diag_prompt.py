#!/usr/bin/env python3
"""Dump the PROMPT text an agent was fed for a given day/agent-id substring."""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--seat", default="p1")
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--aid", default="_THINK")  # substring match on agent_id
    ap.add_argument("--grep", default="")       # optional: only show around matches
    args = ap.parse_args()
    os.environ.setdefault("SOC_BACKEND", "snowflake")
    from sea_of_colours.snowpark import backend as soc_backend
    store = soc_backend.get_store()
    invs = store.list_agent_invocations(args.session, day=args.day)
    for r in invs:
        if str(r.get("player") or "") != args.seat:
            continue
        aid = str(r.get("agent_id") or "")
        if args.aid not in aid:
            continue
        print("COLUMNS:", sorted(r.keys()))
        prompt = ""
        for k in ("prompt_text", "prompt", "prompt_excerpt", "input_text", "request_text"):
            if r.get(k):
                prompt = r[k]; print(f"[prompt key = {k}, len={len(prompt)}]"); break
        print("=" * 80)
        print(f"  {aid}  day {args.day}  seat {args.seat}")
        print("=" * 80)
        if args.grep:
            import re
            for m in re.finditer(re.escape(args.grep), prompt, re.I):
                s = max(0, m.start() - 400); e = min(len(prompt), m.end() + 800)
                print(f"\n... [match @ {m.start()}] ...\n" + prompt[s:e])
        else:
            print(prompt)
        print("\n" + "#" * 80 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
