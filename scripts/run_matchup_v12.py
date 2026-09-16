"""Competitive validation sweep for tabula_v12 — the benchmark to beat.

Headless seasons with p1 bound to tabula_v12 against the heuristic bots, so a
change to the harness can be scored instead of eyeballed. Per-seat agents are
bound via ``run_agent_turn(agent_label=...)``:

  * mode=heur   — p1 tabula_v12  vs  p2 RED_HARVEST                (performance floor)
  * mode=lite   — p1 tabula_v12  vs  p2 RED_HARVEST_LITE           (tutorial opponent)
  * mode=mirror — p1/p2/p3 all tabula_v12                          (self-collision spread)

Needs Cortex credentials (see docs/SNOWFLAKE_SETUP.md §1) — every V12 seat
calls the inference API each night. ``SOC_BACKEND=memory`` is fine; the
Snowflake backend only buys you durable replay rows.

Usage::

    SOC_BACKEND=memory python scripts/run_matchup_v12.py \
        --modes heur lite --seeds 69 2 56 --jobs 3
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# seat -> (agent_label or None for heuristic). None => heuristic seat.
_MODES: Dict[str, Dict[str, Optional[str]]] = {
    "heur": {"p1": "tabula_v12", "p2": None},
    "heur2": {"p1": "tabula_v12", "p2": None, "p3": None},
    "heur3": {"p1": "tabula_v12", "p2": None, "p3": None, "p4": None},
    "mirror": {"p1": "tabula_v12", "p2": "tabula_v12", "p3": "tabula_v12"},
    "lite": {"p1": "tabula_v12", "p2": "red_harvest_lite"},
}

_HERO = "tabula_v12"


def _run_one(mode: str, seed: int, *, days: Optional[int], tag: str) -> dict:
    from sea_of_colours.orchestrator_2.runtime import run_agent_turn
    from sea_of_colours.game.session import SEASON_DAY_CAP, Phase
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import engine as soc_engine

    soc_backend.reset_for_tests()
    store = soc_backend.get_store()

    seat_map = _MODES[mode]
    seats = list(seat_map.keys())
    agents = {s: ("cortex" if seat_map[s] else "red_harvest") for s in seats}
    season_name = f"V12_{mode.upper()}_{tag}_s{seed}"
    info = soc_engine.init_session(
        store, seed=seed, width=40, height=28,
        season_name=season_name, season_day_cap=days,
        players=seats, agents=agents,
    )
    session_id = info["session_id"]
    cap = int(info.get("season_day_cap") or SEASON_DAY_CAP)
    label = " | ".join(f"{s}={seat_map[s] or 'heuristic'}" for s in seats)
    print(f"\n=== {mode.upper()} | {season_name} | session={session_id} | "
          f"seed={seed} | cap={cap} | {label} ===", flush=True)

    started = time.time()
    green_lines: List[str] = []
    iterations = 0
    MAX_ITERS = 600
    while iterations < MAX_ITERS:
        status = soc_engine.get_session_status(store, session_id)
        if status.get("phase") == Phase.SEASON_COMPLETE.value:
            break
        pending = status.get("pending") or {}
        seat = next((s for s in seats if not pending.get(s, False)), None)
        if seat is None:
            soc_engine.run_night(store, session_id)
            iterations += 1
            continue
        pre = int((soc_engine.get_session_status(store, session_id) or {}).get("day", 0))
        override = "cortex" if seat_map[seat] else "heuristic"
        res = run_agent_turn(
            store, session_id, seat,
            runtime_override=override, agent_label=seat_map[seat],
        )
        post = soc_engine.get_session_status(store, session_id) or {}
        resolved = int(post.get("day", pre)) > pre
        if resolved:
            print(f"  [day {pre}] resolved "
                  f"({res.get('agent_id','?')} {int(res.get('ms_elapsed',0))}ms)",
                  flush=True)
            for r in store.list_log(session_id, day_from=pre, day_to=pre):
                t = str(r.get("text", ""))
                if "GREEN" in t and "auto-harvest" in t.lower():
                    green_lines.append(f"[d{pre}] {t}")
        iterations += 1

    view = soc_engine.get_view(store, session_id, seats[0])
    scores = {s: int((view.get("scores") or {}).get(s, 0)) for s in seats}
    top = max(scores.values()) if scores else 0
    winners = [s for s, sc in scores.items() if sc == top]
    elapsed = time.time() - started
    order = sorted(scores.items(), key=lambda kv: -kv[1])
    rank_of_hero = next(
        (i + 1 for i, (s, _) in enumerate(order) if seat_map[s] == _HERO),
        None,
    )
    print(f"  --> scores: " + ", ".join(
        f"{s}({seat_map[s] or 'heur'})={scores[s]}" for s in seats),
        flush=True)
    print(f"  --> winner: {','.join(winners)} | v12 rank={rank_of_hero} | "
          f"green_lines={len(green_lines)} | wall={elapsed:.1f}s", flush=True)
    for gl in green_lines:
        print(f"      GREEN! {gl}", flush=True)
    return {"mode": mode, "seed": seed, "scores": scores, "winners": winners,
            "v12_rank": rank_of_hero, "green": len(green_lines),
            "wall_s": round(elapsed, 1), "season": season_name,
            "seat_map": {s: (seat_map[s] or "heur") for s in seats}}


def _run_one_safe(mode: str, seed: int, *, days: Optional[int], tag: str) -> dict:
    """``_run_one`` wrapper that never raises — for the parallel pool."""
    try:
        return _run_one(mode, seed, days=days, tag=tag)
    except Exception as e:  # noqa: BLE001 — a failed season must not kill the pool
        import traceback
        print(f"  !! {mode} seed {seed} FAILED: {e}", flush=True)
        traceback.print_exc()
        return {"mode": mode, "seed": seed, "error": str(e)}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="run_matchup_v12")
    p.add_argument("--modes", nargs="*", default=["heur", "lite"],
                   choices=list(_MODES.keys()))
    p.add_argument("--seeds", type=int, nargs="*", default=[69, 2, 56])
    p.add_argument("--pairs", nargs="*", default=None,
                   help="explicit MODE:SEED tasks (overrides --modes x --seeds), "
                        "e.g. --pairs heur:69 lite:69 mirror:2")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--tag", default="VAL")
    p.add_argument("--jobs", type=int, default=1,
                   help="run this many seasons in parallel (separate processes)")
    args = p.parse_args(argv)

    # Memory, not snowflake: a benchmark run needs Cortex credentials but not
    # a deployed schema, and defaulting to snowflake made the first run fail on
    # a missing snowpark install rather than on anything to do with the agent.
    os.environ.setdefault("SOC_BACKEND", "memory")

    # Build the task list: explicit pairs win, else the modes x seeds cross-product.
    if args.pairs:
        tasks: List[tuple] = []
        for tok in args.pairs:
            mode, _, seed = tok.partition(":")
            if mode not in _MODES or not seed.isdigit():
                print(f"  !! bad --pairs token '{tok}' (want MODE:SEED)", flush=True)
                return 2
            tasks.append((mode, int(seed)))
    else:
        tasks = [(m, s) for m in args.modes for s in args.seeds]

    results = []
    if args.jobs > 1 and len(tasks) > 1:
        import concurrent.futures as _cf
        import multiprocessing as _mp
        ctx = _mp.get_context("spawn")  # snowpark connections are NOT fork-safe
        with _cf.ProcessPoolExecutor(max_workers=args.jobs, mp_context=ctx) as ex:
            futs = {
                ex.submit(_run_one_safe, m, s, days=args.days, tag=args.tag): (m, s)
                for (m, s) in tasks
            }
            for fut in _cf.as_completed(futs):
                results.append(fut.result())
    else:
        for (mode, seed) in tasks:
            results.append(_run_one_safe(mode, seed, days=args.days, tag=args.tag))

    print("\n==================== V12 VALIDATION SWEEP SUMMARY ====================",
          flush=True)
    for r in results:
        if "error" in r:
            print(f"  {r['mode']:>6} s{r['seed']:<3}: ERROR {r['error']}", flush=True)
        else:
            sc = ", ".join(
                f"{s}({r['seat_map'][s]})={r['scores'][s]}" for s in r["scores"]
            )
            print(f"  {r['mode']:>6} s{r['seed']:<3}: {sc} | winner="
                  f"{','.join(r['winners'])} v12_rank={r['v12_rank']} "
                  f"green={r['green']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
