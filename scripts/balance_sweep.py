#!/usr/bin/env python3
"""Offline balance lab — run many RED_HARVEST vs RED_HARVEST seasons and
fold them into per-season + aggregate metrics, fully in-memory (no
Snowflake). This is the measurement rig the vision rework is tuned
against: run it once on the current rules to establish a baseline, then
re-run with rule flags toggled and diff the numbers.

The sweep stays metrics-only (fast, in RAM). To eyeball a specific game,
``--persist-seeds`` re-runs those seeds through the file-backed store so
they open in the watcher (``SOC_BACKEND=file`` server on the same dir).

Examples::

    # Baseline: 30 seasons on the current ruleset -> reports/sweep_baseline.csv
    python scripts/balance_sweep.py --label baseline --n 30

    # Same, but also persist two games to ./seasons for /watch inspection
    python scripts/balance_sweep.py --label baseline --n 30 \\
        --persist-seeds 1000,1007 --store-dir ./seasons

    # A rule variant (Phase 2/3): env knobs applied to every season
    python scripts/balance_sweep.py --label live_only \\
        --set SOC_DROP_MODE=live_only --set SOC_PROBE_RADIUS=4 --n 30
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

MAX_TURN_ITERATIONS = 80


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="balance_sweep",
        description="Run N heuristic seasons and emit balance metrics (CSV).",
    )
    p.add_argument("--label", default="baseline",
                   help="Config label; names the output CSV + metrics rows.")
    p.add_argument("--n", type=int, default=20,
                   help="Number of seasons/seeds to run (default 20).")
    p.add_argument("--seed-base", type=int, default=1000,
                   help="First seed; seeds run seed-base .. seed-base+n-1.")
    p.add_argument("--seeds", default=None,
                   help="Explicit comma-separated seed list (overrides --n/--seed-base).")
    p.add_argument("--width", type=int, default=40)
    p.add_argument("--height", type=int, default=28)
    p.add_argument("--days", type=int, default=None,
                   help="Planning days per season (default: engine default).")
    p.add_argument("--p1", choices=("heuristic",), default="heuristic")
    p.add_argument("--p2", choices=("heuristic",), default="heuristic")
    p.add_argument("--set", dest="env_sets", action="append", default=[],
                   metavar="KEY=VAL",
                   help="Env override applied to every season (repeatable).")
    p.add_argument("--persist-seeds", default=None,
                   help="Comma-separated seeds to ALSO persist to --store-dir "
                        "(file backend) so they're watchable in /watch.")
    p.add_argument("--store-dir", default="./seasons",
                   help="Directory for persisted sample seasons (file backend).")
    p.add_argument("--out", default=None,
                   help="CSV output path (default reports/sweep_<label>.csv).")
    p.add_argument("-q", "--quiet", action="store_true")
    return p


def _resolve_seeds(args: argparse.Namespace) -> List[int]:
    if args.seeds:
        return [int(s) for s in args.seeds.split(",") if s.strip()]
    return [args.seed_base + i for i in range(args.n)]


def _apply_env(env_sets: List[str]) -> Dict[str, str]:
    applied: Dict[str, str] = {}
    for kv in env_sets:
        if "=" not in kv:
            raise SystemExit(f"--set expects KEY=VAL, got: {kv!r}")
        k, v = kv.split("=", 1)
        os.environ[k.strip()] = v.strip()
        applied[k.strip()] = v.strip()
    return applied


def run_one_season(
    store: Any,
    *,
    seed: int,
    width: int,
    height: int,
    days: Optional[int],
    p1: str = "heuristic",
    p2: str = "heuristic",
) -> str:
    """Drive a full heuristic season to completion; return session_id.

    Mirrors scripts/run_season.py's resolution loop but headless/quiet.
    """
    from sea_of_colours.agent.runtime import run_agent_turn
    from sea_of_colours.game.session import Phase
    from sea_of_colours.snowpark import engine as soc_engine

    info = soc_engine.init_session(
        store, seed=seed, width=width, height=height, season_day_cap=days,
    )
    sid = info["session_id"]
    runtime_for = {"p1": p1, "p2": p2}

    for _ in range(MAX_TURN_ITERATIONS):
        status = soc_engine.get_session_status(store, sid)
        if status.get("phase") == Phase.SEASON_COMPLETE.value:
            break
        pending = status.get("pending") or {}
        seat = "p1" if not pending.get("p1", False) else (
            "p2" if not pending.get("p2", False) else None
        )
        if seat is None:
            soc_engine.run_night(store, sid)
            continue
        run_agent_turn(store, sid, seat, runtime_override=runtime_for[seat])
    return sid


def _aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mean/median over numeric metric columns + p1 win rate."""
    if not rows:
        return {}
    from sea_of_colours.evals.season_metrics import METRIC_COLUMNS

    summary: Dict[str, Any] = {"seasons": len(rows)}
    skip = {"config", "seed", "session_id", "season_name", "winner"}
    for col in METRIC_COLUMNS:
        if col in skip:
            continue
        vals = []
        for r in rows:
            v = r.get(col, "")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        if not vals:
            continue
        summary[f"{col}__mean"] = round(statistics.fmean(vals), 3)
        summary[f"{col}__median"] = round(statistics.median(vals), 3)
    winners = [r.get("winner") for r in rows]
    summary["p1_win_rate"] = round(winners.count("p1") / len(rows), 3)
    summary["p2_win_rate"] = round(winners.count("p2") / len(rows), 3)
    summary["tie_rate"] = round(winners.count("tie") / len(rows), 3)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    applied_env = _apply_env(args.env_sets)
    seeds = _resolve_seeds(args)
    persist = set(
        int(s) for s in (args.persist_seeds or "").split(",") if s.strip()
    )

    # Imported AFTER env is applied so any import-time knobs honour --set.
    from sea_of_colours.snowpark.store import InMemorySocStore
    from sea_of_colours.evals.season_metrics import (
        METRIC_COLUMNS,
        compute_season_metrics,
    )

    if not args.quiet:
        print(f"[sweep] label={args.label} seasons={len(seeds)} "
              f"grid={args.width}x{args.height} env={applied_env or '{}'}",
              flush=True)

    rows: List[Dict[str, Any]] = []
    t0 = time.time()
    for i, seed in enumerate(seeds):
        store = InMemorySocStore()
        sid = run_one_season(
            store, seed=seed, width=args.width, height=args.height,
            days=args.days, p1=args.p1, p2=args.p2,
        )
        m = compute_season_metrics(store, sid)
        m["config"] = args.label
        m["seed"] = seed
        rows.append(m)

        if seed in persist:
            from sea_of_colours.snowpark.file_store import FileSocStore
            fstore = FileSocStore(args.store_dir)
            fsid = run_one_season(
                fstore, seed=seed, width=args.width, height=args.height,
                days=args.days, p1=args.p1, p2=args.p2,
            )
            if not args.quiet:
                print(f"[sweep] persisted seed {seed} -> {args.store_dir} "
                      f"(session {fsid[:8]}) — watchable in /watch", flush=True)

        if not args.quiet:
            print(f"  [{i+1}/{len(seeds)}] seed={seed} "
                  f"margin={m['margin_abs']} winner={m['winner']} "
                  f"denial={m['denial_events']} min_gap={m['min_harv_gap']} "
                  f"contact_r2={m['contact_frac_r2']}", flush=True)

    # ── write per-season CSV ──────────────────────────────────────
    out_path = Path(args.out) if args.out else (
        _REPO_ROOT / "reports" / f"sweep_{args.label}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=METRIC_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    summary = _aggregate(rows)
    summary_path = out_path.with_name(out_path.stem + "_summary.csv")
    with summary_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["metric", "value"])
        for k in sorted(summary):
            writer.writerow([k, summary[k]])

    elapsed = time.time() - t0
    if not args.quiet:
        print(f"\n[sweep] wrote {out_path} ({len(rows)} rows) in {elapsed:.1f}s")
        print(f"[sweep] wrote {summary_path}")
        print("\n── aggregate (north-star highlighted) ──")
        for key in (
            "p1_win_rate", "margin_abs__median",
            "harvested_total__mean", "harvested_per_night__mean",
            "denial_events__mean", "probe_supersedes__mean",
            "probe_collisions__mean",
            "min_harv_gap__median", "mean_harv_gap__mean",
            "contact_frac_r2__mean", "contact_frac_r4__mean",
            "explored_frac_union__mean",
            "probes_built__mean", "probe_avg_lifetime__mean",
        ):
            if key in summary:
                print(f"  {key:32s} {summary[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
