#!/usr/bin/env python3
"""Engineered demo season — build-first weapons flow (v0.9.4).

A sibling of :file:`scripts/fake_collision_season.py`. Bypasses the
agent runtime and feeds hand-crafted policies straight into
``engine.submit_policy`` so the watcher sees one **interdiction
weapon** fire per night.

v1.31 — the caltrop night was CUT, taking the season from 7 nights
to 5. It could not simply be left in place: ``mine_lay`` now parses
to a waste marker, so that night would still run and still announce
itself while producing no frames whatsoever — a demo that lies is
worse than one that is a night shorter.

v0.9.4 — the demo runs TWICE. Nights 1-3 are the "p1 deploys" half
(setup + EMP + chaff fired BY p1 against p2's harvester); Nights
4-5 are the "p2 deploys" half (same choreography mirrored: same
coords, but with p1↔p2 / harvester_p1↔harvester_p2 swapped
throughout). That gives a clean side-by-side A/B of "what does an
EMP look like when MY seat fires it" vs "what does it look like
when the OTHER seat fires it on me".

v0.9.3 introduced the build-first flow: weapons are BUILT during
the preceding Orbit phase and DRAINED from a per-seat stockpile at
launch. So each demo night has a matching orbit action queue.

  Night 1 — PLANNING (no orbit before day 1). Idle for both seats so
    the simulator advances day → 2 and opens an Orbit phase we can
    use to build weapons.

  Orbit Day 2 — p1 builds 1 × EMP warhead.
  Night 2 — EMP warhead deployment.
    p2 probes (10, 10) for vision, drops harvester_p2 onto (10, 10).
    p1 launches an EMP onto the same cell at H2. The 7×7 cloud
    blooms for 8 hours; p2's harvester sits inside it, so every
    step from H3..H9 is smothered (``empd`` frames). The cloud
    evaporates pre-H10, so p2's H10 pickup recovers the harvester
    cleanly — no dawn strand.

  Orbit Day 3 — p1 builds 1 × chaff flare.
  Night 3 — Orbital chaff flare deployment.
    p2 probes (20, 10), drops at (20, 10), then steps east. p1
    fires chaff_flare at H3 — every OTHER seat's hour-3 action is
    smothered. p2's H3 step is cancelled (``chaffed``); the same
    step is retried at H4 and resumes normally (chaff is 1-hour at
    v0.9.0). Pickup at H6 closes the run.

Resources: the script pre-seeds p1's hoard with blue parcels and
boosts p2's probe stock so each night has at least one probe to
satisfy the v0.9.2 fog-of-war drop rule. Credits are covered by
the +1000c/turn Orbit award accumulating across days 2-3.

Run::

    python scripts/fake_weapons_season.py --season-name Weapons_Demo_v093

Then load ``/watch.html?season=weapons-demo-v093`` to see the EMP
burst and the chaff snowstorm play out in sequence.
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


# ── CLI ──────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Engineered weapons-showcase season — one interdiction "
            "weapon per night (EMP / chaff), fired by each seat in turn."
        )
    )
    p.add_argument("--seed", type=int, default=9090)
    p.add_argument("--width", type=int, default=40)
    p.add_argument("--height", type=int, default=28)
    p.add_argument(
        "--season-name",
        default="Weapons_Demo",
        help="Season label / slug source (default: Weapons_Demo).",
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


# ── Choreography ─────────────────────────────────────────────────────
#
# v0.9.3 — weapons are now BUILT during a preceding ORBIT phase and
# DRAINED from a per-seat stockpile at launch. The choreography
# below alternates:
#
#   * an ORBIT submission (``ORBIT_PLANS[i]``) where p1 builds the
#     weapon they need for that night, and
#   * a NIGHT submission (``SCRIPTED_NIGHTS[i]``) that deploys it.
#
# Day 1 opens in PLANNING (no orbit yet), so night 1 is a setup
# night with no weapon — its job is just to advance the day so we
# land in ORBIT for day 2. Nights 2..4 each fire one weapon.
#
# Interleave reminder: during PRAXIS p1 acts first each hour, then
# p2. ``[wait, emp_launch]`` on p1 means "do nothing in H1, fire EMP
# in H2"; p2's parallel ``[probe, drop]`` means "probe at H1, drop
# at H2 (just AFTER p1's EMP fires)".

# v0.9.4 — the demo runs TWICE: once with p1 deploying weapons
# against p2's harvester (Nights 1-4) and then a mirrored run with
# the roles swapped (Nights 5-7). The mirrored half uses the same
# choreography, just substituting p1↔p2 / harvester_p1↔harvester_p2
# / RIGHT-side coords for the same-side coords. That gives a clean
# side-by-side reference for "what does an EMP look like when MY
# seat fires it vs when the OTHER seat fires it on me?"


def _swap_seat(s: str) -> str:
    return "p2" if s == "p1" else "p1"


def _swap_unit(u: str) -> str:
    if u.endswith("_p1"):
        return u[:-3] + "_p2"
    if u.endswith("_p2"):
        return u[:-3] + "_p1"
    return u


# Orbit submissions per day. ``None`` means "no orbit phase for that
# day" (used for the day-1 entry to keep the index aligned with
# SCRIPTED_NIGHTS).
ORBIT_PLANS: List[Optional[Dict[str, List[Dict[str, Any]]]]] = [
    # Day 1 has no orbit phase (engine opens in PLANNING).
    None,
    # Day 2 orbit — p1 builds 1 EMP warhead.
    {"p1": [{"a": "build_emp"}], "p2": []},
    # Day 3 orbit — p1 builds 1 chaff flare.
    {"p1": [{"a": "build_chaff"}], "p2": []},
    # Day 4 orbit — REVERSAL: p2 starts building. EMP again, this
    # time deployed by p2 against p1.
    {"p1": [], "p2": [{"a": "build_emp"}]},
    # Day 5 orbit — p2 builds a chaff flare.
    {"p1": [], "p2": [{"a": "build_chaff"}]},
]


# The four-night "p1 deploys" half. Nights 5-7 are derived by
# swapping every seat / unit reference (see ``_swap_seat``,
# ``_swap_unit`` below).
NIGHTS_P1_DEPLOYS: List[Dict[str, List[Dict[str, Any]]]] = [
    # ── Night 1 — SETUP NIGHT (no weapons, just advance day) ──────
    # Both seats idle for 1 hour. After this resolves we land in
    # ORBIT on day 2 with credits awarded and a fresh slate to start
    # building weapons.
    {
        "p1": [{"a": "wait"}],
        "p2": [{"a": "wait"}],
    },
    # ── Night 2 — EMP warhead ──────────────────────────────────────
    {
        "p1": [
            {"a": "wait"},
            {"a": "emp_launch", "at": [10, 10]},
        ],
        "p2": [
            {"a": "probe", "at": [12, 10]},
            {"a": "drop", "unit": "harvester_p2", "at": [10, 10]},
            {"a": "step", "unit": "harvester_p2", "to": [11, 10]},
            {"a": "step", "unit": "harvester_p2", "to": [11, 10]},
            {"a": "step", "unit": "harvester_p2", "to": [11, 10]},
            {"a": "step", "unit": "harvester_p2", "to": [11, 10]},
            {"a": "step", "unit": "harvester_p2", "to": [11, 10]},
            {"a": "step", "unit": "harvester_p2", "to": [11, 10]},
            {"a": "step", "unit": "harvester_p2", "to": [11, 10]},
            {"a": "pickup", "unit": "harvester_p2"},
        ],
    },
    # ── Night 3 — Orbital chaff flare ──────────────────────────────
    # v1.31 — the caltrop night used to sit between the EMP and the
    # chaff. It was cut rather than left in: ``mine_lay`` now parses to
    # a waste marker, so the night would still run, still print
    # "MINE", and quietly produce no frames at all.
    {
        "p1": [
            {"a": "wait"},
            {"a": "wait"},
            {"a": "chaff_flare"},
        ],
        "p2": [
            {"a": "probe", "at": [22, 10]},
            {"a": "drop", "unit": "harvester_p2", "at": [20, 10]},
            {"a": "step", "unit": "harvester_p2", "to": [21, 10]},
            {"a": "step", "unit": "harvester_p2", "to": [21, 10]},
            {"a": "pickup", "unit": "harvester_p2"},
        ],
    },
]


# How far to shift the mirrored half's coords on the X axis. The
# original choreography lives in x∈[10..22]; nights 2-4 plant probes
# at (12,10) and (22,10) that PERSIST across nights (RULEBOOK §3.11.1
# — probes only die when a harvester crushes them or two probes
# collide). If the mirrored half drops probes on those same cells
# the result is a probe-on-probe collision that wipes out BOTH
# probes and leaves the mirrored seat blind, so its drop into
# (10,10) is rejected as "drop into fog". Shifting the mirror to
# x+15 puts it firmly in untouched territory (~25..37) and gives
# the watcher visually distinct regions for the two halves.
_MIRROR_X_OFFSET = 15


def _shift_xy(pt: List[int], dx: int) -> List[int]:
    if not isinstance(pt, list) or len(pt) != 2:
        return pt
    return [int(pt[0]) + dx, int(pt[1])]


def _mirror_night(
    night: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Swap seats + units + shift coords by ``_MIRROR_X_OFFSET``.

    Two adjustments per mirrored night:

    1. **Seat / unit swap** — p1↔p2, harvester_p1↔harvester_p2 so
       the OTHER seat now plays the deployer role.
    2. **X-axis shift** — every (x,y) in ``at`` / ``to`` is shifted
       right by ``_MIRROR_X_OFFSET`` cells. This keeps the
       choreography away from probes and echoes left over from the
       first half (which would otherwise collide and blind the
       mirrored seat — see the comment on ``_MIRROR_X_OFFSET``).
    """
    new_seat: Dict[str, List[Dict[str, Any]]] = {"p1": [], "p2": []}
    for seat, moves in night.items():
        rewritten: List[Dict[str, Any]] = []
        for mv in moves:
            mv2 = dict(mv)
            if "unit" in mv2 and isinstance(mv2["unit"], str):
                mv2["unit"] = _swap_unit(mv2["unit"])
            if "at" in mv2 and isinstance(mv2["at"], list):
                mv2["at"] = _shift_xy(mv2["at"], _MIRROR_X_OFFSET)
            if "to" in mv2 and isinstance(mv2["to"], list):
                mv2["to"] = _shift_xy(mv2["to"], _MIRROR_X_OFFSET)
            rewritten.append(mv2)
        new_seat[_swap_seat(seat)] = rewritten
    return new_seat


