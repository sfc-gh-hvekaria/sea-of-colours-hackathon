"""What each frozen scenario looked like FROM THE SEAT.

Written after a canonical was found to name a cell the seat could not see. A
canonical has to be authorable from the agent's own view or it is not a standard
the agent can be held to — so before writing one, read this.

    SOC_BACKEND=snowflake python scripts/scenario_facts.py
    SOC_BACKEND=snowflake python scripts/scenario_facts.py --only blind_grab_rival_seam

No model calls: this reads the snapshot and prints. Seconds, not minutes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SUITE = _REPO_ROOT / "reports" / "turn_suite" / "suite.json"
_OUT = _REPO_ROOT / "reports" / "scenarios" / "facts"

Cell = Tuple[int, int]


def _xy(v: Any) -> Optional[Cell]:
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        try:
            return (int(v[0]), int(v[1]))
        except (TypeError, ValueError):
            return None
    return None


def _live_index(av: Mapping[str, Any]) -> Dict[Cell, Tuple[str, int]]:
    out: Dict[Cell, Tuple[str, int]] = {}
    for r in ((av.get("world") or {}).get("live") or []):
        c = _xy((r.get("x"), r.get("y")))
        if c is not None:
            out[c] = (str(r.get("tile") or ""), int(r.get("purity") or 0))
    return out


def _rival_probes(av: Mapping[str, Any]) -> List[Tuple[Cell, str, Any]]:
    """Rival probe cells the seat knows about, with owner and when seen."""
    seen: Dict[Cell, Tuple[str, Any]] = {}
    for r in ((av.get("competitor_intel") or {}).get("new_this_day") or []):
        if str(r.get("kind")) == "enemy_probe_launch":
            c = _xy(r.get("at"))
            if c is not None:
                seen[c] = (str(r.get("owner") or "?"), r.get("day_seen"))
    for e in ((av.get("entities") or {}).get("echoes") or []):
        if str(e.get("type")) == "probe":
            c = _xy(e.get("last_seen_pos"))
            if c is not None and c not in seen:
                seen[c] = (str(e.get("id") or "?"), "echo")
    return [(c, o, d) for c, (o, d) in sorted(seen.items())]


def _my_probes(av: Mapping[str, Any]) -> List[Cell]:
    out = []
    for e in ((av.get("entities") or {}).get("mine") or []):
        if str(e.get("type")) == "probe":
            c = _xy(e.get("pos"))
            if c is not None:
                out.append(c)
    return sorted(out)


def _harvesters(av: Mapping[str, Any]) -> Tuple[int, int]:
    """(alive in orbit, destroyed) — the fleet the seat can actually field."""
    orbit = destroyed = 0
    for a in (av.get("my_assets") or []):
        if str(a.get("kind")) != "harvester":
            continue
        if ((a.get("lifetime") or {}).get("destroyed_on_day")) is not None:
            destroyed += 1
        elif str(a.get("state")) == "orbit":
            orbit += 1
    return orbit, destroyed


def _redsigns(av: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for rs in (av.get("redsign") or []):
        cells = [
            (int(c[0]), int(c[1]), float(c[2]))
            for c in (rs.get("cells") or []) if isinstance(c, (list, tuple)) and len(c) >= 3
        ]
        cells.sort(key=lambda t: t[2], reverse=True)
        out.append({
            "mine": bool(rs.get("mine")),
            "live": bool(rs.get("live")),
            "center": rs.get("center"),
            "day": rs.get("day"),
            "hour": rs.get("hour"),
            "spent_by": rs.get("spent_by") or "",
            "cells": cells,
        })
    return out


def _fmt(c: Sequence[Any]) -> str:
    return f"({int(c[0])},{int(c[1])})"


def _report(turn: Mapping[str, Any], av: Mapping[str, Any]) -> str:
    live = _live_index(av)
    hud = av.get("hud") or {}
    orbit_h, dead_h = _harvesters(av)
    stock = int((av.get("orbit") or {}).get("probe_stock") or 0)
    rivals = _rival_probes(av)
    mine = _my_probes(av)

    L: List[str] = [
        f"# {turn.get('id')} — what the seat could see",
        "",
        f"- snapshot `{turn.get('snapshot')}` · seat **{turn.get('seat')}** · "
        f"day {hud.get('day')} of {hud.get('season_day_cap')}",
        f"- score {hud.get('score')}  ·  all seats {hud.get('scores')}",
        f"- **{orbit_h} harvester(s) in orbit**, {dead_h} destroyed  ·  "
        f"**{stock} probe(s) in stock**",
        f"- live vision: {len(live)} cell(s)  ·  my probes {mine or 'none'}",
    ]
    if rivals:
        L.append("- rival probes known: " + ", ".join(
            f"{_fmt(c)} [{o}, seen {d}]" for c, o, d in rivals))
    else:
        L.append("- rival probes known: none")

    # RED the seat can actually act on, split by whether it is LIT right now.
    reds = [r for r in (av.get("red_tiles") or []) if _xy((r.get("x"), r.get("y")))]
    lit = [r for r in reds if _xy((r.get("x"), r.get("y"))) in live]
    echo = [r for r in reds if _xy((r.get("x"), r.get("y"))) not in live]
    L += ["", "## RED", f"- {len(lit)} in LIVE vision, {len(echo)} from memory/echo"]
    for label, rows in (("LIVE", lit), ("ECHO", echo)):
        pures = [r for r in rows if int(r.get("purity") or 0) >= 255]
        best = sorted(rows, key=lambda r: int(r.get("purity") or 0), reverse=True)[:8]
        if not rows:
            continue
        L.append(f"- {label} pures: " + (
            ", ".join(_fmt((r["x"], r["y"])) for r in pures) if pures else "none"))
        L.append(f"- {label} richest: " + ", ".join(
            f"{_fmt((r['x'], r['y']))} {r.get('tier')} {r.get('purity')}" for r in best))

    for i, rs in enumerate(_redsigns(av)):
        who = "MINE" if rs["mine"] else "RIVAL"
        L += ["", f"## REDSIGN {i} — {who}"]
        L.append(f"- centre {rs['center']} · raised day {rs['day']} h{rs['hour']} · "
                 f"live={rs['live']} · spent_by='{rs['spent_by']}'")
        top = rs["cells"][:10]
        L.append("- hottest cells (the ONLY guide to where the pure is):")
        for x, y, w in top:
            in_los = "lit" if (x, y) in live else "fog"
            L.append(f"    {_fmt((x, y))}  {w:.3f}  [{in_los}]")
        # The question every canonical for a seam turn has to answer first.
        foot = {(x, y) for x, y, _ in rs["cells"]}
        n_lit = len(foot & set(live))
        L.append(f"- **{n_lit} of {len(foot)} smear cells are in live vision** — "
                 + ("the seat is working blind here; a canonical CANNOT name the "
                    "pure's cell, only a drop and a comb direction."
                    if n_lit == 0 else
                    "some of the smear is lit; a canonical may name lit cells."))

    L += ["", "## The scenario as written", "",
          f"**Question.** {turn.get('question') or '(none)'}", ""]
    if turn.get("canonical"):
        L += [f"**Canonical on file.** {turn['canonical']}", ""]
    if turn.get("expect"):
        L += ["**Scored on.** ```" + json.dumps(turn["expect"]) + "```", ""]
    return "\n".join(L) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser("scenario_facts")
    p.add_argument("--suite", default=str(_SUITE))
    p.add_argument("--only", nargs="*", default=[])
    p.add_argument("--outdir", default=str(_OUT))
    a = p.parse_args(argv)

    from sea_of_colours.snowpark import backend as soc_backend, engine as soc_engine

    turns = json.loads(Path(a.suite).read_text(encoding="utf-8"))
    turns = turns.get("turns") if isinstance(turns, dict) else turns
    if a.only:
        turns = [t for t in turns if t.get("id") in set(a.only)]

    store = soc_backend.get_store()
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    for t in turns:
        snap, seat = t.get("snapshot"), t.get("seat")
        if not store.load_session(snap):
            print(f"  {t.get('id')}: no such snapshot {snap}", file=sys.stderr)
            continue
        av = (soc_engine.get_view(store, snap, seat) or {}).get("agent_view") or {}
        text = _report(t, av)
        (outdir / f"{t.get('id')}.md").write_text(text, encoding="utf-8")
        print(text)
        print("-" * 72)
    print(f"written to {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
