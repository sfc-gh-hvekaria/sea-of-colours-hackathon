#!/usr/bin/env python3
"""4-player score evaluation across two candidate rulesets.

Runs RED_HARVEST × 4 seasons under two vision/drop configs and prints
the FINAL SHIPPING SCORES with deductions (the canonical
``score_for`` breakdown: shipped − green-penalty − vault-red-loss),
exactly as the end-of-game results screen reports them.

Configs (the two the review is comparing):

  * ``live_drop_r4``  — live-only drops, probe vision radius 4.
  * ``echo_drop_r3``  — live-or-echo drops (current rules), radius 3.

Usage::

    python scripts/score_eval.py --n 20 --days 7
    python scripts/score_eval.py --n 8 --days 7 --persist 2
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

# Vision-rework knobs cleared before each config so env never leaks.
KNOBS = ("SOC_DROP_MODE", "SOC_PROBE_RADIUS", "SOC_PROBE_LIFETIME_NIGHTS")

CONFIGS: List[tuple[str, Dict[str, str]]] = [
    ("live_drop_r4", {"SOC_DROP_MODE": "live_only", "SOC_PROBE_RADIUS": "4"}),
    ("echo_drop_r3", {"SOC_PROBE_RADIUS": "3"}),  # live_or_echo is the default
]

PLAYERS = ["p1", "p2", "p3", "p4"]
MAX_TURN_ITERATIONS = 600


def _apply_config_env(env: Dict[str, str]) -> None:
    for k in KNOBS:
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v


def _run_4p_season(
    store: Any, *, seed: int, width: int, height: int, days: int,
    season_name: Optional[str] = None,
) -> str:
    """Drive a full 4-seat RED_HARVEST season to SEASON_COMPLETE."""
    from sea_of_colours.agent.runtime import run_agent_turn
    from sea_of_colours.game.session import Phase
    from sea_of_colours.snowpark import engine as soc_engine

    info = soc_engine.init_session(
        store, seed=seed, width=width, height=height,
        season_day_cap=days, season_name=season_name,
        players=list(PLAYERS),
        agents={p: "red_harvest" for p in PLAYERS},
    )
    sid = info["session_id"]
    for _ in range(MAX_TURN_ITERATIONS):
        status = soc_engine.get_session_status(store, sid)
        if status.get("phase") == Phase.SEASON_COMPLETE.value:
            break
        pending = status.get("pending") or {}
        seat = next((p for p in PLAYERS if not pending.get(p)), None)
        if seat is None:
            soc_engine.run_night(store, sid)
            continue
        run_agent_turn(store, sid, seat, runtime_override="heuristic")
    return sid


def _season_scores(store: Any, sid: str) -> List[Dict[str, Any]]:
    """Per-seat final scores + deduction breakdown, ranked desc."""
    from sea_of_colours.snowpark import engine as soc_engine

    summary = soc_engine.get_endgame_summary(store, sid)
    rows: List[Dict[str, Any]] = []
    for p in summary.get("players") or []:
        bd = p.get("breakdown") or {}
        rows.append({
            "seat": p.get("seat"),
            "name": p.get("name"),
            "score": int(p.get("score", 0)),
            "shipped": int(bd.get("shipped", 0)),
            "green_penalty": int(bd.get("green_penalty", 0)),
            "vault_red_loss": int(bd.get("vault_red_loss", 0)),
            "red_harvested": int(p.get("red_harvested", 0)),
            "green_held": int(p.get("green_held", 0)),
            "harvesters_built": int(p.get("harvesters_built", 0)),
        })
    rows.sort(key=lambda r: -r["score"])
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="score_eval")
    ap.add_argument("--n", type=int, default=20, help="Seasons per config.")
    ap.add_argument("--days", type=int, default=7, help="Nights per season.")
    ap.add_argument("--seed-base", type=int, default=4000)
    ap.add_argument("--width", type=int, default=40)
    ap.add_argument("--height", type=int, default=28)
    ap.add_argument("--persist", type=int, default=0,
                    help="Watchable sample seasons to persist per config.")
    ap.add_argument("--store-dir", default="./seasons")
    args = ap.parse_args(argv)

    from sea_of_colours.snowpark.store import InMemorySocStore

    seeds = [args.seed_base + i for i in range(args.n)]
    reports = _REPO_ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    per_config_summary: Dict[str, Dict[str, float]] = {}

    for label, env in CONFIGS:
        _apply_config_env(env)
        print(f"\n[score_eval] ===== {label} ===== env={env}", flush=True)
        csv_rows: List[Dict[str, Any]] = []
        win_by_seat: Dict[str, int] = {p: 0 for p in PLAYERS}
        winner_scores: List[int] = []
        margins: List[int] = []
        all_scores: List[int] = []
        all_shipped: List[int] = []
        all_green_pen: List[int] = []
        all_red_loss: List[int] = []

        for seed in seeds:
            store = InMemorySocStore()
            sid = _run_4p_season(
                store, seed=seed, width=args.width, height=args.height,
                days=args.days,
            )
            rows = _season_scores(store, sid)
            winner = rows[0]
            runner_up = rows[1] if len(rows) > 1 else rows[0]
            win_by_seat[winner["seat"]] = win_by_seat.get(winner["seat"], 0) + 1
            winner_scores.append(winner["score"])
            margins.append(winner["score"] - runner_up["score"])
            for r in rows:
                all_scores.append(r["score"])
                all_shipped.append(r["shipped"])
                all_green_pen.append(r["green_penalty"])
                all_red_loss.append(r["vault_red_loss"])
                csv_rows.append({"config": label, "seed": seed, **r})

            score_str = "  ".join(
                f"{r['seat']}={r['score']}"
                f"(s{r['shipped']}-g{r['green_penalty']}-r{r['vault_red_loss']})"
                for r in rows
            )
            print(
                f"  seed {seed}: winner {winner['seat']} "
                f"'{winner['name']}' {winner['score']}  |  {score_str}",
                flush=True,
            )

        # Persist a couple of watchable, labelled seasons.
        if args.persist > 0:
            from sea_of_colours.snowpark.file_store import FileSocStore
            fstore = FileSocStore(args.store_dir)
            for seed in seeds[: args.persist]:
                name = f"{label}__4p__seed{seed}"
                _run_4p_season(
                    fstore, seed=seed, width=args.width, height=args.height,
                    days=args.days, season_name=name,
                )
                print(f"  persisted watchable season: {name}", flush=True)

        summ = {
            "seasons": float(len(seeds)),
            "score_mean": round(statistics.fmean(all_scores), 1),
            "score_median": round(statistics.median(all_scores), 1),
            "score_max": float(max(all_scores)),
            "winner_score_mean": round(statistics.fmean(winner_scores), 1),
            "margin_mean": round(statistics.fmean(margins), 1),
            "margin_median": round(statistics.median(margins), 1),
            "shipped_mean": round(statistics.fmean(all_shipped), 1),
            "green_penalty_mean": round(statistics.fmean(all_green_pen), 1),
            "vault_red_loss_mean": round(statistics.fmean(all_red_loss), 1),
        }
        per_config_summary[label] = summ

        out = reports / f"score_{label}.csv"
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=[
                    "config", "seed", "seat", "name", "score", "shipped",
                    "green_penalty", "vault_red_loss", "red_harvested",
                    "green_held", "harvesters_built",
                ],
                extrasaction="ignore",
            )
            w.writeheader()
            w.writerows(csv_rows)
        print(f"  wrote {out.name}", flush=True)
        print(f"  win distribution: {win_by_seat}", flush=True)
        print(
            "  scores: "
            f"mean {summ['score_mean']} · median {summ['score_median']} · "
            f"max {summ['score_max']} | winner-mean {summ['winner_score_mean']} | "
            f"margin mean {summ['margin_mean']} | "
            f"shipped {summ['shipped_mean']} · "
            f"green-pen {summ['green_penalty_mean']} · "
            f"red-loss {summ['vault_red_loss_mean']}",
            flush=True,
        )

    print(f"\n[score_eval] done in {time.time() - t0:.1f}s")
    print("\n── final-score comparison (per-seat, with deductions) ──")
    keys = [
        "score_mean", "score_median", "score_max", "winner_score_mean",
        "margin_mean", "shipped_mean", "green_penalty_mean",
        "vault_red_loss_mean",
    ]
    hdr = "metric".ljust(22) + "".join(c.rjust(16) for c, _ in CONFIGS)
    print(hdr)
    for k in keys:
        line = k.ljust(22)
        for c, _ in CONFIGS:
            line += str(per_config_summary[c].get(k, "")).rjust(16)
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
