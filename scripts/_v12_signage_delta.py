#!/usr/bin/env python3
"""Before/after on the sign-age key fix, against a frozen agent_view.

The fix changes ``nights_held`` from a constant 0 (the two key mismatches made
every reader miss) to the real age, and ``_pure_survival`` discounts on that —
so it moves the blind-attack price on EVERY night of every game, not just the
menu. This prints the delta rather than shipping it blind.

    python scripts/_v12_signage_delta.py reports/frozen/caelum_d6_p1_redsign.json
"""
from __future__ import annotations

import json
import sys

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import option_economics as oe


def _blind_over_smear(view: dict) -> dict | None:
    """Price a comb over the whole of the first smear, fog or not."""
    regions = oe._redsign_smear_regions(view)
    if not regions:
        return None
    cells = sorted(regions[0])
    return oe.blind_estimate(cells, view)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "reports/frozen/caelum_d6_p1_redsign.json"
    view = json.load(open(path))

    print(f"board: {path}")
    print(f"  day        : hud={(view.get('hud') or {}).get('day')} "
          f"meta={(view.get('meta') or {}).get('day')} "
          f"top={view.get('day')}")
    for row in (view.get("redsign") or []):
        print(f"  sign {row.get('id')}: day={row.get('day')} "
              f"mine={bool(row.get('mine'))} cells={len(row.get('cells') or [])}")

    print(f"\n  view_day()        -> {oe.view_day(view)}")
    print(f"  sign_age_nights() -> {oe.sign_age_nights(view)}")

    metas = oe._redsign_smear_meta(view)
    print(f"\n  nights_held  AFTER : {[m['nights_held'] for m in metas]}")
    print(f"  nights_held  BEFORE: {[0 for _ in metas]}   (every reader missed)")

    print("\n  _pure_survival (odds the broadcast pure is still there):")
    for m in metas:
        after = oe._pure_survival(m)
        before = oe._pure_survival({**m, "nights_held": 0})
        print(f"    mine={bool(m['mine'])} nights={m['nights_held']}: "
              f"{before:.3f} -> {after:.3f}")

    # The "before" is exactly what an undated sign still produces, so strip the
    # date rather than reinstate the old broken readers.
    stale = json.loads(json.dumps(view))
    for row in (stale.get("redsign") or []):
        for k in ("day", "found_day", "day_found"):
            row.pop(k, None)

    after, before = _blind_over_smear(view), _blind_over_smear(stale)
    if after and before:
        print("\n  blind_estimate over the full smear   BEFORE -> AFTER:")
        for k in sorted(set(after) | set(before)):
            b, a = before.get(k), after.get(k)
            flag = "   <<<" if b != a else ""
            print(f"    {k:20} {b} -> {a}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
