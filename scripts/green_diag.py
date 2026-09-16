#!/usr/bin/env python3
"""Diagnose green flow: harvested vs jettisoned vs held, and whether the
green (solar-jettison) catapult is actually awarding slots.

Runs a few 4-player RED_HARVEST seasons and, per season, reports each
seat's green harvest/disposal balance plus a dump of every green
catapult resolution (submitted? slots awarded? fuel committed vs spent?)
pulled straight from ``sess.catapult_history``.

Usage::  python scripts/green_diag.py --n 4 --days 7 --config echo_drop_r3
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

KNOBS = ("SOC_DROP_MODE", "SOC_PROBE_RADIUS", "SOC_PROBE_LIFETIME_NIGHTS")
CONFIG_ENV: Dict[str, Dict[str, str]] = {
    "live_drop_r4": {"SOC_DROP_MODE": "live_only", "SOC_PROBE_RADIUS": "4"},
    "echo_drop_r3": {"SOC_PROBE_RADIUS": "3"},
}
PLAYERS = ["p1", "p2", "p3", "p4"]
MAX_ITERS = 600


def _apply_env(env: Dict[str, str]) -> None:
    for k in KNOBS:
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v


def _run(store: Any, *, seed: int, days: int) -> str:
    from sea_of_colours.agent.runtime import run_agent_turn
    from sea_of_colours.game.session import Phase
    from sea_of_colours.snowpark import engine as soc_engine

    info = soc_engine.init_session(
        store, seed=seed, width=40, height=28, season_day_cap=days,
        players=list(PLAYERS), agents={p: "red_harvest" for p in PLAYERS},
    )
    sid = info["session_id"]
    for _ in range(MAX_ITERS):
        st = soc_engine.get_session_status(store, sid)
        if st.get("phase") == Phase.SEASON_COMPLETE.value:
            break
        pending = st.get("pending") or {}
        seat = next((p for p in PLAYERS if not pending.get(p)), None)
        if seat is None:
            soc_engine.run_night(store, sid)
            continue
        run_agent_turn(store, sid, seat, runtime_override="heuristic")
    return sid


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="green_diag")
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--seed-base", type=int, default=4000)
    ap.add_argument("--config", default="echo_drop_r3", choices=list(CONFIG_ENV))
    args = ap.parse_args(argv)

    from sea_of_colours.snowpark.store import InMemorySocStore
    from sea_of_colours.snowpark import engine as soc_engine

    _apply_env(CONFIG_ENV[args.config])
    print(f"=== green diagnostic · config={args.config} "
          f"env={CONFIG_ENV[args.config]} ===\n")

    tot_harv = tot_jett = tot_held = 0
    tot_submits = tot_awarded = tot_fuel_committed = tot_fuel_spent = 0

    for i in range(args.n):
        seed = args.seed_base + i
        store = InMemorySocStore()
        sid = _run(store, seed=seed, days=args.days)
        summary = soc_engine.get_endgame_summary(store, sid)
        sess = soc_engine._hydrate_session(store, sid)

        print(f"-- seed {seed} --")
        for p in summary.get("players") or []:
            seat = p["seat"]
            harv = int(p.get("green_harvested", 0))
            jett = int(p.get("green_jettisoned", 0))
            held = int(p.get("green_held", 0))
            tot_harv += harv
            tot_jett += jett
            tot_held += held
            print(f"   {seat}: green harvested={harv:3d}  "
                  f"jettisoned={jett:3d}  held@end={held:3d}  "
                  f"(penalty -{held * 100})")

        # Per-orbit green catapult resolution detail.
        for entry in sess.catapult_history or []:
            jett_block = entry.get("jettison") or {}
            seats = jett_block.get("seats") or {}
            day = entry.get("day")
            submitted = {
                s: d for s, d in seats.items()
                if (d or {}).get("submitted")
            }
            if not submitted:
                continue
            slots_used = jett_block.get("slots_used", 0)
            slots_total = jett_block.get("slots_total", 0)
            parts = []
            for s, d in submitted.items():
                tot_submits += 1
                awarded = int(d.get("awarded", 0) or 0)
                budget = int(d.get("budget", 0) or 0)
                committed = int(d.get("green_committed", 0) or 0)
                spent = int(d.get("fuel_spent", 0) or 0)
                tot_awarded += awarded
                tot_fuel_committed += budget
                tot_fuel_spent += spent
                parts.append(
                    f"{s}[want {committed} → won {awarded}, "
                    f"fuel {spent}/{budget}]"
                )
            print(f"     day {day} catapult slots {slots_used}/{slots_total}: "
                  + "  ".join(parts))
        print()

    print("=== totals ===")
    print(f"  green harvested : {tot_harv}")
    print(f"  green jettisoned: {tot_jett}  "
          f"({(100 * tot_jett / tot_harv) if tot_harv else 0:.0f}% of harvested)")
    print(f"  green held @ end: {tot_held}  (penalty -{tot_held * 100})")
    print(f"  catapult submissions: {tot_submits}  slots won: {tot_awarded}")
    print(f"  red fuel committed: {tot_fuel_committed}  spent: {tot_fuel_spent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
