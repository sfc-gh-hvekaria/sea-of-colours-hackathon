#!/usr/bin/env python3
"""Total HARVESTED RED value per player — production, independent of shipping.

``vault_score`` only counts parcels the orbit catapult actually SHIPPED. This
tool instead sums every parcel a seat put in its hoard over the whole season
(``sess.harvest_log``), valued with the engine's own tier formula
(``effective_purity × RED_QUALITY_MULTIPLIER``). It isolates "did the seat
actually dig up less value?" from "did it just ship late?".

Reads persisted sessions directly (no replay). Requires SOC_BACKEND=snowflake.

Usage::

    PYTHONPATH=. python scripts/harvest_totals.py \\
        91cfd144adff4e07adf10a5f3759adb5 1869cb1201bb4739b9127c7ccb26a938
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _val(sess, parcel, RED_QUALITY_MULTIPLIER) -> int:
    eff = parcel.get("effective_purity")
    if eff is None:
        eff = sess._parcel_purity(parcel)
    eff = max(0, int(eff))
    tier = parcel.get("score_tier") or sess._tier_for_purity(eff)
    return int(round(eff * RED_QUALITY_MULTIPLIER.get(tier, 1.0)))


def _report_session(sess, session_id: str) -> str:
    from sea_of_colours.game.session import RED_QUALITY_MULTIPLIER, Tile

    lines: List[str] = []
    name = getattr(sess, "season_name", "") or "(unnamed)"
    lines.append(f"\n=== {name}  ({session_id[:8]}) ===")
    lines.append(
        f"{'seat':<5} {'agent':<14} {'RED harv value':>14} "
        f"{'RED parcels':>11} {'shipped(vault)':>14} {'unshipped':>10} "
        f"{'green(pois)':>11}"
    )
    rows: List[Dict] = []
    for seat in sess.players:
        agent = str(sess.agents.get(seat, "?"))
        red_value = red_n = green_n = 0
        by_tier: Dict[str, int] = {}
        for parcel in sess.harvest_log.get(seat, []) or []:
            tile = parcel.get("tile_at_harvest")
            if tile is None:
                tile = parcel.get("origin_tile")
            try:
                tile = int(tile)
            except (TypeError, ValueError):
                tile = -1
            if tile == int(Tile.RED):
                v = _val(sess, parcel, RED_QUALITY_MULTIPLIER)
                red_value += v
                red_n += 1
                eff = max(0, int(parcel.get("effective_purity")
                                 or sess._parcel_purity(parcel)))
                t = parcel.get("score_tier") or sess._tier_for_purity(eff)
                by_tier[t] = by_tier.get(t, 0) + 1
            elif tile == int(Tile.GREEN):
                green_n += 1
        shipped = float(sess.cumulative_shipped_score.get(seat, 0.0) or 0.0)
        unshipped = max(0.0, red_value - shipped)
        rows.append({
            "seat": seat, "agent": agent, "red_value": red_value,
            "red_n": red_n, "shipped": shipped, "unshipped": unshipped,
            "green_n": green_n, "by_tier": by_tier,
        })

    for r in sorted(rows, key=lambda r: r["red_value"], reverse=True):
        lines.append(
            f"{r['seat']:<5} {r['agent']:<14} {r['red_value']:>14d} "
            f"{r['red_n']:>11d} {int(r['shipped']):>14d} "
            f"{int(r['unshipped']):>10d} {r['green_n']:>11d}"
        )
    lines.append("  tier breakdown of harvested RED parcels:")
    for r in rows:
        lines.append(f"    {r['seat']} {r['agent']}: {r['by_tier']}")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    sessions = argv or [
        "91cfd144adff4e07adf10a5f3759adb5",
        "1869cb1201bb4739b9127c7ccb26a938",
    ]
    os.environ.setdefault("SOC_BACKEND", "snowflake")
    if os.environ["SOC_BACKEND"].lower() != "snowflake":
        print("preflight: set SOC_BACKEND=snowflake", file=sys.stderr)
        return 2
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark.engine import _hydrate_session

    store = soc_backend.get_store()
    out: List[str] = [
        "HARVESTED RED value = sum(effective_purity × tier_mult) over the whole",
        "harvest_log, regardless of whether it ever shipped. 'shipped(vault)' is",
        "the scored total; 'unshipped' ≈ value harvested but never converted.",
    ]
    for sid in sessions:
        sess = _hydrate_session(store, sid)
        out.append(_report_session(sess, sid))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
