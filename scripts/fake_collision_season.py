#!/usr/bin/env python3
"""Engineered demo season — three nights, three FX events.

This script bypasses the agent runtime entirely and submits hand-
crafted policies directly through ``engine.submit_policy``. Each
night is deliberately set up to produce **one** of the three FX
events we want to show off in the watcher:

  Night 1 — Probe crush.
    p1 plants a probe at (10,10); p2 drops harvester_p2 on top of it.
    The harvester landing crushes the probe, surfacing a
    ``crushed_probes`` event on the drop frame; the watcher fires a
    small pixel-splash in the probe owner's seat colour. p2 then
    picks up the harvester so the asset survives dawn.

  Night 2 — Drop-on-harvester collision (landing).
    Both seats target the same cell at the same hour. p1 drops first
    (ok), p2's drop lands on the now-healthy harvester → mutual
    damage. Both seats then pick up (the lifter auto-repairs damaged
    harvesters in orbit, RULEBOOK §3.6.1) so both assets survive.

  Night 3 — Pass-through swap collision.
    Both seats drop adjacent (p1 @ (20,14), p2 @ (21,14)), then step
    into each other's tiles on the same hour. The swap pre-pass
    detects the A↔B cross-step pattern and resolves it as mutual
    damage at both destination cells. Both seats then pick up.

The season uses a 3-day cap so the run is short and watchable. It
defaults to the Snowflake backend so the watcher (``/watch.html``)
can replay it; pass ``--backend memory`` to inspect locally without
hitting Snowflake.

Run:
    python scripts/fake_collision_season.py --season-name FX_Demo_Reel

Then load ``/watch.html?season=fx-demo-reel`` (or whatever slug the
``--season-name`` resolves to) to see all three FX in one season.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Engineered FX-showcase season — 3 nights, one collision "
            "type per night (probe crush / drop-on / swap)."
        )
    )
    p.add_argument("--seed", type=int, default=4242)
    p.add_argument("--width", type=int, default=40)
    p.add_argument("--height", type=int, default=28)
    p.add_argument(
        "--season-name",
        default="FX_Demo_Reel",
        help="Season label / slug source (default: FX_Demo_Reel).",
    )
    p.add_argument(
        "--backend",
        choices=("snowflake", "memory"),
        default=None,
        help=(
            "Storage backend. Defaults to SOC_BACKEND env (or "
            "'snowflake' if unset). Use 'memory' for an offline "
            "smoke test."
        ),
    )
    p.add_argument(
        "--wipe-first",
        action="store_true",
        help="Destructive: wipe all SOC_* rows before init.",
    )
    p.add_argument(
        "--watch-base-url",
        default="",
        help=(
            "Base URL of the watcher (e.g. http://localhost:8000). The "
            "final block stitches the watch URL together with the "
            "season slug so the run can be opened in one click."
        ),
    )
    return p


# Three hand-crafted nights. Each entry is the (p1_policy, p2_policy)
# pair to submit for that night. Coordinates are picked to land in
# empty-tile space for the default 40×28 grid + seed 4242 so the FX
# stand out without being buried under harvested RED.
#
# The interleave model is p1 first, then p2, alternating per hour
# (RULEBOOK §3.10) — so the choreography below assumes p1's drop
# at hour 1 happens BEFORE p2's drop at hour 1.
SCRIPTED_NIGHTS: List[Dict[str, List[Dict[str, Any]]]] = [
    # ── Night 1 — probe crush ──────────────────────────────────────
    {
        "p1": [
            {"a": "probe", "at": [10, 10]},
        ],
        "p2": [
            {"a": "drop", "unit": "harvester_p2", "at": [10, 10]},
            {"a": "pickup", "unit": "harvester_p2"},
        ],
    },
    # ── Night 2 — drop-on-harvester (landing collision) ────────────
    {
        "p1": [
            {"a": "drop", "unit": "harvester_p1", "at": [15, 12]},
            {"a": "pickup", "unit": "harvester_p1"},
        ],
        "p2": [
            {"a": "drop", "unit": "harvester_p2", "at": [15, 12]},
            {"a": "pickup", "unit": "harvester_p2"},
        ],
    },
    # ── Night 3 — pass-through swap collision ──────────────────────
    {
        "p1": [
            {"a": "drop", "unit": "harvester_p1", "at": [20, 14]},
            {"a": "step", "unit": "harvester_p1", "to": [21, 14]},
            {"a": "pickup", "unit": "harvester_p1"},
        ],
        "p2": [
            {"a": "drop", "unit": "harvester_p2", "at": [21, 14]},
            {"a": "step", "unit": "harvester_p2", "to": [20, 14]},
            {"a": "pickup", "unit": "harvester_p2"},
        ],
    },
]


def _slug(name: str) -> str:
    """Mirror the watcher's season-name → slug rule (lower, hyphen)."""
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def _configure_backend(arg_backend: Optional[str]) -> str:
    if arg_backend is not None:
        os.environ["SOC_BACKEND"] = arg_backend
    os.environ.setdefault("SOC_BACKEND", "snowflake")
    return os.environ["SOC_BACKEND"].lower()


