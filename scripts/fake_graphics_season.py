#!/usr/bin/env python3
"""Engineered demo season — the full GRAPHICS / FX reel in one season.

This merges the two former single-purpose demos —
:file:`scripts/fake_collision_season.py` (harvester collision FX) and
:file:`scripts/fake_weapons_season.py` (interdiction-weapon FX) — into a
*single* watchable season so the whole client FX catalogue can be
eyeballed from one replay.

Eleven scripted nights, one effect family per night::

    Night  1 — PROBE CRUSH.        p1 probes (10,10), then drops its own
                                   harvester on top → the landing crushes
                                   the probe (``crushed_probes`` splash).
    Night  2 — DROP-ON COLLISION.  both seats land a harvester on (15,12)
                                   in the same hour → mutual-damage
                                   collision burst.
    Night  3 — PASS-THROUGH SWAP.  adjacent harvesters cross-step into
                                   each other's tile → swap collision.
    Night  4 — PROBE SUPERSEDE.    p2 lands on p1's probe an hour later
                                   (§3.16(a)) → streak, ripple, and the
                                   old probe bursts under the newcomer.
    Night  5 — PILE-UP.            three probes die on one square
                                   (§3.16(c)) → two staggered streaks,
                                   three splashes, no ripple, nothing
                                   left standing.
    Night  6 — EMP  (p1 fires).    7×7 EMP cloud smothers p2's harvester.
    Night  7 — CHAFF (p1 fires).   orbital chaff flare smothers the hour.
    Night  8 — EMP  (p2 fires).    mirrored: p2 deploys against p1.
    Night  9 — CHAFF (p2 fires).   mirrored chaff.

v1.31 — there were two caltrop nights, N7 and N10. The weapons half of
this reel is DERIVED from ``fake_weapons_season.NIGHTS_P1_DEPLOYS``, so
cutting the mine there shortened this reel from 11 nights to 9 on its
own; only the prose and the tally needed a hand.

Because v0.9.2+ requires a live sensor beacon over the landing cell, the
collision nights each probe for line-of-sight *before* dropping (probe
vision radius is 2, so a seat can light up a cell from up to two tiles
away — see night 2 where p2 probes (13,12) to observe (15,12)). The
weapons half is BUILD-first: weapons are built in the preceding Orbit
phase and drained from a per-seat stockpile at launch, so this script
pre-seeds both hoards with blue parcels and tops up probe stock.

Run::

    python scripts/fake_graphics_season.py --backend file \\
        --season-name Graphics_FX_Reel --wipe-first

Then serve it on the SAME backend and load
``/watch.html?season=graphics-fx-reel`` to scrub the whole effect
catalogue end to end::

    SOC_BACKEND=file python run_web.py --port 8013

``--backend memory`` still works for a quick engine-side check, but the
store dies with this process, so nothing is left for a server to replay.
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
# The sibling demo modules live next to this file (scripts/ is not a
# package), so add the scripts dir to the path and import by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_weapons_season as fws  # noqa: E402  (path set above)


# ── Collision choreography (nights 1-3) ──────────────────────────────
#
# Interleave model: p1 acts first each hour, then p2 (RULEBOOK §3.10).
# Every drop is preceded by a probe so the seat has the live LOS the
# v0.9.2 fog rule demands. A seat dropping on its OWN freshly-probed
# cell also crushes that probe — a bonus splash on the collision nights.
COLLISION_NIGHTS: List[Dict[str, List[Dict[str, Any]]]] = [
    # ── Night 1 — PROBE CRUSH ──────────────────────────────────────
    # p1 probes (10,10) for LOS, drops its harvester there (crushing
    # the probe), then lifts it back to orbit so the asset survives.
    {
        "p1": [
            {"a": "probe", "at": [10, 10]},
            {"a": "drop", "unit": "harvester_p1", "at": [10, 10]},
            {"a": "pickup", "unit": "harvester_p1"},
        ],
        "p2": [{"a": "wait"}],
    },
    # ── Night 2 — DROP-ON COLLISION ────────────────────────────────
    # A *staggered* landing so it's a true drop-on (not a simultaneous
    # double-drop, which would knock BOTH harvesters orbital). p1 probes
    # then lands at (15,12) at H2. p2 probes (13,12) — seeing (15,12)
    # via the radius-2 disk — waits a beat, then drops onto the occupied
    # cell at H3 → p1's grounded harvester takes damage but stays put;
    # p2's lander never sets down and stays orbital + damaged (repaired
    # in the next Orbit, see ORBIT_PLANS).
    {
        "p1": [
            {"a": "probe", "at": [15, 12]},
            {"a": "drop", "unit": "harvester_p1", "at": [15, 12]},
            {"a": "wait"},
            {"a": "pickup", "unit": "harvester_p1"},
        ],
        "p2": [
            {"a": "probe", "at": [13, 12]},
            {"a": "wait"},
            {"a": "drop", "unit": "harvester_p2", "at": [15, 12]},
            {"a": "pickup", "unit": "harvester_p2"},
        ],
    },
    # ── Night 3 — PASS-THROUGH SWAP ────────────────────────────────
    # Adjacent drops at (20,14)/(21,14), then a same-hour cross-step
    # into each other's tile → the swap pre-pass resolves it as mutual
    # damage at both destination cells.
    {
        "p1": [
            {"a": "probe", "at": [20, 14]},
            {"a": "drop", "unit": "harvester_p1", "at": [20, 14]},
            {"a": "step", "unit": "harvester_p1", "to": [21, 14]},
            {"a": "pickup", "unit": "harvester_p1"},
        ],
        "p2": [
            {"a": "probe", "at": [21, 14]},
            {"a": "drop", "unit": "harvester_p2", "at": [21, 14]},
            {"a": "step", "unit": "harvester_p2", "to": [20, 14]},
            {"a": "pickup", "unit": "harvester_p2"},
        ],
    },
    # ── Night 4 — PROBE SUPERSEDE (§3.16(a)) ───────────────────────
    # v1.15 — p1 lands on (25,8) at H1; p2 lands on the SAME cell at
    # H2. A later stamp, so it's supersession, not annihilation: p2's
    # streak arrives, the ripple opens (it takes the vision), and p1's
    # probe bursts under it. One streak, one ripple, one splash.
    {
        "p1": [{"a": "probe", "at": [25, 8]}],
        "p2": [{"a": "wait"}, {"a": "probe", "at": [25, 8]}],
    },
    # ── Night 5 — PILE-UP ON ONE SQUARE (§3.16(c)) ─────────────────
    # v1.15 — three probes die on (28,20). p1 takes it at H1 (the
    # incumbent), then BOTH seats land there at H2 on the same stamp:
    # the two arrivals annihilate, and the incumbent goes with them as
    # a collision casualty with nobody credited a supersede. Two
    # staggered streaks, three splashes, an empty square — and NO
    # ripple, because none of them lived to grant vision.
    #
    # Two seats is the ceiling on same-hour arrivals (one slot each),
    # so this is the deepest pile a 2-seat reel can stage; the 3- and
    # 4-way piles are covered in tests/test_probe_death_fx.py.
    {
        "p1": [{"a": "probe", "at": [28, 20]}, {"a": "probe", "at": [28, 20]}],
        "p2": [{"a": "wait"}, {"a": "probe", "at": [28, 20]}],
    },
]


# Full choreography: the collision / probe-FX nights, then the weapon
# nights (p1 deploys EMP + chaff, then the mirrored p2 half). We
# skip the weapons demo's idle "setup night" — the collision nights
# already carry us out of day-1 PLANNING.
WEAPON_NIGHTS = list(fws.NIGHTS_P1_DEPLOYS[1:]) + [
    fws._mirror_night(n) for n in fws.NIGHTS_P1_DEPLOYS[1:]
]
SCRIPTED_NIGHTS: List[Dict[str, List[Dict[str, Any]]]] = (
    COLLISION_NIGHTS + WEAPON_NIGHTS
)

# Orbit submissions per night (1-based). Day 1 opens in PLANNING (no
# orbit). Night 2 settles an EMPTY orbit. The orbit before night 3
# REPAIRS p2's harvester: night 2's drop-on collision leaves the second
# dropper (p2, since p1 acts first) *orbital* and damaged — and an
# orbital unit can't be picked up, so the free surface-pickup repair
# never applies. A paid Orbit REPAIR is the only way to clear it before
# it would otherwise block every later drop. The weapon nights reuse the
# weapons demo's per-night BUILD plans.
_EMPTY_ORBIT: Dict[str, List[Dict[str, Any]]] = {"p1": [], "p2": []}
ORBIT_PLANS: List[Optional[Dict[str, List[Dict[str, Any]]]]] = (
    [
        None,
        dict(_EMPTY_ORBIT),
        {"p1": [], "p2": [{"a": "repair", "unit": "harvester_p2"}]},
        # v1.15 — the two probe-collision nights settle empty orbits. These
        # two entries exist to keep the weapon nights aligned with the
        # weapons demo's BUILD plans below: this list is indexed by night,
        # so inserting nights without padding here would hand night 6 the
        # orbit meant for night 4 and every weapon launch after it would
        # fire from an empty stockpile.
        dict(_EMPTY_ORBIT),
        dict(_EMPTY_ORBIT),
    ]
    + list(fws.ORBIT_PLANS[1:])
)

# Probe budget: each seat probes several times across the 9 nights
# (p1: 3 collision + 3 mirrored-weapon target nights; p2: 2 collision +
# 3 weapon target nights). Top both seats up generously so nobody dries
# out mid-reel.
_PROBE_FLOOR = 16


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Engineered GRAPHICS/FX showcase season — 9 nights covering "
            "every collision + interdiction-weapon effect in one replay."
        )
    )
    p.add_argument("--seed", type=int, default=4242)
    p.add_argument("--width", type=int, default=40)
    p.add_argument("--height", type=int, default=28)
    p.add_argument(
        "--season-name",
        default="Graphics_FX_Reel",
        help="Season label / slug source (default: Graphics_FX_Reel).",
    )
    p.add_argument(
        "--backend",
        choices=("snowflake", "file", "memory"),
        default=None,
        help=(
            "Storage backend. Defaults to SOC_BACKEND env (or "
            "'snowflake' if unset). Use 'memory' for a throwaway check, "
            "or 'file' to leave the reel on disk (SOC_STORE_DIR, default "
            ".soc_sessions) so a server started on the same backend can "
            "actually replay it — a memory store dies with this process."
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
        help="Base URL of the watcher (e.g. http://localhost:8000).",
    )
    return p


def _inject_stockpile(store, session_id: str) -> None:
    """Seed both hoards with weapon-build blue parcels and a tall probe
    floor (the combined reel probes more often than either source demo)."""
    from sea_of_colours.snowpark import engine as soc_engine
    fws._inject_demo_stockpile(store, session_id)
    sess = soc_engine._hydrate_session(store, session_id)
    for owner in ("p1", "p2"):
        sess.probe_stock[owner] = max(
            int(sess.probe_stock.get(owner, 0)), _PROBE_FLOOR
        )
    soc_engine.save_session_full(store, sess)


def _summarize_events(rep: Dict[str, Any]) -> Dict[str, int]:
    """Count every FX-bearing payload in a per-night replay so the
    operator can confirm the effects fired engine-side (the animations
    themselves are client-only)."""
    frames = [f for d in rep.get("days", []) for f in d.get("frames", [])]
    wep = fws._summarize_weapon_events(rep)
    wep.update({
        "collisions": sum(len(f.get("collisions") or []) for f in frames),
        "crushed_probes": sum(
            len(f.get("crushed_probes") or []) for f in frames
        ),
        "swap_frames": sum(
            1 for f in frames if f.get("tag") == "collision_swap"
        ),
    })
    return wep


def _print_banner(*, season_name: str, session_id: str, seed: int,
                  backend: str) -> None:
    line = "═" * 72
    print()
    print(line)
    print("  SEA OF COLOURS — engineered GRAPHICS / FX reel season")
    print(line)
    print(f"  season       : {season_name}")
    print(f"  session_id   : {session_id}")
    print(f"  seed         : {seed}")
    print(f"  backend      : {backend}")
    print(f"  day cap      : {len(SCRIPTED_NIGHTS)} nights")
    print("  collisions   : N1 crush · N2 drop-on · N3 swap")
    print("  probe FX     : N4 supersede · N5 pile-up (3 dead on one cell)")
    print("  weapons (p1) : N6 EMP · N7 CHAFF")
    print("  weapons (p2) : N8 EMP · N9 CHAFF")
    print(line, flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    backend = fws._configure_backend(args.backend)

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

    print("  → injecting BLUE stockpile into BOTH hoards + topping up "
          f"probe stock (floor {_PROBE_FLOOR})", flush=True)
    _inject_stockpile(store, session_id)

    for night_idx, policies in enumerate(SCRIPTED_NIGHTS, start=1):
        print()
        print(f"── Day {night_idx} ──────────────────────────────────────",
              flush=True)

        plan = (
            ORBIT_PLANS[night_idx - 1]
            if night_idx - 1 < len(ORBIT_PLANS)
            else None
        )
        if plan is not None:
            print(f"  ORBIT (day {night_idx})", flush=True)
            for seat in ("p1", "p2"):
                acts = plan.get(seat, []) or []
                if acts:
                    print(f"    [{seat}] orbit actions: {acts}", flush=True)
                res_orbit = soc_engine.submit_orbit_actions(
                    store, session_id, seat, acts,
                )
                if not res_orbit.get("ok", True):
                    print(f"    [!] {seat} orbit failed: "
                          f"{res_orbit.get('errors')}", flush=True)
                    return 2

        print(f"  NIGHT {night_idx}", flush=True)
        for seat in ("p1", "p2"):
            moves = policies.get(seat, [])
            print(f"    [{seat}] submit {len(moves)} move(s)", flush=True)
            res = soc_engine.submit_policy(store, session_id, seat, moves)
            if not res.get("ok", True):
                print(f"    [!] {seat} submit failed: {res.get('errors')}",
                      flush=True)
                return 2
            if res.get("night_resolved"):
                print("    → PRAXIS resolved (day advanced)", flush=True)

        rep = soc_engine.get_replay(
            store, session_id, day_from=night_idx, day_to=night_idx,
        )
        ev = _summarize_events(rep)
        print(
            "    events: "
            f"crush={ev['crushed_probes']}  coll={ev['collisions']}  "
            f"swap={ev['swap_frames']}  emp={ev['emp_events']}  "
            f"chaff={ev['chaff_events']}",
            flush=True,
        )

    final = soc_engine.get_view(store, session_id, "p1")
    phase = final.get("phase")
    if phase != Phase.SEASON_COMPLETE.value:
        print(f"\n[note] final phase={phase!r} (final-orbit / complete).",
              flush=True)

    fws._print_final_block(
        view=final,
        season_name=season_name,
        session_id=session_id,
        watch_base_url=args.watch_base_url or "",
    )
    print(f"  wall time    : {time.time() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
