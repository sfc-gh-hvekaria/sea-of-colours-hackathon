#!/usr/bin/env python3
"""Read-only post-mortem for an ALREADY-RUN session.

Pulls SOC_GAME_LOG + SOC_AGENT_INVOCATION for a session_id and prints a concise
per-day diagnosis: score movement, redsign mints (with discoverer), probe /
harvester collisions + crushes, and — for each seat on a redsign night — the
THINKER's selected plan IDs and the MOVER's drop/probe cells, so we can see
whether the CASE-2 poker actually reached the pure or whiffed.

Usage::
    SOC_BACKEND=snowflake PYTHONPATH=.:scripts python scripts/diag_session.py \
        --session <id> [--label NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Log-text keywords that matter for the redsign / collision post-mortem.
_KEY_PATTERNS = [
    ("REDSIGN", re.compile(r"red\s*sign", re.I)),
    ("PROBECOLL", re.compile(r"probe collision|collision crater", re.I)),
    ("SUPERSEDE", re.compile(r"supersede", re.I)),
    ("CRUSH", re.compile(r"crush", re.I)),
    ("HARVCOLL", re.compile(r"harvester.*(collision|collide)|collision.*harvester", re.I)),
    ("CRASH", re.compile(r"crash", re.I)),
    ("EMP", re.compile(r"\bemp\b", re.I)),
    ("CHAFF", re.compile(r"chaff", re.I)),
    ("SHIP", re.compile(r"catapult|shipped|vault", re.I)),
]

_CELL_RE = re.compile(r"\[?\(?(\d{1,2})\s*,\s*(\d{1,2})\)?\]?")


def _classify(text: str) -> Optional[str]:
    for tag, pat in _KEY_PATTERNS:
        if pat.search(text or ""):
            return tag
    return None


def _extract_moves(resp: str) -> List[Dict[str, Any]]:
    if not resp:
        return []
    try:
        obj = json.loads(resp)
    except Exception:
        m = re.search(r"\{.*\}", resp, re.S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return []
    mv = obj.get("moves") if isinstance(obj, dict) else None
    return mv if isinstance(mv, list) else []


def _summarise_moves(moves: List[Dict[str, Any]]) -> str:
    drops, probes, steps, picks = [], [], 0, 0
    for m in moves:
        if not isinstance(m, dict):
            continue
        a = m.get("a")
        if a == "drop":
            at = m.get("at")
            drops.append(f"{m.get('unit','?')}@{tuple(at) if at else '?'}")
        elif a == "probe":
            at = m.get("at")
            probes.append(f"{tuple(at) if at else '?'}")
        elif a == "step":
            steps += 1
        elif a == "pickup":
            picks += 1
    bits = []
    if drops:
        bits.append("drops[" + "; ".join(drops) + "]")
    if probes:
        bits.append("probes[" + "; ".join(probes) + "]")
    bits.append(f"{steps} steps, {picks} pickups")
    return " ".join(bits)


def _plan_ids(resp: str) -> str:
    if not resp:
        return ""
    try:
        obj = json.loads(resp)
    except Exception:
        m = re.search(r"\{.*\}", resp, re.S)
        obj = json.loads(m.group(0)) if m else {}
    if not isinstance(obj, dict):
        return ""
    posture = obj.get("posture") or ""
    plan = obj.get("plan") or []
    sit = obj.get("situational") or {}
    mine = sit.get("mine") if isinstance(sit, dict) else None
    tail = f" mine={mine}" if mine is not None else ""
    return f"posture={posture} plan={plan}{tail}"


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="diag_session")
    p.add_argument("--session", required=True)
    p.add_argument("--label", default="")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    os.environ.setdefault("SOC_BACKEND", "snowflake")
    from sea_of_colours.snowpark import backend as soc_backend

    store = soc_backend.get_store()
    sid = args.session

    log = store.list_log(sid, day_from=0, day_to=args.days + 1)
    invs = store.list_agent_invocations(sid)

    out: List[str] = []

    def emit(s: str = "") -> None:
        out.append(s)
        print(s, flush=True)

    emit("=" * 78)
    emit(f"  DIAG — {args.label or sid}")
    emit(f"  session: {sid}")
    emit("=" * 78)

    # ── per-day log highlights ────────────────────────────────────────
    by_day_log: Dict[int, List[Dict[str, Any]]] = {}
    for r in log:
        by_day_log.setdefault(int(r.get("day", 0)), []).append(r)

    # ── per-day invocations grouped by seat ───────────────────────────
    by_day_inv: Dict[int, List[Dict[str, Any]]] = {}
    for r in invs:
        by_day_inv.setdefault(int(r.get("day", 0)), []).append(r)

    for day in range(0, args.days + 1):
        logs = by_day_log.get(day, [])
        keyed = [(l, _classify(l.get("text", ""))) for l in logs]
        keyed = [(l, t) for (l, t) in keyed if t]
        dinv = by_day_inv.get(day, [])
        if not keyed and not dinv:
            continue
        emit(f"\n── DAY {day} " + "─" * 60)
        # Log highlights (deduped by tag+text).
        seen = set()
        for l, tag in keyed:
            txt = (l.get("text") or "").strip()
            k = (tag, txt[:80])
            if k in seen:
                continue
            seen.add(k)
            emit(f"  [{tag}] {txt[:160]}")
        # Per-seat plan + moves (THINKER decision + MOVER moves).
        seats: Dict[str, Dict[str, Any]] = {}
        for r in dinv:
            aid = str(r.get("agent_id") or "")
            seat = str(r.get("player") or "")
            seats.setdefault(seat, {})
            resp = r.get("response_text") or ""
            if aid.endswith("_THINKER"):
                seats[seat]["plan"] = _plan_ids(resp)
                seats[seat]["plan_ms"] = r.get("ms_elapsed")
            elif aid.endswith("_THINK"):
                seats[seat]["think_ms"] = r.get("ms_elapsed")
            elif aid.endswith("_THINKER") is False and "moves" in (resp or ""):
                seats[seat]["moves"] = _summarise_moves(_extract_moves(resp))
                seats[seat]["mover_ms"] = r.get("ms_elapsed")
        for seat in sorted(seats):
            s = seats[seat]
            plan = s.get("plan") or "(no plan)"
            moves = s.get("moves") or "(no moves)"
            emit(
                f"    {seat}: {plan}"
                f"  | think {s.get('think_ms','?')}ms plan {s.get('plan_ms','?')}ms "
                f"mover {s.get('mover_ms','?')}ms"
            )
            emit(f"        MOVES: {moves}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(out), encoding="utf-8")
        print(f"\n  report: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