def _print_banner(*, season_name: str, session_id: str, seed: int,
                  backend: str) -> None:
    line = "═" * 72
    print()
    print(line)
    print("  SEA OF COLOURS — engineered FX demo season")
    print(line)
    print(f"  season       : {season_name}")
    print(f"  session_id   : {session_id}")
    print(f"  seed         : {seed}")
    print(f"  backend      : {backend}")
    print(f"  day cap      : 3 nights (one FX type per night)")
    print(f"  choreography : N1 = probe crush · N2 = drop-on · "
          f"N3 = pass-through swap")
    print(line, flush=True)


def _print_final_block(*, view: Dict[str, Any], season_name: str,
                       session_id: str, watch_base_url: str) -> None:
    slug = _slug(season_name)
    scores = view.get("scores") or {}
    line = "═" * 72
    print()
    print(line)
    print(f"  SEASON_COMPLETE — {season_name}")
    print(line)
    print(f"  session_id   : {session_id}")
    print(f"  scores")
    print(f"    p1 : {scores.get('p1', 0)}")
    print(f"    p2 : {scores.get('p2', 0)}")
    print(f"  replay")
    print(f"    season slug : {slug}")
    base = watch_base_url.rstrip("/")
    print(f"    watch URL   : {base}/watch.html?season={slug}")
    print(f"    proxy URL   : {base}/api/game/{session_id}/replay")
    print(line, flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    backend = _configure_backend(args.backend)

    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import engine as soc_engine
    from sea_of_colours.game.session import Phase

    soc_backend.reset_for_tests()
    store = soc_backend.get_store()

    if args.wipe_first:
        print("--wipe-first: deleting all prior SOC_* rows before starting",
              flush=True)
        store.wipe_all_sessions()

    info = soc_engine.init_session(
        store,
        seed=args.seed,
        width=args.width,
        height=args.height,
        season_name=args.season_name,
        season_day_cap=len(SCRIPTED_NIGHTS),
    )
    session_id = info["session_id"]
    season_name = info.get("season_name") or args.season_name

    _print_banner(
        season_name=season_name,
        session_id=session_id,
        seed=args.seed,
        backend=backend,
    )

    started = time.time()
    for night_idx, policies in enumerate(SCRIPTED_NIGHTS, start=1):
        print()
        print(f"── Night {night_idx} ─────────────────────────────────────",
              flush=True)
        for seat in ("p1", "p2"):
            moves = policies.get(seat, [])
            print(f"  [{seat}] submit {len(moves)} move(s): {moves}",
                  flush=True)
            res = soc_engine.submit_policy(store, session_id, seat, moves)
            if not res.get("ok", True):
                print(f"  [!] {seat} submit failed: {res.get('errors')}",
                      flush=True)
                return 2
            if res.get("night_resolved"):
                print(f"  → PRAXIS resolved (day advanced)", flush=True)

        # Surface this night's collision + crushed-probe counts from
        # the freshly resolved replay so the operator can confirm the
        # FX events actually fired at the engine layer (the splash
        # animations are client-side and won't show in stdout).
        rep = soc_engine.get_replay(
            store, session_id, day_from=night_idx, day_to=night_idx,
        )
        frames = [f for d in rep.get("days", []) for f in d.get("frames", [])]
        n_coll = sum(len(f.get("collisions") or []) for f in frames)
        n_crush = sum(len(f.get("crushed_probes") or []) for f in frames)
        n_swap = sum(
            1 for f in frames if f.get("tag") == "collision_swap"
        )
        print(
            f"  events: collisions={n_coll}  crushed_probes={n_crush}  "
            f"swap_frames={n_swap}",
            flush=True,
        )

    # Pull the final phase + per-seat scores from the player view (any
    # seat will do — ``scores`` reports both).
    final = soc_engine.get_view(store, session_id, "p1")
    phase = final.get("phase")
    if phase != Phase.SEASON_COMPLETE.value:
        print(f"\n[warn] season did not reach SEASON_COMPLETE "
              f"(phase={phase!r}); did the day cap drop?",
              flush=True)

    _print_final_block(
        view=final,
        season_name=season_name,
        session_id=session_id,
        watch_base_url=args.watch_base_url or "",
    )
    print(f"  wall time    : {time.time() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
