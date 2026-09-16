"""Rebuild V12's OPTION MENU for a seat, headless, with no LLM call.

Answers one question: on this exact board, does the deterministic half of the
harness produce chain options for the seat's spare harvesters, or does the menu
collapse? Everything here is the same code the live turn runs, so a difference
between this and the live card is a percept difference, not a model difference.

FREEZE a board once, then iterate on a fix against it offline (no Snowflake, no
model, instant):

    SOC_BACKEND=snowflake python scripts/_v12_menu_probe.py \
        --session 7830d381bd4246f9928dbf4de9d6ff7f --seat p1 \
        --save reports/frozen/caelum_d6_p1.json

    python scripts/_v12_menu_probe.py --load reports/frozen/caelum_d6_p1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import (  # noqa: E402
    agency,
    chain_filter,
    option_economics,
    packager,
    seam_control,
)
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import (  # noqa: E402
    heuristic_chains,
)
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.probe_hints import (  # noqa: E402
    _orbit_harvester_ids,
)


def _cell(c: Any) -> str:
    try:
        return f"({int(c[0])},{int(c[1])})"
    except Exception:
        return str(c)


def _chain_cells(h: Mapping[str, Any]) -> set:
    out = {
        (int(c[0]), int(c[1]))
        for c in (h.get("cells") or [])
        if isinstance(c, (list, tuple)) and len(c) == 2
    }
    d = h.get("drop_at")
    if isinstance(d, (list, tuple)) and len(d) == 2:
        out.add((int(d[0]), int(d[1])))
    return out


def load_view(args: argparse.Namespace) -> Mapping[str, Any]:
    if args.load:
        return json.loads(Path(args.load).read_text())
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import engine as soc_engine

    store = soc_backend.store_for_session(args.session)
    status = soc_engine.get_session_status(store, args.session)
    print(f"live: {status.get('season_name')} day={status.get('day')} "
          f"phase={status.get('phase')}")
    av = soc_engine.get_view(store, args.session, args.seat).get("agent_view") or {}
    if args.save:
        p = Path(args.save)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(av, indent=1, default=str))
        print(f"frozen -> {p}  ({p.stat().st_size:,} bytes)")
    return av


def report(av: Mapping[str, Any]) -> None:
    hud, meta = av.get("hud") or {}, av.get("meta") or {}
    print(f"\nday: top-level={av.get('day')!r} hud={hud.get('day')!r} "
          f"meta={meta.get('day')!r}")

    for r in (av.get("redsign") or []):
        keys = {k: r.get(k) for k in
                ("id", "day", "hour", "found_day", "day_found", "live", "mine")}
        print(f"redsign: {keys}  smear={len(r.get('cells') or [])} cells")

    age = option_economics.sign_age_nights(av)
    fresh = option_economics._SIGN_FRESH_NIGHTS
    print(f"sign_age_nights -> {age!r}   (_SIGN_FRESH_NIGHTS={fresh}; "
          f"eases when age > {fresh})")

    footprint = option_economics.redsign_footprint(av)
    if footprint:
        xs, ys = [c[0] for c in footprint], [c[1] for c in footprint]
        print(f"footprint: {len(footprint)} cells  "
              f"bbox x {min(xs)}..{max(xs)} y {min(ys)}..{max(ys)}")

    raw = heuristic_chains.top_chain_hints(av, max_chains=8)
    kept = chain_filter.dedupe_and_floor(raw)[:5]
    print(f"\nchains: {len(raw)} raw -> {len(kept)} after value/dedupe filter")

    survived, suppressed = agency._chains_off_the_seam(kept, av, True)
    ids = {id(h) for h in survived}
    print(f"_chains_off_the_seam(has_seam=True): "
          f"{len(kept)} -> {len(survived)} kept, {len(suppressed)} suppressed")
    for h in kept:
        cells = _chain_cells(h)
        hit = cells & footprint
        share = len(hit) / max(1, len(cells))
        mark = "KEPT      " if id(h) in ids else "SUPPRESSED"
        print(f"   {mark} {_cell(h.get('drop_at'))} "
              f"overlap {len(hit)}/{len(cells)} ({share:.0%})  "
              f"purities={h.get('purities')}")

    harvesters = len(_orbit_harvester_ids(av))
    stock = packager._probe_stock(av)
    print(f"\nfleet: {harvesters} harvester(s), {stock} probe(s) in stock")

    seams = seam_control.build_seam_menu(av, []) or []
    reg = agency.build_registry(
        agent_view=av,
        seam_patterns=seams,
        chain_hints=kept,
        harvesters_alive=harvesters,
    )
    cap = agency._deploy_capacity(reg, av)
    print(f"\nMENU: {len(reg)} option(s); deploy capacity {cap} "
          f"for {harvesters} harvester(s)"
          + ("  <-- SHORTFALL" if cap < harvesters else "  (fleet covered)"))
    for oid, o in reg.items():
        hd = packager._harvester_demand(o)
        pd = packager._probe_demand(o)
        flag = ""
        if hd > 0:
            flag = "  UNAFFORDABLE" if pd > stock else "  <= playable"
        print(f"   [{oid:<14}] kind={getattr(o, 'kind', '?'):<9} "
              f"harv={hd} probe={pd}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser("_v12_menu_probe")
    ap.add_argument("--session")
    ap.add_argument("--seat", default="p1")
    ap.add_argument("--save")
    ap.add_argument("--load")
    args = ap.parse_args()
    if not args.session and not args.load:
        ap.error("need --session (live) or --load (frozen)")
    report(load_view(args))


if __name__ == "__main__":
    main()
