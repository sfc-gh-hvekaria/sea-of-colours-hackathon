#!/usr/bin/env python3
"""Rename PAST seasons to self-describing names (dry-run by default).

The engine auto-names seasons ``Latin_Noun`` (``Tempus_Falcon``) which are
impossible to tell apart in the replay list. This rewrites those auto-names to
``<matchup>_s<seed>_d<days>_<id6>`` derived from each session's STORED agents /
seed / day-cap, updating both the ``season_name`` column and the embedded state.

Safety:
  * DRY RUN by default — prints old -> new; writes nothing until ``--apply``.
  * Only touches auto-generated ``Latin_Noun`` names (won't clobber names you
    set deliberately) unless you pass explicit ``--session`` / ``--all``.
  * Skips ``eval:`` sessions.
  * Appends the 6-char session id so same-matchup runs stay distinct + traceable.

Usage::

    PYTHONPATH=. python scripts/rename_seasons.py            # dry run, auto-named
    PYTHONPATH=. python scripts/rename_seasons.py --apply     # do it
    PYTHONPATH=. python scripts/rename_seasons.py --session 1869cb... --name FOO --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Auto-generated evocative names look like ``Tempus_Falcon`` (Capitalised pair).
_AUTONAME_RE = re.compile(r"^[A-Z][a-z]+_[A-Z][a-z]+$")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rename_seasons")
    p.add_argument("--apply", action="store_true",
                   help="Actually write the renames (default is a dry run).")
    p.add_argument("--all", action="store_true",
                   help="Consider ALL sessions, not just auto-named ones.")
    p.add_argument("--session", default=None,
                   help="Rename just this session_id.")
    p.add_argument("--name", default=None,
                   help="Explicit new name for --session (else auto-derived).")
    return p


def _json_state(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("json_state")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _derive_name(row: Dict[str, Any], session_id: str) -> Optional[str]:
    from _season_naming import make_season_name

    state = _json_state(row)
    agents = state.get("agents") or {}
    players = state.get("players") or sorted(agents.keys())
    labels = [str(agents.get(s, s)) for s in players] if agents else []
    if not labels:
        return None
    seed = row.get("seed")
    if seed is None:
        seed = state.get("seed", 0)
    days = state.get("season_day_cap") or row.get("day")
    base = make_season_name(labels, int(seed or 0), int(days) if days else None,
                            stamp=False)
    return f"{base}_{session_id[:6]}"


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    os.environ.setdefault("SOC_BACKEND", "snowflake")
    if os.environ["SOC_BACKEND"].lower() != "snowflake":
        print("preflight: set SOC_BACKEND=snowflake", file=sys.stderr)
        return 2
    from sea_of_colours.snowpark import backend as soc_backend
    store = soc_backend.get_store()

    # Build the candidate list.
    if args.session:
        candidates = [args.session]
    else:
        candidates = [
            str(s.get("session_id"))
            for s in store.list_sessions()
            if s.get("session_id")
        ]

    planned: List[tuple] = []  # (session_id, old, new)
    for sid in candidates:
        row = store.load_session(sid)
        if not row:
            print(f"  skip {sid[:8]}: not found", file=sys.stderr)
            continue
        old = str(row.get("season_name") or "")
        if old.lower().startswith("eval:"):
            continue
        if args.session and args.name:
            new = args.name.strip()
        else:
            if not args.all and not args.session and not _AUTONAME_RE.match(old):
                continue  # leave deliberately-named seasons alone
            new = _derive_name(row, sid)
        if not new or new == old:
            continue
        planned.append((sid, old, new))

    if not planned:
        print("nothing to rename.")
        return 0

    print(f"{'session':<10} {'old name':<24} -> new name")
    for sid, old, new in planned:
        print(f"{sid[:8]:<10} {old:<24} -> {new}")
    print(f"\n{len(planned)} season(s) {'RENAMED' if args.apply else 'to rename (dry run)'}.")

    if not args.apply:
        print("re-run with --apply to write these.")
        return 0

    for sid, _old, new in planned:
        row = dict(store.load_session(sid) or {})
        if not row:
            continue
        state = _json_state(row)
        state["season_name"] = new
        store.save_session({
            "session_id": sid,
            "season_name": new,
            "width": int(row.get("width") or state.get("width") or 0),
            "height": int(row.get("height") or state.get("height") or 0),
            "seed": int(row.get("seed") or state.get("seed") or 0),
            "day": int(row.get("day") or state.get("day") or 0),
            "phase": str(row.get("phase") or state.get("phase") or ""),
            "json_state": state,
        })
        print(f"  renamed {sid[:8]} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
