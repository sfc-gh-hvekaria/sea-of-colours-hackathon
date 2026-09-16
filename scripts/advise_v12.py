"""Read-only ADVISOR for tabula_v12 — "what would v12 do here, and why?".

You play a seat yourself (in the live web UI, as a ``human`` seat) and, at any
NIGHT-planning state, point this at the same session to see the v12 harness's
reasoning for that exact board — WITHOUT it touching the game. It runs the full
THINK -> PLAN pipeline via ``harness.run(..., submit=False)`` so nothing is
submitted and no turn memory/snapshot is written; the seat stays yours.

Workflow
--------
1. Start the game server and create a game with your seat as ``human``.
2. Play a turn up to the NIGHT-planning decision (before you submit moves).
3. In another terminal, run this against the session + your seat and read the
   card it prints. Paste the card into the chat and we diagnose together.

Usage
-----
    # find your session id
    SOC_BACKEND=snowflake python scripts/advise_v12.py --list

    # get v12's reasoning for the current night state of seat p1
    SOC_BACKEND=snowflake python scripts/advise_v12.py --session <SID> --seat p1

    # also print the option menu, resolved geometry + packed moves
    python scripts/advise_v12.py --session <SID> --seat p1 --full

    # write the card to a file (handy for pasting) and/or dump raw JSON
    python scripts/advise_v12.py --session <SID> --seat p1 --out card.txt --json

Notes
-----
* The thinker is a live Cortex (LLM) call, so this needs Snowflake/Cortex
  access — the same dependency as playing a cortex seat.
* Covers NIGHT planning (where the THINK/PLAN reasoning lives). ORBIT turns are
  skipped (they'd submit); advance to night to inspect the thinker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import card as card_mod

_AGENT = "SOC_RED_REAPER_TABULA_V12"
_RULE = "=" * 72
_SUBRULE = "-" * 72

# The card renderer lives in the harness package so that the cards a SEASON
# writes as it plays (SOC_CARD_DUMP_DIR) and the cards this advisor renders on
# demand are the same artefact, readable and diffable side by side.
_render_card = card_mod.render


def _list_sessions(store: Any) -> int:
    from sea_of_colours.snowpark import engine as soc_engine

    payload = soc_engine.list_sessions(store)
    rows = payload.get("sessions") or []
    if not rows:
        print("no sessions found", flush=True)
        return 0
    print(f"{'SESSION_ID':<40} {'DAY':>3} {'PHASE':<10} SEASON", flush=True)
    print(_SUBRULE, flush=True)
    for r in rows:
        print(
            f"{str(r.get('session_id','')):<40} "
            f"{str(r.get('day','?')):>3} "
            f"{str(r.get('phase','?')):<10} "
            f"{r.get('season_name') or ''}",
            flush=True,
        )
    return 0


def _pick_seat(status: Dict[str, Any], seat_arg: Optional[str]) -> Optional[str]:
    players = list(status.get("players") or [])
    agents = dict(status.get("agents") or {})
    if seat_arg:
        return seat_arg if seat_arg in players else None
    # Prefer the (first) human seat — that's the one you're playing.
    for p in players:
        if str(agents.get(p, "")).lower() == "human":
            return p
    return players[0] if players else None


def _advise(args: argparse.Namespace) -> int:
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import engine as soc_engine
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import harness as v12

    store = soc_backend.get_store()  # attach; NEVER reset (that wipes state)

    if args.list:
        return _list_sessions(store)

    if not args.session:
        print("error: --session <id> is required (or use --list)", flush=True)
        return 2

    status = soc_engine.get_session_status(store, args.session)
    if not status.get("players"):
        print(f"error: session {args.session} not found", flush=True)
        return 2

    seat = _pick_seat(status, args.seat)
    if not seat:
        print(f"error: could not resolve seat (players={status.get('players')})",
              flush=True)
        return 2

    phase = str(status.get("phase") or "").lower()
    if phase == "orbit":
        print(
            f"seat {seat} is in ORBIT phase — the advisor covers NIGHT planning "
            "reasoning. Advance to night and re-run to inspect the thinker.",
            flush=True,
        )
        return 0
    if status.get("is_season_complete"):
        print("season is complete — nothing to advise.", flush=True)
        return 0

    view = soc_engine.get_view(store, args.session, seat)
    agent_view = view.get("agent_view") or {}

    print(f"running v12 read-only advisor for {seat} @ {args.session} ...",
          flush=True)
    res = v12.run(
        store=store, session_id=args.session, player=seat, view=view,
        submit=False,
    )
    if res.get("submitted_policy"):
        # Defensive: should never happen with submit=False.
        print("WARNING: advisor reported submitted_policy=True — aborting print "
              "so we don't mislead you.", flush=True)
        return 1

    card = _render_card(
        session_id=args.session, seat=seat, status=status,
        agent_view=agent_view, res=res, full=args.full,
        show_prompt=args.prompt,
    )
    print("\n" + card, flush=True)

    if args.out:
        Path(args.out).write_text(card + "\n", encoding="utf-8")
        print(f"\n(card written to {args.out})", flush=True)
    if args.json:
        blob = {"session_id": args.session, "seat": seat, "moves": res.get("moves"),
                "extras": res.get("extras")}
        out_json = (args.out + ".json") if args.out else "advise_v12.json"
        Path(out_json).write_text(json.dumps(blob, indent=2, default=str),
                                  encoding="utf-8")
        print(f"(raw trace written to {out_json})", flush=True)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="advise_v12", description=__doc__)
    p.add_argument("--session", help="session id to advise (see --list)")
    p.add_argument("--seat", help="seat to advise (default: your human seat, else p1)")
    p.add_argument("--list", action="store_true", help="list sessions and exit")
    p.add_argument("--full", action="store_true",
                   help="also print the packager log + packed moves")
    p.add_argument("--prompt", action="store_true",
                   help="print the EXACT prompt(s) the model receives, verbatim")
    p.add_argument("--out", help="write the card to this file too")
    p.add_argument("--json", action="store_true",
                   help="also dump the raw trace to <out>.json / advise_v12.json")
    args = p.parse_args(argv)

    os.environ.setdefault("SOC_BACKEND", "snowflake")
    os.environ.setdefault("SOC_CORTEX_AGENT", _AGENT)
    return _advise(args)


if __name__ == "__main__":
    sys.exit(main())
