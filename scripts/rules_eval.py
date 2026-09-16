#!/usr/bin/env python3
"""Rules-evaluation driver for the end-of-game review.

Runs RED_HARVEST vs RED_HARVEST seasons across the candidate rule
configurations (current defaults vs the vision-rework knobs in
:mod:`sea_of_colours.game.tuning`), emitting:

  * per-config metric CSVs + summaries under ``reports/`` (reusing the
    balance-lab metrics), and
  * a handful of fully-played, config-labelled seasons persisted to the
    local file store (``./seasons``) so they open in /watch with the new
    end-of-game results screen for side-by-side eyeballing.

Each config flips the env knobs in-process (they're read at call time),
so a single run produces a comparable matrix. Metrics use an in-memory
store; the watchable samples use the file store the server reads.

Usage::

    python scripts/rules_eval.py --n 20 --days 7 --persist 2
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The three vision-rework knobs we sweep. Cleared before each config so a
# prior config's env never leaks forward.
KNOBS = ("SOC_DROP_MODE", "SOC_PROBE_RADIUS", "SOC_PROBE_LIFETIME_NIGHTS")

# (label, env overrides). ``defaults`` is the current canonical ruleset.
CONFIGS: List[tuple[str, Dict[str, str]]] = [
    ("defaults", {}),
    ("live_only", {"SOC_DROP_MODE": "live_only"}),
    ("radius4", {"SOC_PROBE_RADIUS": "4"}),
    ("live_only_r4", {"SOC_DROP_MODE": "live_only", "SOC_PROBE_RADIUS": "4"}),
    ("probe_life3", {"SOC_PROBE_LIFETIME_NIGHTS": "3"}),
    ("live_only_r4_life3", {
        "SOC_DROP_MODE": "live_only",
        "SOC_PROBE_RADIUS": "4",
        "SOC_PROBE_LIFETIME_NIGHTS": "3",
    }),
]

MAX_TURN_ITERATIONS = 120


def _apply_config_env(env: Dict[str, str]) -> None:
    for k in KNOBS:
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v


def _run_season(store: Any, *, seed: int, width: int, height: int,
                days: int, season_name: Optional[str] = None) -> str:
    from sea_of_colours.agent.runtime import run_agent_turn
    from sea_of_colours.game.session import Phase
    from sea_of_colours.snowpark import engine as soc_engine

    info = soc_engine.init_session(
        store, seed=seed, width=width, height=height,
        season_day_cap=days, season_name=season_name,
    )
    sid = info["session_id"]
    for _ in range(MAX_TURN_ITERATIONS):
        status = soc_engine.get_session_status(store, sid)
        if status.get("phase") == Phase.SEASON_COMPLETE.value:
            break
        pending = status.get("pending") or {}
        seat = "p1" if not pending.get("p1") else (
            "p2" if not pending.get("p2") else None)
        if seat is None:
            soc_engine.run_night(store, sid)
            continue
        run_agent_turn(store, sid, seat, runtime_override="heuristic")
    return sid


def _aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    from sea_of_colours.evals.season_metrics import METRIC_COLUMNS
    summary: Dict[str, Any] = {"seasons": len(rows)}
    skip = {"config", "seed", "session_id", "season_name", "winner"}
    for col in METRIC_COLUMNS:
        if col in skip:
            continue
        vals = [float(r[col]) for r in rows
                if isinstance(r.get(col), (int, float)) and not isinstance(r.get(col), bool)]
        if vals:
            summary[f"{col}__mean"] = round(statistics.fmean(vals), 3)
            summary[f"{col}__median"] = round(statistics.median(vals), 3)
    winners = [r.get("winner") for r in rows]
    n = max(1, len(rows))
    summary["p1_win_rate"] = round(winners.count("p1") / n, 3)
    summary["tie_rate"] = round(winners.count("tie") / n, 3)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="rules_eval")
    ap.add_argument("--n", type=int, default=20, help="Seasons per config.")
    ap.add_argument("--days", type=int, default=7, help="Nights per season.")
    ap.add_argument("--seed-base", type=int, default=2000)
    ap.add_argument("--width", type=int, default=40)
    ap.add_argument("--height", type=int, default=28)
    ap.add_argument("--persist", type=int, default=2,
                    help="Watchable sample seasons to persist per config.")
    ap.add_argument("--store-dir", default="./seasons")
    args = ap.parse_args(argv)

    from sea_of_colours.snowpark.store import InMemorySocStore
    from sea_of_colours.snowpark.file_store import FileSocStore
    from sea_of_colours.evals.season_metrics import (
        METRIC_COLUMNS, compute_season_metrics,
    )

    seeds = [args.seed_base + i for i in range(args.n)]
    reports = _REPO_ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    all_summaries: Dict[str, Dict[str, Any]] = {}
    t0 = time.time()

    for label, env in CONFIGS:
        _apply_config_env(env)
        print(f"\n[rules_eval] === {label} === env={env or '{}'}", flush=True)
        rows: List[Dict[str, Any]] = []
        for i, seed in enumerate(seeds):
            store = InMemorySocStore()
            sid = _run_season(store, seed=seed, width=args.width,
                              height=args.height, days=args.days)
            m = compute_season_metrics(store, sid)
            m["config"] = label
            m["seed"] = seed
            rows.append(m)
        # Persist a few watchable, config-labelled seasons to ./seasons.
        fstore = FileSocStore(args.store_dir)
        for seed in seeds[:max(0, args.persist)]:
            name = f"{label}__seed{seed}"
            _run_season(fstore, seed=seed, width=args.width,
                       height=args.height, days=args.days, season_name=name)
            print(f"  persisted watchable season: {name}", flush=True)

        out = reports / f"rules_{label}.csv"
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=METRIC_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        summ = _aggregate(rows)
        all_summaries[label] = summ
        with out.with_name(out.stem + "_summary.csv").open(
                "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["metric", "value"])
            for k in sorted(summ):
                w.writerow([k, summ[k]])
        print(f"  wrote {out.name} (+summary)", flush=True)

    # Comparison matrix across configs for the north-star metrics.
    compare_keys = [
        "p1_win_rate", "tie_rate",
        "margin_abs__median", "harvested_total__mean",
        "harvested_per_night__mean", "denial_events__mean",
        "min_harv_gap__median", "contact_frac_r2__mean",
        "explored_frac_union__mean", "probes_built__mean",
    ]
    comp = reports / "rules_eval_compare.csv"
    with comp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["metric"] + [c for c, _ in CONFIGS])
        for key in compare_keys:
            w.writerow([key] + [all_summaries[c].get(key, "") for c, _ in CONFIGS])
    print(f"\n[rules_eval] wrote {comp} in {time.time() - t0:.1f}s")
    print("\n── config comparison (north-star) ──")
    hdr = "metric".ljust(30) + "".join(c[:14].rjust(15) for c, _ in CONFIGS)
    print(hdr)
    for key in compare_keys:
        line = key.ljust(30)
        for c, _ in CONFIGS:
            line += str(all_summaries[c].get(key, "")).rjust(15)
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
