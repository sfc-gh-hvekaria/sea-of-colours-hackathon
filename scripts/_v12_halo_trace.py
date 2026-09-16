#!/usr/bin/env python3
"""How often does V12's blind-comb pricing actually reach its fallback?

`option_economics._ASSUMED_HALO_PURITY = 55` prices the halo of a redsign
smear the agent cannot see. RULEBOOK §2.2 (v1.29) enriched exactly that
ground, so the constant is probably low — but `blind_estimate` MEASURES
each seam it can see and POOLS across seams before reaching any constant,
and the measuring path adapts to graded terrain on its own. Whether the
constant is worth retuning therefore depends on a number nobody had:

    how often is it actually reached, and what do real smears measure now?

This answers both by playing real offline seasons (heuristic seats, memory
backend, no Cortex and no network), rebuilding the V12 agent_view at every
planning day for every seat, and pricing a comb over each redsign smear —
the same call the live harness makes. It never asks an LLM anything; only
the option-card arithmetic is exercised, which is where the constant lives.

Usage::

    python scripts/_v12_halo_trace.py [n_seasons] [--seats 2] [--days 10]
    python scripts/_v12_halo_trace.py 12 --seats 3

Scratch harness, like the other ``scripts/_*.py`` — not a test.
"""
from __future__ import annotations

import argparse
import os
import statistics as st
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SOC_BACKEND", "memory")
os.environ["SOC_V12_HALO_TRACE"] = "1"

import scripts.run_season as run_season  # noqa: E402
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import (  # noqa: E402
    option_economics as oe,
)
from sea_of_colours.snowpark import engine as sp_engine  # noqa: E402

_SEEN_VIEWS = 0


def _price_every_smear(view: dict) -> None:
    """Make the same call the harness makes, once per smear region.

    Combs the WHOLE smear rather than a plausible 6-cell walk: we are
    sampling which pricing PATH a seam takes, and that does not depend on
    how many of its cells we choose to walk.
    """
    for region in oe._redsign_smear_regions(view):
        if region:
            oe.blind_estimate(sorted(region), view)


def _install_hook() -> None:
    """Price smears on every agent view the engine builds.

    Wrapping the engine's own view builder, rather than re-implementing a
    season loop here, is the point: it means the fog, the probe coverage
    and the redsign state we sample are exactly the ones a real seat gets
    on a real night, not an approximation of them.
    """
    original = sp_engine.build_agent_view

    def wrapper(*a, **kw):
        global _SEEN_VIEWS
        view = original(*a, **kw)
        _SEEN_VIEWS += 1
        try:
            _price_every_smear(view)
        except Exception:
            pass          # telemetry must never break the season
        return view

    sp_engine.build_agent_view = wrapper


def _play(seed: int, seats: int, days: int) -> None:
    argv = ["--seed", str(seed), "--days", str(days),
            "--backend", "memory", "--quiet"]
    for i in range(seats):
        argv += [f"--p{i + 1}", "heuristic"]
    try:
        run_season.main(argv)
    except SystemExit:
        pass
    except Exception as exc:
        print(f"  (seed {seed} aborted: {type(exc).__name__}: {exc})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=10)
    ap.add_argument("--seats", type=int, default=2)
    ap.add_argument("--days", type=int, default=10)
    args = ap.parse_args()

    oe.reset_halo_trace()
    _install_hook()
    for seed in range(args.n):
        _play(seed, args.seats, args.days)

    rows = oe.halo_trace()
    print(f"\nseasons: {args.n}   seats: {args.seats}   "
          f"agent views sampled: {_SEEN_VIEWS}")
    print(f"blind_estimate calls with a priceable comb: {len(rows)}")
    if not rows:
        print("\nNo redsign smear was ever priced — no pure was discovered.")
        print("Try more seasons or more days.")
        return 0

    # The headline: per SEAM, which path priced it.
    paths = Counter(s for r in rows for s in r["sources"])
    seams = sum(paths.values())
    print(f"\nper-seam pricing path ({seams} seams priced):")
    labels = {
        "measured": "measured off this seam        (self-corrects on graded terrain)",
        "pooled": "pooled from another seam      (self-corrects)",
        "constant_purity": "CONSTANT purity, measured density",
        "constant_both": "CONSTANT purity AND density",
    }
    for key in ("measured", "pooled", "constant_purity", "constant_both"):
        n = paths.get(key, 0)
        print(f"  {labels[key]:<58} {n:5d}  ({100.0 * n / seams:5.1f}%)")
    reach = paths.get("constant_purity", 0) + paths.get("constant_both", 0)
    print(f"\n  => the constant is reached on {100.0 * reach / seams:.1f}% of seams")

    # And the other half of the question: what do real smears measure NOW?
    measured = [p for r in rows for p in r["measured_purities"]]
    dens = [d for r in rows for d in r["measured_densities"]]
    if measured:
        measured.sort()
        print(f"\nmeasured non-pure purity on real smears "
              f"(n={len(measured)}):")
        print(f"  min {measured[0]}  median {measured[len(measured) // 2]}  "
              f"mean {st.mean(measured):.1f}  max {measured[-1]}")
        print(f"  vs the constant's {oe._ASSUMED_HALO_PURITY}"
              f"   (mass tier, and its x1.5 multiplier, starts at 151)")
        over = sum(1 for p in measured if p >= 151)
        print(f"  smears measuring into MASS: {over}/{len(measured)} "
              f"({100.0 * over / len(measured):.1f}%)")
    if dens:
        dens.sort()
        print(f"\nmeasured RED density (n={len(dens)}): "
              f"median {dens[len(dens) // 2]:.2f}  mean {st.mean(dens):.2f}"
              f"   vs the constant's {oe._ASSUMED_RED_DENSITY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
