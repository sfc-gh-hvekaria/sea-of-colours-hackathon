#!/usr/bin/env python3
"""Deep per-day dump for ONE seat: full THINK/THINKER/mover text + the ENGINE
LOG lines (ground truth of what actually executed) + the registry cells the
plan IDs resolved to. Read-only."""
from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _txt(r, *keys):
    for k in keys:
        v = r.get(k)
        if v:
            return v
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--seat", default="p1")
    ap.add_argument("--days", default="3,4,5,6,7")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    days = [int(d) for d in args.days.split(",")]

    os.environ.setdefault("SOC_BACKEND", "snowflake")
    from sea_of_colours.snowpark import backend as soc_backend
    store = soc_backend.get_store()
    sid = args.session

    invs = store.list_agent_invocations(sid)
    log = store.list_log(sid, day_from=0, day_to=max(days) + 1)
    if invs:
        print("INVOCATION COLUMNS:", sorted(invs[0].keys()))

    by_day_inv = {}
    for r in invs:
        if str(r.get("player") or "") != args.seat:
            continue
        by_day_inv.setdefault(int(r.get("day", 0)), []).append(r)
    by_day_log = {}
    for r in log:
        by_day_log.setdefault(int(r.get("day", 0)), []).append(r)

    out = []
    def emit(s=""):
        out.append(s); print(s, flush=True)

    seat = args.seat
    for day in days:
        emit("\n" + "=" * 80)
        emit(f"  DAY {day}  seat={seat}")
        emit("=" * 80)
        # engine log lines that mention this seat OR are redsign/global events
        emit("\n--- ENGINE LOG (executed truth) ---")
        for l in by_day_log.get(day, []):
            t = (l.get("text") or "").strip()
            if not t:
                continue
            if (seat in t) or re.search(r"red\s*sign|EMP|chaff|crush|supersede|collision|catapult|shipped", t, re.I):
                emit(f"  [{l.get('level','')}] {t}")
        # agent invocations
        for r in by_day_inv.get(day, []):
            aid = str(r.get("agent_id") or "")
            resp = _txt(r, "response_text", "response", "output_text")
            ms = r.get("ms_elapsed") or r.get("latency_ms") or "?"
            emit(f"\n--- {aid}  ({ms}ms) ---")
            if aid.endswith("_THINK"):
                emit(resp[:2600])
            elif aid.endswith("_THINKER"):
                emit(resp[:3000])
            else:
                # harness/mover: show moves + any extras
                emit(resp[:2600])

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(out), encoding="utf-8")
        print(f"\n  report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
