#!/usr/bin/env python3
"""Offline N-seat heuristic battery runner for Sea of Colours.

Runs a matrix of full seasons headlessly with the **file** backend so
every season is watchable offline (point a ``SOC_BACKEND=file`` server at
the same ``--store-dir``). Unlike ``run_season.py`` (hard-wired to 2
seats), this driver generalises the turn loop to any seat count 1..4 so
we can sweep solo / duo / trio / quad seasons in one go.

All seats run the in-process ``RED_HARVEST`` heuristic. Cortex is NOT
supported here on purpose: cortex agents reach game state via the
Snowflake ``SOC_GET_VIEW`` stored procedure and cannot see a file/memory
session — those matches require ``--backend snowflake`` via run_season.py.

Canonical knobs are taken from the environment (set by the caller), e.g.::

    SOC_BACKEND=file SOC_STORE_DIR=./seasons_battery \\
    SOC_DROP_MODE=live_only SOC_PROBE_RADIUS=4 \\
    SOC_PROBE_LIFETIME_NIGHTS=3 SOC_AGENT_WORLD_VIEW=grid \\
    python scripts/run_battery.py --seats 1,2,3,4 --seeds 101,202,303

Each (seat_count, seed) pair mints one season. Bot seats get auto Latin
names + 3-letter tags + random palette colours (seeded by the game seed)
courtesy of ``GameSession.new()``.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

MAX_TURN_ITERATIONS = 600  # generous N-seat ceiling (4 seats × ~7 days)
_SEAT_IDS = ["p1", "p2", "p3", "p4"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_battery",
        description="Sweep offline heuristic seasons across seat counts/seeds.",
    )
    p.add_argument(
        "--seats",
        default="1,2,3,4",
        help="Comma list of seat counts to run (default: 1,2,3,4).",
    )
    p.add_argument(
        "--seeds",
        default="101,202",
        help="Comma list of game seeds per seat count (default: 101,202).",
    )
    p.add_argument("--width", type=int, default=40, help="Grid width (default 40).")
    p.add_argument("--height", type=int, default=28, help="Grid height (default 28).")
    p.add_argument(
        "--days",
        type=int,
        default=7,
        help="Planning days per season (default: 7 — canonical).",
    )
    p.add_argument(
        "--store-dir",
        default=None,
        help="File-backend dir (default: SOC_STORE_DIR env or ./seasons_battery).",
    )
    p.add_argument(
        "--wipe-first",
        action="store_true",
        help="Delete every prior session in the store before the sweep.",
    )
    return p


def _csv_ints(raw: str) -> List[int]:
    return [int(x) for x in str(raw).split(",") if x.strip()]


def _battery_season_name(seed: int, seat_count: int, run_tag: str) -> str:
    """Unique, human-readable season label for a battery season.

    Same seed used to collide across seat counts (and across battery runs),
    making replays impossible to tell apart. We (a) salt the evocative name
    generator with the run tag + seat count so the ``Latin_Noun`` pair itself
    differs, and (b) append a compact identifier ``run_tag·<n>p·s<seed>`` so
    the season is traceable back to the exact battery invocation.
    """
    from sea_of_colours.game.season_names import generate_season_name

    base = generate_season_name(seed, salt=f"{run_tag}:{seat_count}")
    return f"{base} · {run_tag}·{seat_count}p·s{seed}"


def _run_one_season(
    *,
    soc_engine,
    run_agent_turn,
    Phase,
    store,
    seed: int,
    seat_count: int,
    width: int,
    height: int,
    days: int,
    run_tag: str,
) -> Dict[str, Any]:
    """Drive a single season to SEASON_COMPLETE; return a summary dict."""
    players = _SEAT_IDS[:seat_count]
    agents = {seat: "red_harvest" for seat in players}
    info = soc_engine.init_session(
        store,
        seed=seed,
        width=width,
        height=height,
        season_day_cap=days,
        players=players,
        agents=agents,
        season_name=_battery_season_name(seed, seat_count, run_tag),
    )
    session_id = info["session_id"]
    season_name = info.get("season_name") or "(unnamed)"
    cap = int(info.get("season_day_cap") or days)

    # v0.9.19 — coalesce the file backend's per-turn full-table rewrites
    # (profiled at ~91% of wall time) into one flush per season. Reads
    # serve RAM during the batch so the simulation is unaffected. No-op for
    # stores that don't support batching (memory / snowflake).
    batched = (
        hasattr(store, "begin_batch")
        and hasattr(store, "flush_batch")
        and os.environ.get("SOC_BATCH_OFF") not in ("1", "true", "yes")
    )

    started = time.time()
    iteration = 0
    if batched:
        store.begin_batch()
    try:
        while iteration < MAX_TURN_ITERATIONS:
            status = soc_engine.get_session_status(store, session_id)
            if status.get("phase") == Phase.SEASON_COMPLETE.value:
                break
            pending = status.get("pending") or {}
            seat = next((s for s in players if not pending.get(s, False)), None)
            if seat is None:
                soc_engine.run_night(store, session_id)
                iteration += 1
                continue
            run_agent_turn(store, session_id, seat, runtime_override="heuristic")
            iteration += 1
    finally:
        if batched:
            store.flush_batch()

    final_view = soc_engine.get_view(store, session_id, players[0])
    scores = final_view.get("scores") or {}
    profiles = final_view.get("player_profiles") or {}
    days_played = max(0, int(final_view.get("day", 1)) - 1)
    return {
        "session_id": session_id,
        "season_name": season_name,
        "seed": seed,
        "seat_count": seat_count,
        "cap": cap,
        "days_played": days_played,
        "scores": {s: scores.get(s, 0) for s in players},
        "profiles": {s: profiles.get(s, {}) for s in players},
        "iterations": iteration,
        "wall_s": round(time.time() - started, 1),
        "completed": iteration < MAX_TURN_ITERATIONS,
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    # File backend is the offline / watchable default.
    os.environ.setdefault("SOC_BACKEND", "file")
    store_dir = args.store_dir or os.environ.get("SOC_STORE_DIR") or "./seasons_battery"
    os.environ["SOC_STORE_DIR"] = store_dir

    from sea_of_colours.agent.runtime import run_agent_turn
    from sea_of_colours.game.session import Phase
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import engine as soc_engine

    soc_backend.reset_for_tests()
    store = soc_backend.get_store()

    if args.wipe_first:
        print(f"--wipe-first: clearing all sessions in {store_dir}", flush=True)
        store.wipe_all_sessions()

    seat_counts = _csv_ints(args.seats)
    seeds = _csv_ints(args.seeds)

    # One compact, unique tag per battery invocation (``MMDD-HHMM-XX``) so
    # every season this run mints carries a shared, traceable identifier and
    # no two batteries collide even within the same minute.
    run_tag = f"{time.strftime('%m%d-%H%M')}-{os.urandom(1).hex()}"

    line = "=" * 78
    print(line, flush=True)
    print("  SEA OF COLOURS — offline heuristic battery", flush=True)
    print(f"  run_tag={run_tag}", flush=True)
    print(f"  backend={os.environ['SOC_BACKEND']}  store_dir={store_dir}", flush=True)
    print(f"  drop={os.environ.get('SOC_DROP_MODE', '(default)')}  "
          f"probe_r={os.environ.get('SOC_PROBE_RADIUS', '(default)')}  "
          f"probe_life={os.environ.get('SOC_PROBE_LIFETIME_NIGHTS', '(default)')}  "
          f"world_view={os.environ.get('SOC_AGENT_WORLD_VIEW', '(default)')}", flush=True)
    print(f"  seat_counts={seat_counts}  seeds={seeds}  days={args.days}", flush=True)
    print(line, flush=True)

    results: List[Dict[str, Any]] = []
    for seat_count in seat_counts:
        for seed in seeds:
            r = _run_one_season(
                soc_engine=soc_engine,
                run_agent_turn=run_agent_turn,
                Phase=Phase,
                store=store,
                seed=seed,
                seat_count=seat_count,
                width=args.width,
                height=args.height,
                days=args.days,
                run_tag=run_tag,
            )
            results.append(r)
            score_str = "  ".join(
                f"{s}={int(r['scores'].get(s, 0))}"
                f"({(r['profiles'].get(s) or {}).get('tag', '???')}"
                f"/{(r['profiles'].get(s) or {}).get('color', '?')})"
                for s in _SEAT_IDS[:seat_count]
            )
            flag = "" if r["completed"] else "  [ABORTED]"
            print(
                f"[{seat_count}p seed={seed:<6}] {r['season_name']:<28} "
                f"d{r['days_played']}/{r['cap']}  {score_str}  "
                f"{r['wall_s']}s{flag}",
                flush=True,
            )

    print(line, flush=True)
    print(f"  battery complete — {len(results)} seasons in {store_dir}", flush=True)
    print(line, flush=True)
    aborted = [r for r in results if not r["completed"]]
    return 3 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
