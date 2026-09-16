"""Replay one frozen night, any number of times, with any harness.

Each run clones the snapshot into a throwaway session, plays a single planning
turn there, and records what the agent picked and what the compiler emitted.
The snapshot is never touched, so the same night can be re-run before and after
a fix and the two distributions compared.

    # what does v12 do on this night, six times?
    SOC_BACKEND=snowflake python scripts/replay_turn.py \
        --snapshot SNAP_e01903bd_d4_p1 --runs 6

    # same board, different agent
    SOC_BACKEND=snowflake python scripts/replay_turn.py \
        --snapshot SNAP_e01903bd_d4_p1 --harness tabula_v13 --runs 6

Because the model is stochastic, judge a change by how the spread of picks
moves across runs, not by a single run.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("replay_turn")
    p.add_argument("--snapshot", required=True, help="snapshot session id")
    p.add_argument("--seat", default=None,
                   help="seat to play (default: parsed from the snapshot id)")
    p.add_argument("--harness", default=None,
                   help="agent label to pin, e.g. tabula_v12 (default: the "
                        "binding already on the board)")
    p.add_argument("--runs", type=int, default=6)
    p.add_argument("--outdir", default="reports/replay")
    p.add_argument("--keep", action="store_true",
                   help="keep the throwaway clone sessions (default: delete)")
    return p


def _seat_from(snapshot_id: str, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    tail = snapshot_id.rsplit("_", 1)[-1]
    return tail if tail.startswith("p") else "p1"


def _plan_of(store: Any, sid: str, seat: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for r in store.list_agent_invocations(sid):
        if str(r.get("player") or "") != seat:
            continue
        aid = str(r.get("agent_id") or "").upper()
        resp = str(r.get("response_text") or "")
        if not resp:
            continue
        if aid.endswith("_THINK"):
            out["think"] = resp
        elif aid.endswith("_THINKER"):
            out["plan"] = resp
    return out


def _compiler_of(store: Any, sid: str, seat: str) -> str:
    for entry in store.list_log(sid, limit=500):
        text = str(entry.get("text") or "")
        if f"({seat})" in text and "exec=" in text:
            return text
    return ""


def _moves_of(store: Any, sid: str, seat: str, day: int) -> List[Dict[str, Any]]:
    """The compiled wire moves for the turn.

    The only unambiguous record of what the agent actually did. Option
    labels don't say where a chain walked, and the plan's prose names cells
    the packager may never have emitted, so any question of the form "did it
    reach that cell" has to be answered here.
    """
    try:
        return list((store.list_policies(sid, day) or {}).get(seat) or [])
    except Exception:
        return []


def _render_moves(moves: Sequence[Dict[str, Any]]) -> str:
    if not moves:
        return "(none)"
    out = []
    for m in moves:
        cell = m.get("at") or m.get("to")
        unit = str(m.get("unit") or "")
        out.append(
            f"{m.get('a')}"
            + (f" ({cell[0]},{cell[1]})" if isinstance(cell, (list, tuple)) else "")
            + (f"  {unit}" if unit else "")
        )
    return "\n".join(out)


def _ids(plan_json: str) -> str:
    import re
    m = re.search(r'"plan"\s*:\s*\[(.*?)\]', plan_json, re.S)
    return m.group(1).replace('"', "").replace(" ", "") if m else "(none)"


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import snapshot as soc_snapshot
    from sea_of_colours.orchestrator_2.runtime import run_agent_turn

    store = soc_backend.get_store()
    seat = _seat_from(args.snapshot, args.seat)
    outdir = _REPO_ROOT / args.outdir / args.snapshot
    outdir.mkdir(parents=True, exist_ok=True)

    snap_row = store.load_session(args.snapshot)
    if not snap_row:
        print(f"no such snapshot: {args.snapshot}", file=sys.stderr)
        return 1
    # The clone advances once the night resolves, so read the day off the
    # frozen snapshot — that is the day the policy was filed under.
    snap_day = int(snap_row.get("day") or 0)

    picks: collections.Counter = collections.Counter()
    rows: List[Dict[str, Any]] = []

    for i in range(1, args.runs + 1):
        rid = soc_snapshot.clone_for_run(store, args.snapshot)
        started = time.time()
        try:
            run_agent_turn(store, rid, seat, agent_label=args.harness)
        except Exception as exc:
            print(f"  run {i}: turn failed — {exc}")
            continue
        elapsed = int((time.time() - started) * 1000)
        got = _plan_of(store, rid, seat)
        plan_ids = _ids(got.get("plan", ""))
        compiler = _compiler_of(store, rid, seat)
        moves = _moves_of(store, rid, seat, snap_day)
        picks[plan_ids] += 1
        rows.append({"run": i, "clone": rid, "plan": plan_ids,
                     "ms": elapsed, "compiler": compiler, "moves": moves})
        (outdir / f"run{i:02d}.md").write_text("\n".join([
            f"# {args.snapshot} — run {i} — {args.harness or '(board binding)'}",
            "", f"- clone `{rid}`  ·  {elapsed} ms", "",
            "## PLAN", "```json", got.get("plan", "(none)"), "```", "",
            "## THINK", "```", got.get("think", "(none)"), "```", "",
            "## Compiler", "```", compiler or "(none)", "```", "",
            "## Moves", "```", _render_moves(moves), "```",
        ]), encoding="utf-8")
        print(f"  run {i}: {plan_ids}  ({elapsed} ms)")
        if not args.keep:
            try:
                store.delete_session(rid)
            except Exception:
                pass

    (outdir / "runs.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\n{args.snapshot} · {args.harness or '(board binding)'} · "
          f"{len(rows)}/{args.runs} runs")
    for plan, n in picks.most_common():
        print(f"  {n}/{len(rows)}  {plan}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