# Build the full 7-night choreography: 4 nights with p1 deploying,
# 3 nights (skipping the setup night) with p2 deploying.
SCRIPTED_NIGHTS: List[Dict[str, List[Dict[str, Any]]]] = (
    list(NIGHTS_P1_DEPLOYS)
    + [_mirror_night(n) for n in NIGHTS_P1_DEPLOYS[1:]]
)


# ── Hoard pre-seeding ────────────────────────────────────────────────


def _blue_parcel(owner: str, idx: int, purity: int) -> Dict[str, Any]:
    """Make a fake BLUE-tile parcel ready to slot into ``owner``'s hoard."""
    from sea_of_colours.generator import Tile
    sid = f"demo-blue-{owner}-{idx:02d}"
    return {
        "square_id": sid,
        "site_id": sid,
        "x": 0,
        "y": 0,
        "tile_at_harvest": int(Tile.BLUE),
        "purity_at_harvest": int(purity),
        "origin_purity": int(purity),
        "origin_tile": int(Tile.BLUE),
        "lineage": "natural",
        "harvested_on_planning_day": 0,
        "stored_received_planning_day": 0,
    }


# v0.9.4 — both seats now build weapons during their half of the
# demo, so both get pre-seeded with the same denomination ladder,
# covering one of everything on the price list with zero waste.
#
# Priced off the weapons table rather than written out (v1.36): this
# used to read [50, 50, 50, 50, 255] for an EMP and a chaff, and the
# moment chaff went 255 -> 300 the demo was seeding a seat that could
# not afford the weapon the demo exists to show.
#
# Fifties rather than one lump per weapon because the station draws a
# pip per parcel and this whole script is a camera rig for watching
# blue turn cyan — one pip is not an animation. Every published cost is
# a multiple of 100, so fifties divide all of them exactly, and they
# stay under the 255 purity ceiling (§3.14) that a 300 lump would not.
def _weapons_blue_ladder() -> List[int]:
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND
    return [
        50
        for cost in BLUE_COST_BY_KIND.values()
        for _ in range(int(cost) // 50)
    ]


WEAPONS_BLUE_PARCELS: List[int] = _weapons_blue_ladder()


def _slug(name: str) -> str:
    out: List[str] = []
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


def _settle_empty_orbit(store, session_id: str) -> None:
    """Auto-settle the current ORBIT phase with empty action queues
    for both seats. Used at session start (because v0.8.0 sessions
    open in ORBIT) and after every night (because dawn re-opens
    ORBIT — RULEBOOK §4)."""
    from sea_of_colours.snowpark import engine as soc_engine
    soc_engine.submit_orbit_actions(store, session_id, "p1", [])
    soc_engine.submit_orbit_actions(store, session_id, "p2", [])


def _inject_demo_stockpile(store, session_id: str) -> None:
    """Pre-seed the session so the demo can actually fire the weapons.

    v0.9.4 — BOTH seats are deployers now (p1 in the first half,
    p2 in the mirrored second half), so both get:

    * a stack of BLUE-tile parcels matching ``WEAPONS_BLUE_PARCELS``
      so the build_* orbit actions can debit blue at face cost with
      zero waste, AND
    * a probe-stock floor of 4 so the 6 target-probing nights across
      the full run never dry up.
    """
    from sea_of_colours.snowpark import engine as soc_engine
    sess = soc_engine._hydrate_session(store, session_id)
    for owner in ("p1", "p2"):
        seed_parcels = [
            _blue_parcel(owner, i, p)
            for i, p in enumerate(WEAPONS_BLUE_PARCELS)
        ]
        sess.hoard_squares[owner] = (
            list(sess.hoard_squares.get(owner, [])) + seed_parcels
        )
        sess.probe_stock[owner] = max(int(sess.probe_stock.get(owner, 0)), 4)
    soc_engine.save_session_full(store, sess)


# ── Reporting ────────────────────────────────────────────────────────


def _print_banner(*, season_name: str, session_id: str, seed: int,
                  backend: str) -> None:
    line = "═" * 72
    print()
    print(line)
    print("  SEA OF COLOURS — engineered WEAPONS demo season (v0.9.4)")
    print(line)
    print(f"  season       : {season_name}")
    print(f"  session_id   : {session_id}")
    print(f"  seed         : {seed}")
    print(f"  backend      : {backend}")
    print(f"  day cap      : {len(SCRIPTED_NIGHTS)} nights "
          f"(build-first weapons, both seats deploy)")
    print(f"  half 1       : N1 setup · N2 p1→EMP · N3 p1→CHAFF")
    print(f"  half 2       : N4 p2→EMP · N5 p2→CHAFF")
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


def _summarize_weapon_events(rep: Dict[str, Any]) -> Dict[str, int]:
    """Count EMP / chaff event payloads in a per-night replay so the
    operator can confirm the FX actually fired engine-side (the splash
    / cloud / static animations are client-only)."""
    frames = [f for d in rep.get("days", []) for f in d.get("frames", [])]
    n_emp = sum(len(f.get("emp") or []) for f in frames)
    n_chaff = sum(len(f.get("chaff") or []) for f in frames)
    n_empd = sum(1 for f in frames if f.get("tag") == "empd")
    n_chaffed = sum(1 for f in frames if f.get("tag") == "chaffed")
    return {
        "emp_events": n_emp,
        "chaff_events": n_chaff,
        "empd_frames": n_empd,
        "chaffed_frames": n_chaffed,
    }


# ── Main ─────────────────────────────────────────────────────────────


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

    # v0.9.4 — sessions open in PLANNING for day 1. No opening
    # orbit to settle; the engineered stockpile is injected now so
    # that both halves of the demo (p1 deploys, then p2 deploys)
    # have the blue parcels they need to pay each build.
    print(f"  → injecting BLUE stockpile into BOTH hoards "
          f"(parcels: {WEAPONS_BLUE_PARCELS}) + boosting probe stock",
          flush=True)
    _inject_demo_stockpile(store, session_id)

    for night_idx, policies in enumerate(SCRIPTED_NIGHTS, start=1):
        print()
        print(f"── Day {night_idx} ──────────────────────────────────────",
              flush=True)

        # First settle the matching ORBIT phase (if any). The day-1
        # entry in ORBIT_PLANS is ``None`` because day 1 opens
        # directly in PLANNING — every other day comes in via the
        # post-dawn ORBIT transition, which is where weapons get
        # built.
        plan = ORBIT_PLANS[night_idx - 1] if night_idx - 1 < len(ORBIT_PLANS) else None
        if plan is not None:
            print(f"  ORBIT (day {night_idx})", flush=True)
            for seat in ("p1", "p2"):
                acts = plan.get(seat, []) or []
                print(f"    [{seat}] orbit actions: {acts}", flush=True)
                res_orbit = soc_engine.submit_orbit_actions(
                    store, session_id, seat, acts,
                )
                if not res_orbit.get("ok", True):
                    print(f"    [!] {seat} orbit failed: {res_orbit.get('errors')}",
                          flush=True)
                    return 2
            # Surface the post-resolution stockpile so the operator
            # can see the build landed.
            sess_after_orbit = soc_engine._hydrate_session(store, session_id)
            print(f"    → p1 weapon_stock: {sess_after_orbit.weapon_stock['p1']}",
                  flush=True)

        print(f"  NIGHT {night_idx}", flush=True)
        for seat in ("p1", "p2"):
            moves = policies.get(seat, [])
            print(f"    [{seat}] submit {len(moves)} move(s): {moves}",
                  flush=True)
            res = soc_engine.submit_policy(store, session_id, seat, moves)
            if not res.get("ok", True):
                print(f"    [!] {seat} submit failed: {res.get('errors')}",
                      flush=True)
                return 2
            if res.get("night_resolved"):
                print(f"    → PRAXIS resolved (day advanced)", flush=True)

        rep = soc_engine.get_replay(
            store, session_id, day_from=night_idx, day_to=night_idx,
        )
        events = _summarize_weapon_events(rep)
        print(
            "    events: "
            f"emp={events['emp_events']}  "
            f"chaff={events['chaff_events']}  "
            f"empd_frames={events['empd_frames']}  "
            f"chaffed_frames={events['chaffed_frames']}",
            flush=True,
        )

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
