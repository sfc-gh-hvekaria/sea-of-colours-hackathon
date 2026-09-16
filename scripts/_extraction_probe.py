"""Scratch probe: what does "% of the map's RED extracted" actually come to?

Three defensible denominators give three different stories, so this
prints all of them against a real finished season before we commit the
report card to one. Throwaway — not wired into anything.
"""
from __future__ import annotations

import sys

from sea_of_colours.generator import Tile
from sea_of_colours.game.session import (
    RED_QUALITY_MULTIPLIER,
    GameSession,
)
from sea_of_colours.snowpark import backend as soc_backend
from sea_of_colours.snowpark import engine as soc_engine


def tier_value(purity: int) -> int:
    tier = GameSession._tier_for_purity(purity)
    return int(round(purity * RED_QUALITY_MULTIPLIER.get(tier, 1.0)))


def main(session_id: str) -> None:
    store = soc_backend.store_for_session(session_id)
    sess = soc_engine._hydrate_session(store, session_id)

    # ── denominator: the map as generated ────────────────────────────
    gen_cells = 0
    gen_purity = 0
    gen_value = 0
    by_tier: dict[str, int] = {}
    for entry in sess.ledger.entries.values():
        if int(entry["tile_at_generation"]) != int(Tile.RED):
            continue
        p = int(entry["purity_at_generation"])
        gen_cells += 1
        gen_purity += p
        gen_value += tier_value(p)
        by_tier[GameSession._tier_for_purity(p)] = (
            by_tier.get(GameSession._tier_for_purity(p), 0) + 1
        )

    print(f"season      : {sess.season_name}  seed={sess.seed}")
    print(f"grid        : {sess.width}x{sess.height} = {sess.width * sess.height} cells")
    print()
    print("GENESIS RED ON MAP")
    print(f"  cells         : {gen_cells}")
    print(f"  raw purity    : {gen_purity}")
    print(f"  tier value    : {gen_value}")
    print(f"  by tier       : {by_tier}")
    print()

    # ── numerators ───────────────────────────────────────────────────
    print("PER SEAT")
    tot_h_cells = tot_h_value = tot_s_value = 0
    for seat in sess.players:
        h_cells = h_purity = h_value = 0
        for parcel in sess.harvest_log.get(seat, []) or []:
            if int(parcel.get("tile_at_harvest", -1)) != int(Tile.RED):
                continue
            p = int(GameSession._parcel_purity(parcel))
            h_cells += 1
            h_purity += p
            h_value += tier_value(p)

        s_value = 0
        for parcel in sess.shipped_squares.get(seat, []) or []:
            if int(parcel.get("tile_at_harvest", -1)) != int(Tile.RED):
                continue
            eff = int(
                parcel.get("effective_purity")
                if parcel.get("effective_purity") is not None
                else GameSession._parcel_purity(parcel)
            )
            tier = parcel.get("score_tier") or GameSession._tier_for_purity(eff)
            s_value += int(round(eff * RED_QUALITY_MULTIPLIER.get(tier, 1.0)))

        tot_h_cells += h_cells
        tot_h_value += h_value
        tot_s_value += s_value
        print(f"  {seat}  score={sess.score_for(seat)}")
        print(
            f"      harvested : {h_cells} cells "
            f"({100 * h_cells / max(1, gen_cells):.1f}% of cells) · "
            f"purity {h_purity} ({100 * h_purity / max(1, gen_purity):.1f}%) · "
            f"value {h_value} ({100 * h_value / max(1, gen_value):.1f}%)"
        )
        print(
            f"      shipped   : value {s_value} "
            f"({100 * s_value / max(1, gen_value):.1f}% of map value)"
        )

    print()
    print("WHOLE SEASON (both seats)")
    print(
        f"  cells mined     : {tot_h_cells}/{gen_cells} = "
        f"{100 * tot_h_cells / max(1, gen_cells):.1f}%"
    )
    print(
        f"  value harvested : {tot_h_value}/{gen_value} = "
        f"{100 * tot_h_value / max(1, gen_value):.1f}%"
    )
    print(
        f"  value shipped   : {tot_s_value}/{gen_value} = "
        f"{100 * tot_s_value / max(1, gen_value):.1f}%"
    )


if __name__ == "__main__":
    main(sys.argv[1])
